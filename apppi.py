import logging
import os
from typing import Any, Mapping, Optional, Sequence

from dataviz_core.models.shared_enums import Status
from celery import Celery, Task
from celery.schedules import crontab
from celery.signals import eventlet_pool_started, task_postrun, worker_process_init
from sg_cacert_file import load_sg_certs
from sg_metrology.extensions import celery as metrology
from sg_opentracing.sg_opentracing import get_current_span
from sg_opentracing.utils import celery_run_set_operation_name

from dataviz_async import core, tracing
from dataviz_async.core import get_core, get_provider

logger = logging.getLogger(__name__)

load_sg_certs()

if not os.path.exists("logs"):
    os.makedirs("logs")

app = Celery(__name__, broker=None)
app.config_from_object("dataviz_async.config")
metrology.init_app(app)
core.init_app(app)
eventlet_pool_started.connect(tracing.set_opentracing)
worker_process_init.connect(tracing.set_opentracing)

# NOTE: Lifecycle consumer is now deployed as a separate standalone service
# See dataviz_async/lifecycle_consumer_main.py and ccp_config_lifecycle.yaml
# DO NOT auto-start the consumer in the CP worker - it blocks indefinitely


@app.task(name="exec_core_function", bind=True, default_retry_delay=10)
def exec_core_function(
    self: Task,
    service: str,
    function: str,
    args: Optional[Sequence[Any]] = None,
    kwargs: Optional[Mapping[str, Any]] = None,
    max_retries: int = 5,
    final_status: bool = False,
) -> Any:
    setattr(self.request, "max_retries", max_retries)
    span = get_current_span()
    celery_run_set_operation_name(span, f"{service}.{function}")
    span.set_tag("service", service)
    span.set_tag("function", function)
    if args is None:
        args = []
    if kwargs is None:
        kwargs = {}
    core = get_core(app)
    serv = getattr(core, service)
    func = getattr(serv, function)
    try:
        res = func(*args, **kwargs)
    except Exception as e:
        logger.exception(
            (
                f"core.{service}.{function} failed. Retry in 10 seconds.."
                f" {getattr(self.request, 'retries', 0)}/{max_retries}"
            ),
            extra={
                "error_retries": getattr(self.request, "retries", 0),
                "error_max_retries": max_retries,
            },
        )
        if getattr(self.request, "retries", 0) == max_retries and final_status:
            logger.info(
                f"the function {function} raised exception with kwargs: {kwargs}",
                extra={"final_status": "FAILURE"},
            )
        raise self.retry(exc=e, max_retries=max_retries)
    else:
        if final_status:
            if getattr(res, "status", None) in [Status.ACTIVE, Status.FAILED, Status.DELETED]:
                result = "FAILURE" if res.status == Status.FAILED else "SUCCESS"
                logger.info(
                    (
                        f"the function {service}.{function} give "
                        f"the async status {result} with kwargs: {kwargs}"
                    ),
                    extra={"final_status": result},
                )


@task_postrun.connect(sender=exec_core_function)
def task_postrun_close_session(**kwargs: Any) -> None:
    logger.info("closing sqlalchemy sessions")
    get_provider(app).close_sessions()


@app.task(name="update_health_cache")
def update_health_cache() -> None:
    logger.info("updating health check cached result: BEGIN")
    get_core(app).health.update_health_cache()
    logger.info("updating health check cached result: DONE")


@app.task(name="backup_status_update")
def backup_status_update() -> None:
    logger.info("updating backup status result: BEGIN")
    get_core(app).backup.backup_status_update()
    logger.info("updating backup status result: DONE")


@app.task(name="check_certificate_expiration")
def check_certificate_expiration() -> None:
    logger.info("Checking expiration certificates: BEGIN")
    get_core(app).certificate.check_certificate_expiration_and_renewal_certificate()
    logger.info("Certificate renewal: DONE")


@app.task(name="account_reconciliation")
def account_reconciliation() -> None:
    """
    Daily reconciliation task to sync account status with Accounts Team.

    Catches any Event Bus events that were missed or failed.
    Runs daily at 2 AM UTC.

    NOTE: Graceful fallback if API client not configured yet.
    """
    logger.info("Account reconciliation: BEGIN")

    try:
        core_instance = get_core(app)

        # Check if reconciliation service is available
        if not hasattr(core_instance, "account_reconciliation"):
            logger.warning(
                "Account reconciliation service not wired in core.py yet. "
                "Skipping reconciliation."
            )
            return

        reconciliation_service = core_instance.account_reconciliation

        # Check if Accounts Team API client is configured
        if not reconciliation_service._accounts_api_client:
            logger.warning(
                "Accounts Team API client not configured. "
                "Skipping reconciliation. Configure ACCOUNTS_TEAM_API_URL to enable."
            )
            return

        summary = reconciliation_service.run()

        logger.info(
            f"Account reconciliation completed: "
            f"checked={summary['accounts_checked']}, "
            f"updated={summary['accounts_updated']}, "
            f"deleted={summary['accounts_deleted']}, "
            f"errors={len(summary['errors'])}"
        )

        if summary["errors"]:
            logger.error(f"Reconciliation errors: {summary['errors']}")

    except Exception as e:
        logger.error(f"Account reconciliation failed: {str(e)}", exc_info=True)

    logger.info("Account reconciliation: DONE")


LIFECYCLE_QUEUE_ALIAS = "dataviz-lifecycle-consumer"
LIFECYCLE_TOPIC = "lifecycle"
LIFECYCLE_ROUTING_KEYS = [
    "LifecycleEvent.ResourceDisabled",
    "LifecycleEvent.ResourceActive",
    "LifecycleEvent.ResourceDeleting",
    "LifecycleEvent.ResourceDeleted",
    "LifecycleEvent.ResourceCreating",   # CCP new.json format
    "LifecycleEvent.ResourceReady",      # CCP new.json format
]


def _parse_eventbus_regions(configured_regions: str) -> list[str]:
    """Parse a comma-separated region list while preserving order."""
    regions: list[str] = []
    for raw_region in configured_regions.split(","):
        region = raw_region.strip()
        if region and region not in regions:
            regions.append(region)
    return regions


def _get_eventbus_regions() -> list[str]:
    """Return the configured Event Bus regions, preferring the explicit multi-region env var."""
    configured_regions = os.environ.get("EVENTBUS_REGIONS", "")
    if configured_regions:
        return _parse_eventbus_regions(configured_regions)

    return _parse_eventbus_regions(os.environ.get("REGION", ""))


def _validate_eventbus_credentials(username: str, password: str, regions: list[str]) -> None:
    """Validate Event Bus credentials and raise ValueError if missing."""
    if not username or not password:
        logger.error(
            "EVENTBUS_USERNAME/EVENTBUS_PASSWORD not set! Cannot start lifecycle consumer."
        )
        raise ValueError("EVENTBUS_USERNAME and EVENTBUS_PASSWORD required")

    if not regions:
        logger.error("EVENTBUS_REGIONS/REGION not set! Cannot start lifecycle consumer.")
        raise ValueError("EVENTBUS_REGIONS or REGION required")


def _create_lifecycle_consumers(
    lifecycle_service,
    eventbus_username: str,
    eventbus_password: str,
    regions: list[str],
    *,
    client_class,
    queue_class,
    consumer_class,
):
    """Create one lifecycle consumer per configured Event Bus region."""
    on_lifecycle_event = _create_lifecycle_event_handler(lifecycle_service)
    on_dead_letter = _create_dead_letter_handler()

    consumers = []
    for region in regions:
        eventbus_client = client_class(
            user=eventbus_username,
            password=eventbus_password,
            region=region,
        )

        queue = queue_class(
            client=eventbus_client,
            alias=LIFECYCLE_QUEUE_ALIAS,
            topic=LIFECYCLE_TOPIC,
            routing_key=LIFECYCLE_ROUTING_KEYS,
        )

        logger.info(f"Queue created: {queue} on topic '{LIFECYCLE_TOPIC}' (region: {region})")

        consumers.append(
            consumer_class(
                queue=queue,
                callback=on_lifecycle_event,
                dead_letter=on_dead_letter,
                auto_ack=True,
                timeout=10,
            )
        )

    return consumers


def _extract_event_data(event):
    """Extract event type and data from event object or dict."""
    event_type = getattr(event, "type", None)
    if not event_type and isinstance(event, dict):
        event_type = event.get("type", "")

    event_data = getattr(event, "data", None)
    if not event_data and isinstance(event, dict):
        event_data = event.get("data", {})
    if not isinstance(event_data, dict):
        event_data = {}

    return event_type, event_data


# Alias so eventbus_demo.py (and any other caller) can import either name.
_extract_event_fields = _extract_event_data


def _validate_lifecycle_event(event, event_type: str, event_data: dict) -> tuple[bool, str]:
    """
    Validate that a CloudEvent is a well-formed lifecycle event.

    Accepts both the CCP (new.json) format and the legacy standard format.

    Returns
    -------
    (True, "")        – event is valid and should be processed.
    (False, reason)   – event is malformed and should be rejected / dead-lettered.
    """
    # ── 1. Must have a non-empty event type ─────────────────────────────────
    if not event_type:
        return False, "missing event type"

    # ── 2. Must be a known lifecycle event type ──────────────────────────────
    known_prefix = "LifecycleEvent."
    known_suffixes = {
        "ResourceDisabled",
        "ResourceActive",
        "ResourceDeleting",
        "ResourceDeleted",
        "ResourceCreating",
        "ResourceReady",
        "ResourceModifying",
        "ResourceModified",
        "ResourceError",
    }
    if not event_type.startswith(known_prefix):
        return False, f"unknown event type prefix: {event_type!r}"
    if event_type[len(known_prefix):] not in known_suffixes:
        return False, f"unsupported lifecycle event type: {event_type!r}"

    # ── 3. Must have a data payload ──────────────────────────────────────────
    if not isinstance(event_data, dict) or not event_data:
        return False, "missing or empty data payload"

    # ── 4. CCP format: data.resource must be a dict ──────────────────────────
    resource = event_data.get("resource")
    if resource is not None and not isinstance(resource, dict):
        return False, "data.resource is not a dict"

    # ── 5. Must be able to resolve an owner account ID ───────────────────────
    owner = _extract_account_id(event, event_data)
    if not owner:
        return False, "cannot resolve owner account ID from event"

    return True, ""


def _extract_account_id(event, event_data: dict) -> Optional[str]:
    """
    Extract the owner account UUID from a CloudEvent.

    Supports both the CCP (new.json) format and the legacy standard format.

    Extraction priority
    -------------------
    1. ``data.resource.ownerId`` SRN  (CCP format)
       e.g. ``"srn:sgcp:account.cloud.socgen:account:<uuid>"``  → last ``:`` segment.
    2. Top-level ``resourceowner`` extension attribute (CCP format, plain UUID).
    3. ``data.account_id`` / ``data.resource_id``  (legacy keys inside the data blob).
    4. Top-level ``subject``  (standard format where subject == account UUID).
       *Not* used when CCP fields are present because in the CCP format
       ``subject`` holds the resource UUID, not the account UUID.
    """
    # ── 1. CCP format: data.resource.ownerId SRN ────────────────────────────
    resource = event_data.get("resource", {}) if isinstance(event_data, dict) else {}
    owner_id_srn = resource.get("ownerId", "") if isinstance(resource, dict) else ""
    if owner_id_srn:
        candidate = owner_id_srn.split(":")[-1]
        if candidate:
            logger.debug("account_id extracted from data.resource.ownerId SRN: %s", candidate)
            return candidate

    # ── 2. CCP extension attribute: top-level resourceowner (plain UUID) ────
    if isinstance(event, dict):
        resourceowner = event.get("resourceowner") or event.get("resourceOwner")
    else:
        resourceowner = getattr(event, "resourceowner", None) or getattr(
            event, "resourceOwner", None
        )
    if resourceowner:
        logger.debug("account_id extracted from resourceowner extension: %s", resourceowner)
        return resourceowner

    # ── 3. Legacy keys inside the data blob ─────────────────────────────────
    if isinstance(event_data, dict):
        legacy = event_data.get("account_id") or event_data.get("resource_id")
        if legacy:
            logger.debug("account_id extracted from event_data legacy key: %s", legacy)
            return legacy

    # ── 4. Top-level subject (standard format where subject == account UUID) ─
    if isinstance(event, dict):
        subject = event.get("subject")
    else:
        subject = getattr(event, "subject", None)
    if subject:
        logger.debug("account_id extracted from top-level subject: %s", subject)
    return subject


def _create_lifecycle_event_handler(lifecycle_service):
    """Create and return the lifecycle event callback function."""

    def on_lifecycle_event(event, message):
        try:
            event_type, event_data = _extract_event_data(event)
            account_id = _extract_account_id(event, event_data)

            if not account_id:
                logger.warning(f"Event without account_id: {event}")
                return

            logger.info(f"Processing lifecycle event: {event_type} for account {account_id}")

            result = lifecycle_service.handle_event(
                event_type=str(event_type),
                account_id=account_id,
                event_data=event_data,
            )

            if result.get("success"):
                logger.info(
                    f"Processed {event_type} for {account_id}: {result.get('message', 'OK')}"
                )
            else:
                logger.error(f"Failed {event_type} for {account_id}: {result.get('message')}")

        except Exception as e:
            logger.error(f"Error processing lifecycle event: {e}", exc_info=True)

    return on_lifecycle_event


def _create_dead_letter_handler():
    """Create and return the dead letter callback function."""

    def on_dead_letter(message):
        logger.warning(f"Dead letter received (could not deserialize): {message}")

    return on_dead_letter


@app.task(name="consume_account_lifecycle_events", bind=True)
def consume_account_lifecycle_events(self: Task) -> None:
    """
    Event Bus consumer for account lifecycle events from Accounts Team.

    ⚠️ DEPRECATED as a Celery task - use standalone deployment instead!
    This task is kept for backward compatibility but should NOT be used
    in the CP worker as it blocks indefinitely.

    NEW APPROACH:
    Deploy as a standalone service using:
    - dataviz_async/lifecycle_consumer_main.py
    - ccp_config_lifecycle.yaml

    This ensures the blocking consumer runs in its own pod with
    dedicated resources, separate from the main CP worker.

    Uses the event_bus_client library (AMQP-based) with:
    - Queue bound to topic "lifecycle"
    - routing_key to filter lifecycle events
    - Consumer thread with callback

    Events handled:
    - LifecycleEvent.ResourceDisabled: Account grace period
    - LifecycleEvent.ResourceActive: Account reactivation
    - LifecycleEvent.ResourceDeleting: Account deletion
    - LifecycleEvent.ResourceDeleted: Account deletion completed
    """
    logger.info("Starting Account Lifecycle Event Bus consumer")

    from event_bus_client import Consumer, Queue, Client

    consumers = []
    started_consumers = []

    try:
        lifecycle_service = get_core(app).account_lifecycle_consumer

        # Validate credentials
        eventbus_username = os.environ.get("EVENTBUS_USERNAME", "")
        eventbus_password = os.environ.get("EVENTBUS_PASSWORD", "")
        regions = _get_eventbus_regions()
        _validate_eventbus_credentials(eventbus_username, eventbus_password, regions)

        consumers = _create_lifecycle_consumers(
            lifecycle_service,
            eventbus_username,
            eventbus_password,
            regions,
            client_class=Client,
            queue_class=Queue,
            consumer_class=Consumer,
        )

        logger.info(
            (
                "Lifecycle consumer started. Listening for account lifecycle events "
                f"in regions: {', '.join(regions)}"
            )
        )

        if len(consumers) == 1:
            consumers[0].run()
        else:
            for consumer in consumers:
                consumer.start()
                started_consumers.append(consumer)

            for consumer in started_consumers:
                consumer.join()

    except KeyboardInterrupt:
        logger.info("Lifecycle consumer interrupted")
    except Exception as e:
        logger.error(f"Fatal error in lifecycle consumer: {e}", exc_info=True)
        raise
    finally:
        for consumer in started_consumers:
            try:
                consumer.stop()
            except Exception:
                logger.warning("Failed to stop lifecycle consumer thread cleanly", exc_info=True)
        logger.info("Lifecycle consumer stopped")


app.conf.timezone = "UTC"
app.conf.beat_schedule = {
    "update_health_cache_every_5_minutes": {
        "task": "update_health_cache",
        "schedule": crontab(minute="*/5"),
    },
    "backup_status_update_every_5_minutes": {
        "task": "backup_status_update",
        "schedule": crontab(minute="*/5"),
    },
    "check_certificate_expiration_every_day_midnight": {
        "task": "check_certificate_expiration",
        "schedule": crontab(hour=0, minute=0),
    },
    "account_reconciliation_daily": {
        "task": "account_reconciliation",
        "schedule": crontab(hour=0, minute=0),
        "options": {
            "expires": 3600,
        },
    },
}
