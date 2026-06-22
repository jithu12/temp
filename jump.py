#async/app.py

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
    Runs daily at 12 AM UTC.

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
            "Account reconciliation completed: "
            "checked=%s updated=%s deleted=%s errors=%s",
            summary["accounts_checked"],
            summary["accounts_updated"],
            summary["accounts_deleted"],
            len(summary["errors"]),
        )

        if summary["errors"]:
            logger.error("Reconciliation errors: %s", summary["errors"])

    except Exception as e:
        logger.error("Account reconciliation failed: %s", e, exc_info=True)

    logger.info("Account reconciliation: DONE")


LIFECYCLE_QUEUE_ALIAS = "dataviz-lifecycle-consumer"
LIFECYCLE_TOPIC = "lifecycle"
LIFECYCLE_ROUTING_KEYS = [
    "LifecycleEvent.ResourceDisabled",
    "LifecycleEvent.ResourceActive",
    "LifecycleEvent.ResourceDeleting",
    "LifecycleEvent.ResourceDeleted",
]

LIFECYCLE_EVENT_TYPES = set(LIFECYCLE_ROUTING_KEYS) | {
    "ResourceDisabled",
    "ResourceActive",
    "ResourceDeleting",
    "ResourceDeleted",
}

REQUIRED_CLOUDEVENT_FIELDS = (
    "specversion",
    "id",
    "time",
    "type",
    "source",
    "subject",
)


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

        logger.info("Queue created: %s on topic '%s' (region: %s)", queue, LIFECYCLE_TOPIC, region)

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


def _extract_event_fields(event) -> tuple[str, dict]:
    """
    Extract event type and inner data payload from a CloudEvent object or dict.

    Always returns (str, dict) — never (None, None) — so callers can safely
    call .get() on the data without guarding against None.
    """
    if isinstance(event, dict):
        event_type = event.get("type") or ""
        event_data = event.get("data") or {}
    else:
        event_type = getattr(event, "type", None) or ""
        event_data = getattr(event, "data", None) or {}

    if not isinstance(event_data, dict):
        logger.warning(
            "CloudEvent 'data' field is not a dict (got %s) — using empty dict.",
            type(event_data).__name__,
        )
        event_data = {}

    return event_type, event_data


def _get_event_attr(event, key: str):
    """Best-effort CloudEvent attribute extraction across dict/object and key casing."""
    key_lower = key.lower()

    if isinstance(event, dict):
        if key in event:
            return event[key]
        if key_lower in event:
            return event[key_lower]
        for k, value in event.items():
            if isinstance(k, str) and k.lower() == key_lower:
                return value
        return None

    value = getattr(event, key, None)
    if value is not None:
        return value

    value = getattr(event, key_lower, None)
    if value is not None:
        return value

    extensions = getattr(event, "extensions", None)
    if isinstance(extensions, dict):
        if key in extensions:
            return extensions[key]
        if key_lower in extensions:
            return extensions[key_lower]
        for k, ext_value in extensions.items():
            if isinstance(k, str) and k.lower() == key_lower:
                return ext_value

    return None


def _validate_lifecycle_event(event, event_type: str, event_data: dict) -> tuple[bool, str]:
    """Validate mandatory lifecycle CloudEvent envelope fields before processing."""
    missing_fields = [field for field in REQUIRED_CLOUDEVENT_FIELDS if not _get_event_attr(event, field)]
    if missing_fields:
        return False, f"missing required CloudEvent fields: {', '.join(missing_fields)}"

    specversion = str(_get_event_attr(event, "specversion") or "").strip()
    # Accept both "1.0" (canonical) and "1.0.0" (common non-canonical variant)
    # on inbound events to be tolerant of minor sender differences.
    if specversion not in ("1.0", "1.0.0"):
        return False, f"unsupported CloudEvent specversion '{specversion}'"

    if event_type not in LIFECYCLE_EVENT_TYPES:
        return False, f"unsupported lifecycle event type '{event_type}'"

    datacontenttype = str(_get_event_attr(event, "datacontenttype") or "").strip().lower()
    if datacontenttype and datacontenttype != "application/hal+json":
        logger.warning(
            "Lifecycle event has unexpected datacontenttype '%s' (expected application/hal+json)",
            datacontenttype,
        )

    if "resource" not in event_data:
        logger.warning("Lifecycle event payload missing data.resource")

    return True, ""


def _extract_account_id(event, event_data: dict) -> Optional[str]:
    """
    Extract the account ID from the full CloudEvent.

    Resolution order:
      1. 'subject'        — top-level CloudEvent attribute (most reliable)
      2. 'resourceOwner'  — top-level CloudEvent extension attribute
      3. data.account_id  — inner data payload
      4. data.resource_id — inner data payload fallback

    IMPORTANT: this function must receive the FULL event (object or dict),
    not just event["data"]. 'subject' and 'resourceOwner' are top-level
    CloudEvent attributes and will be missed if only the data payload is passed.
    """
    subject = _get_event_attr(event, "subject")
    resource_owner = _get_event_attr(event, "resourceOwner")

    resource = event_data.get("resource") if isinstance(event_data, dict) else None
    if not isinstance(resource, dict):
        resource = {}

    return (
        subject
        or resource_owner
        or event_data.get("account_id")
        or event_data.get("resource_id")
        or resource.get("owner_account_id")
        or resource.get("ownerAccountId")
        or resource.get("account_id")
    )


def _create_lifecycle_event_handler(lifecycle_service):
    """Create and return the lifecycle event callback function."""

    def on_lifecycle_event(event, message):
        try:
            event_type, event_data = _extract_event_fields(event)

            is_valid_event, validation_reason = _validate_lifecycle_event(
                event,
                event_type,
                event_data,
            )
            if not is_valid_event:
                logger.warning(
                    "Skipping lifecycle event due to invalid envelope: %s | event=%s",
                    validation_reason,
                    event,
                )
                return

            # Pass the FULL event so subject/resourceOwner (top-level CloudEvent
            # attributes) are reachable — not just the inner data payload.
            account_id = _extract_account_id(event, event_data)

            if not account_id:
                logger.warning(
                    "Lifecycle event missing account_id — skipping. "
                    "Checked: subject, resourceOwner, data.account_id, data.resource_id. "
                    "event=%s",
                    event,
                )
                return

            logger.info(
                "Processing lifecycle event: type=%s account_id=%s",
                event_type,
                account_id,
            )

            result = lifecycle_service.handle_event(
                event_type=event_type,
                account_id=account_id,
                event_data=event_data,
            )

            # Guard: handle_event must return a dict. Defensive against mocked
            # or older implementations that may return None.
            if not isinstance(result, dict):
                logger.error(
                    "handle_event returned %s instead of dict — "
                    "cannot determine outcome. type=%s account_id=%s",
                    type(result).__name__,
                    event_type,
                    account_id,
                )
                return

            if result.get("success"):
                logger.info(
                    "Lifecycle event handled successfully: type=%s account_id=%s",
                    event_type,
                    account_id,
                )
            else:
                logger.error(
                    "Lifecycle event handling failed: type=%s account_id=%s reason=%s",
                    event_type,
                    account_id,
                    result.get("reason", "unknown"),
                )

        except Exception as e:
            logger.error("Error processing lifecycle event: %s", e, exc_info=True)

    return on_lifecycle_event


def _create_dead_letter_handler():
    """Create and return the dead letter callback function."""

    def on_dead_letter(message):
        logger.warning("Dead letter received (could not deserialize): %s", message)

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
            "Lifecycle consumer started. Listening for account lifecycle events "
            "in regions: %s",
            ", ".join(regions),
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
        logger.error("Fatal error in lifecycle consumer: %s", e, exc_info=True)
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
