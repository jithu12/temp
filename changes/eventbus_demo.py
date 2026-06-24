#!/usr/bin/env python3
"""
Demo: Simulate the full EventBus lifecycle round-trip without a real EventBus connection.

Builds valid CloudEvent payloads and runs them through the complete
publisher → broker → consumer → handler pipeline using in-memory stubs.
No external packages, no database, no Celery, no CCP deployment needed.

DEPLOYMENT ARCHITECTURE  (why two separate services exist)

  +---------------------------+      +------------------------------------+
  |   MAIN CP WORKER          |      |   LIFECYCLE CONSUMER (standalone)  |
  |   async/app.py            |      |   async/lifecycle_consumer_main.py |
  |   ccp_config.yaml         |      |   async/ccp_config_lifecycle.yaml  |
  +---------------------------+      +------------------------------------+
  | - Celery worker            |      | - Separate Kubernetes pod          |
  | - REST API tasks           |      | - Blocking AMQP listener loop      |
  | - Workspace provisioning   |      | - Zero impact on CP worker         |
  | - Health checks / backups  |      | - Handles account lifecycle events |
  +---------------------------+      +------------------------------------+
           |                                       |
           | publish outgoing                      | subscribe incoming
           | workspace events                      | account events
           v                                       v
  +------------------------------------------------------------------+
  |             EVENTBUS  (AMQP / RabbitMQ / Cloud Broker)           |
  |             topic: "lifecycle"                                    |
  +------------------------------------------------------------------+

WHY SEPARATE?
  The EventBus consumer uses consumer.run() — a BLOCKING call that loops
  forever waiting for messages. Running this inside the Celery CP worker
  would permanently occupy a worker thread, starving other async tasks.
  The lead's decision: deploy it as its own pod via ccp_config_lifecycle.yaml.

HOW THE STANDALONE POD WORKS (lifecycle_consumer_main.py):
  1. Reads EVENTBUS_USERNAME, EVENTBUS_PASSWORD, EVENTBUS_REGIONS from env
  2. Creates Client + Queue + Consumer for each configured region
  3. Calls consumer.run() (blocking loop) — one thread per region
  4. On each message: callback extracts (event_type, account_id) and
     calls AccountService.handle_event() → updates DB + fires Celery tasks

THIS DEMO simulates both sides in-memory so you can run it without any
infrastructure (no EventBus, no DB, no Celery, no CCP).

Usage (no arguments — runs all 4 consumer scenarios + publisher demo):
    python eventbus_demo.py

Usage (single event):
    python eventbus_demo.py --account-id <uuid> --event-type ResourceDisabled
    python eventbus_demo.py --account-id <uuid> --event-type ResourceActive
    python eventbus_demo.py --account-id <uuid> --event-type ResourceDeleting
    python eventbus_demo.py --account-id <uuid> --event-type ResourceDeleted

    python eventbus_demo.py --account-id <uuid> --event-type ResourceDeleted --dry-run
    python eventbus_demo.py --account-id <uuid> --event-type ResourceDeleted --live for live
Supported event types (short or full form both accepted):
    ResourceDisabled   /  LifecycleEvent.ResourceDisabled
    ResourceActive     /  LifecycleEvent.ResourceActive
    ResourceDeleting   /  LifecycleEvent.ResourceDeleting
    ResourceDeleted    /  LifecycleEvent.ResourceDeleted
"""

import argparse
import datetime
import json
import logging
import sys
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional

# ──────────────────────────────────────────────────────────────────────────────
# Pretty logging
# ──────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)


def section(title: str) -> None:
    """Print a visible section banner."""
    bar = "=" * 72
    print(f"\n+{bar}+")
    print(f"|  {title:<70}|")
    print(f"+{bar}+")


# ══════════════════════════════════════════════════════════════════════════════
# PART 1 — MOCK DOMAIN MODELS
# (stand-ins for dataviz_core models — zero real imports needed)
# ══════════════════════════════════════════════════════════════════════════════

class Status(Enum):
    CREATION_REQUESTED = "CREATION_REQUESTED"
    CREATING           = "CREATING"
    ACTIVE             = "ACTIVE"
    UPDATE_REQUESTED   = "UPDATE_REQUESTED"
    UPDATING           = "UPDATING"
    INACTIVE           = "INACTIVE"
    DELETION_REQUESTED = "DELETION_REQUESTED"
    DELETING           = "DELETING"
    DELETED            = "DELETED"
    FAILED             = "FAILED"


class Workspace:
    """Minimal workspace model."""
    def __init__(self, name: str, owner_account_id: str, status: Status = Status.ACTIVE):
        self.id               = uuid.uuid4()
        self.name             = name
        self.owner_account_id = owner_account_id
        self.status           = status

    def __repr__(self):
        return f"Workspace(name={self.name!r}, status={self.status.value})"


class AccountDetails:
    """Minimal account-details model."""
    def __init__(self, owner_account_id: str, status: Status = Status.ACTIVE):
        self.id               = uuid.uuid4()
        self.owner_account_id = owner_account_id
        self.status           = status

    def __repr__(self):
        return f"AccountDetails(owner_id={self.owner_account_id!r}, status={self.status.value})"


# ══════════════════════════════════════════════════════════════════════════════
# PART 2 — MOCK EVENTBUS CLIENT LIBRARY
# (stand-ins for event_bus_client + sg_cloud_event — zero real imports needed)
# ══════════════════════════════════════════════════════════════════════════════

class LifecycleEvent:
    """
    Mirrors sg_cloud_event.LifecycleEvent.

    Holds the three sections of a CloudEvent:
      • headers    – CloudEvent 1.0 core attributes
      • extensions – vendor-specific extension attributes
      • data       – arbitrary event payload (HAL+JSON resource)
    """
    def __init__(self, headers: dict, extensions: dict, data: dict):
        self.headers    = headers
        self.extensions = extensions
        self.data       = data
        # Convenience accessors used by the consumer
        self.type       = headers.get("type", "")

    def to_dict(self) -> dict:
        return {**self.headers, **self.extensions, "data": self.data}


class CloudEventMessage:
    """
    Mirrors event_bus_client.CloudEventMessage.

    The wire format that gets serialised and sent to the broker.
    """
    def __init__(self, payload: dict):
        self._payload = payload

    @classmethod
    def build_from_event(cls, event: LifecycleEvent) -> "CloudEventMessage":
        return cls(payload=event.to_dict())

    def serialize(self) -> str:
        return json.dumps(self._payload, indent=2)

    def __repr__(self):
        return f"CloudEventMessage(type={self._payload.get('type')!r})"


# ─── Simulated AMQP broker (in-memory queue) ──────────────────────────────────

_BROKER: List[CloudEventMessage] = []   # shared in-memory "topic"


class Publisher:
    """
    Mirrors event_bus_client.Publisher.

    publish() serialises the CloudEventMessage and drops it on the broker.
    """
    def __init__(self, client: Any, topic: str):
        self._client = client
        self._topic  = topic
        self._log    = logging.getLogger("Publisher")

    def publish(self, message: CloudEventMessage) -> None:
        serialised = message.serialize()
        self._log.info(
            f"[PUBLISH] >> topic '{self._topic}' "
            f"(broker now has {len(_BROKER)+1} message(s))"
        )
        self._log.debug(f"Wire payload:\n{serialised}")
        _BROKER.append(message)


class Queue:
    """
    Mirrors event_bus_client.Queue.

    Binds to the broker topic with specific routing keys.
    """
    def __init__(self, client: Any, alias: str, topic: str, routing_key: List[str]):
        self.alias       = alias
        self.topic       = topic
        self.routing_key = routing_key
        self._log        = logging.getLogger("Queue")
        self._log.info(
            f"Queue '{alias}' bound to topic '{topic}' "
            f"with routing keys: {routing_key}"
        )


class Consumer:
    """
    Mirrors event_bus_client.Consumer.

    In production this runs a blocking AMQP loop.
    Here we drain the in-memory broker synchronously.
    """
    def __init__(self, queue: Queue, callback, dead_letter=None, auto_ack=True, timeout=10):
        self._queue       = queue
        self._callback    = callback
        self._dead_letter = dead_letter
        self._auto_ack    = auto_ack
        self._log         = logging.getLogger("Consumer")

    def drain(self) -> None:
        """Consume all pending messages from the in-memory broker."""
        self._log.info(f"[CONSUME] << draining {len(_BROKER)} message(s) from broker ...")
        while _BROKER:
            msg = _BROKER.pop(0)
            event = LifecycleEvent(
                headers    = msg._payload,
                extensions = msg._payload,
                data       = msg._payload.get("data", {}),
            )
            self._callback(event, msg)
        self._log.info("Consumer: broker is now empty.")


class Client:
    """Stub for event_bus_client.Client (AMQP connection)."""
    def __init__(self, user: str, password: str, region: str):
        self.user   = user
        self.region = region
        logging.getLogger("Client").info(
            f"Client connected  user={user!r}  region={region!r}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# PART 3 — EVENTBUS ADAPTER  (mirrors core/eventbus_adapter.py)
# ══════════════════════════════════════════════════════════════════════════════

STATUS_TO_LIFECYCLE_EVENT_TYPE = {
    Status.CREATION_REQUESTED : "LifecycleEvent.ResourceCreating",
    Status.CREATING           : "LifecycleEvent.ResourceCreating",
    Status.ACTIVE             : "LifecycleEvent.ResourceReady",
    Status.UPDATE_REQUESTED   : "LifecycleEvent.ResourceModifying",
    Status.UPDATING           : "LifecycleEvent.ResourceModifying",
    Status.DELETION_REQUESTED : "LifecycleEvent.ResourceDeleting",
    Status.DELETING           : "LifecycleEvent.ResourceDeleting",
    Status.DELETED            : "LifecycleEvent.ResourceDeleted",
    Status.FAILED             : "LifecycleEvent.ResourceError",
    Status.INACTIVE           : "LifecycleEvent.ResourceError",
}


def get_lifecycle_event_type(old_status: Status, new_status: Status) -> Optional[str]:
    if new_status == Status.ACTIVE and old_status == Status.UPDATING:
        return "LifecycleEvent.ResourceModified"
    return STATUS_TO_LIFECYCLE_EVENT_TYPE.get(new_status)


class EventBusAdapter:
    """
    Mirrors core/eventbus_adapter.py — EventBusAdapter.

    Converts an internal status-change into a CloudEvent and publishes it.
    """

    def __init__(self, eventbus_account: Client, account_id: str, fqdn_source: str):
        self.__account    = eventbus_account
        self._account_id  = account_id
        self._fqdn_source = fqdn_source
        self.logger       = logging.getLogger("EventBusAdapter")

    def send_status(
        self,
        resource_type: str,
        resource_id: str,
        old_status: Status,
        new_status: Status,
        data: Dict[str, Any],
    ) -> None:
        event_type = get_lifecycle_event_type(old_status, new_status)
        if not event_type:
            self.logger.warning(
                f"No lifecycle event type for transition "
                f"{old_status.value} → {new_status.value}. Skipping publish."
            )
            return

        # ── CloudEvent 1.0 core attributes ────────────────────────────────────
        headers = {
            "specversion"     : "1.0",
            "id"              : str(uuid.uuid4()),
            "type"            : event_type,
            "source"          : self._fqdn_source,
            "time"            : datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "subject"         : resource_id,
            "datacontenttype" : "application/hal+json",
            "dataschema"      : "srn:sgcp:refdata:schema:schema-resource-json-1.0.0",
        }
        self.logger.info(f"CloudEvent headers : {headers}")

        # ── CloudEvent extension attributes ───────────────────────────────────
        extensions = {
            "resourceowner"   : self._account_id,
            "resourceservice" : self._fqdn_source,
            "resourcetype"    : resource_type,
            "resourcesubtype" : "None",
        }
        self.logger.info(f"CloudEvent extensions : {extensions}")
        self.logger.info(f"CloudEvent data       : {data}")

        # ── Build + publish ────────────────────────────────────────────────────
        event   = LifecycleEvent(headers=headers, extensions=extensions, data=data)
        message = CloudEventMessage.build_from_event(event)
        self.logger.info(f"CloudEvent message built: {message}")

        publisher = Publisher(client=self.__account, topic="lifecycle")
        publisher.publish(message)


# ══════════════════════════════════════════════════════════════════════════════
# PART 4 — MOCK IN-MEMORY REPOSITORIES + WORKFLOW EXECUTOR
# ══════════════════════════════════════════════════════════════════════════════

class InMemoryAccountRepo:
    """Dead-simple in-memory store for AccountDetails."""

    def __init__(self):
        self._store: Dict[str, AccountDetails] = {}

    def save(self, account: AccountDetails) -> None:
        self._store[account.owner_account_id] = account

    def get(self, owner_account_id: str) -> Optional[AccountDetails]:
        return self._store.get(str(owner_account_id))

    def update_status(self, owner_account_id: str, status: Status) -> None:
        acc = self._store.get(str(owner_account_id))
        if acc:
            acc.status = status


class InMemoryWorkspaceRepo:
    """Dead-simple in-memory store for Workspaces."""

    def __init__(self):
        self._store: List[Workspace] = []

    def save(self, workspace: Workspace) -> None:
        self._store.append(workspace)

    def list_by_owner(
        self,
        owner_account_id: str,
        status_filter: Optional[Status] = None,
    ) -> List[Workspace]:
        results = [w for w in self._store if w.owner_account_id == str(owner_account_id)]
        if status_filter:
            results = [w for w in results if w.status == status_filter]
        return results

    def update_status_for_owner(self, owner_account_id: str, status: Status) -> List[Workspace]:
        updated = []
        for w in self._store:
            if w.owner_account_id == str(owner_account_id):
                w.status = status
                updated.append(w)
        return updated


class MockMonitoringService:
    """
    Mirrors core/monitoring_client.py MonitoringClient.send_notification().

    Real signature:
        send_notification(self, error_level, email_body, email_subject) -> bool

    In production:
        - Gets an IAM token via sg_iamaas.create_token()
        - POSTs to https://monitoring.eu-fr-paris.cloud.socgen/v2/notifications/emails
        - Returns True on HTTP 202, raises MonitoringError on 400/401

    In this demo:
        - Prints the alert to stdout so you can see it
        - Stores it in sent_alerts[] for post-run inspection
        - Returns True (mirrors the real success path)
    """

    def __init__(self):
        self._log = logging.getLogger("MonitoringClient")
        self.sent_alerts: List[dict] = []   # stores alerts for inspection

    def send_notification(
        self,
        error_level: str,
        email_body: str,
        email_subject: str,
    ) -> bool:
        """
        Mirrors MonitoringClient.send_notification(error_level, email_body, email_subject).
        NOTE: email_body comes BEFORE email_subject — matches the real client signature.
        """
        self._log.error(
            f"\n"
            f"  +------------------------------------------------------------------+\n"
            f"  |  [ALERT EMAIL]  error_level : {error_level:<34}|\n"
            f"  |  subject : {email_subject:<58}|\n"
            f"  +------------------------------------------------------------------+\n"
            f"{email_body}"
        )
        # In production: POSTs to monitoring API.  In demo: stores for assertion.
        self.sent_alerts.append({
            "error_level"  : error_level,
            "email_subject": email_subject,
            "email_body"   : email_body,
        })
        return True   # mirrors HTTP 202 success path


class MockWorkflowExecutor:
    """
    Simulates the Celery async worker.

    In production this sends a task to a Celery broker.
    Here we execute it immediately (synchronously) for demo purposes.
    """

    def __init__(self, account_service_ref):
        self._svc = account_service_ref
        self._log = logging.getLogger("WorkflowExecutor")

    def async_exec_core_function(self, service: str, function: str, kwargs: dict):
        self._log.info(
            f"[CELERY TASK] Dispatching {service}.{function}({kwargs})"
        )
        func = getattr(self._svc, function)
        func(**kwargs)


# ══════════════════════════════════════════════════════════════════════════════
# PART 5 — MOCK ACCOUNT SERVICE  (mirrors core/accounts.py)
# ══════════════════════════════════════════════════════════════════════════════

class MockAccountService:
    """
    Simplified AccountService that mirrors the real one.

    Uses in-memory repos instead of SQLAlchemy.
    The handle_event() router is identical to the production version.
    """

    def __init__(
        self,
        account_repo: InMemoryAccountRepo,
        workspace_repo: InMemoryWorkspaceRepo,
        eventbus_adapter: Optional[EventBusAdapter] = None,
    ):
        self._accounts         = account_repo
        self._workspaces       = workspace_repo
        self._eventbus         = eventbus_adapter
        self.workflow_executor = None          # injected after construction
        self.monitoring_service = None         # injected via set_monitoring_service()
        self.logger            = logging.getLogger("AccountService")

    def set_monitoring_service(self, monitoring_service) -> None:
        """Inject MockMonitoringService (mirrors AccountService.set_monitoring_service)."""
        self.monitoring_service = monitoring_service

    # ── lifecycle event router ────────────────────────────────────────────────

    def handle_event(
        self,
        event_type: str,
        account_id: str,
        event_data: Optional[dict] = None,
    ) -> dict:
        """
        Route an incoming lifecycle event to the correct handler.
        Mirrors core/accounts.py AccountService.handle_event().
        """
        handlers = {
            "LifecycleEvent.ResourceDisabled": self._handle_resource_disabled,
            "ResourceDisabled"               : self._handle_resource_disabled,
            "LifecycleEvent.ResourceActive"  : self._handle_resource_active,
            "ResourceActive"                 : self._handle_resource_active,
            "LifecycleEvent.ResourceDeleting": self._handle_resource_deleting,
            "ResourceDeleting"               : self._handle_resource_deleting,
            "LifecycleEvent.ResourceDeleted" : self._handle_resource_deleted,
            "ResourceDeleted"                : self._handle_resource_deleted,
        }
        handler = handlers.get(event_type)
        if handler is None:
            self.logger.warning(f"Unknown event type '{event_type}' — ignoring.")
            return {"success": False, "message": f"Unknown event type: {event_type}"}

        self.logger.info(f"[ROUTE] Routing '{event_type}' -> {handler.__name__}()")
        try:
            handler(account_id=account_id, event_data=event_data or {})
            return {"success": True, "message": f"{event_type} handled OK"}
        except Exception as exc:
            self.logger.error(f"Handler failed: {exc}", exc_info=True)
            return {"success": False, "message": str(exc)}

    # ── individual handlers ───────────────────────────────────────────────────

    def _handle_resource_disabled(self, account_id: str, event_data: dict) -> None:
        """
        LifecycleEvent.ResourceDisabled
        → Accounts Team has started the grace period for this account.
        → We deactivate all ACTIVE workspaces.
        """
        self.logger.info(
            f"  [ResourceDisabled] Account {account_id} — grace period starts. "
            "Deactivating workspaces …"
        )
        account = self._accounts.get(account_id)
        if not account:
            self.logger.warning(f"  Account {account_id} not found — skipping.")
            return

        if account.status == Status.INACTIVE:
            self.logger.info("  Account already INACTIVE — nothing to do.")
            return

        active_ws = self._workspaces.list_by_owner(account_id, status_filter=Status.ACTIVE)
        if not active_ws:
            self.logger.info("  No active workspaces — setting account INACTIVE immediately.")
            self._accounts.update_status(account_id, Status.INACTIVE)
            return

        self.logger.info(f"  Found {len(active_ws)} active workspace(s) — scheduling async task.")
        self._accounts.update_status(account_id, Status.UPDATE_REQUESTED)

        if self.workflow_executor:
            self.workflow_executor.async_exec_core_function(
                service="account",
                function="deactivate_account",
                kwargs={"owner_account_id": account_id},
            )

    def deactivate_account(self, owner_account_id: str) -> None:
        """Called by async worker — deactivates all ACTIVE workspaces."""
        self.logger.info(f"  [async] deactivate_account({owner_account_id})")
        updated = self._workspaces.update_status_for_owner(
            owner_account_id, Status.INACTIVE
        )
        self.logger.info(f"  Deactivated {len(updated)} workspace(s): {[w.name for w in updated]}")
        self._accounts.update_status(owner_account_id, Status.INACTIVE)
        self.logger.info(f"  Account {owner_account_id} → INACTIVE ✓")

    def _handle_resource_active(self, account_id: str, event_data: dict) -> None:
        """
        LifecycleEvent.ResourceActive
        → Accounts Team cancelled the grace period — reactivate.
        → We reactivate all INACTIVE workspaces.
        """
        self.logger.info(
            f"  [ResourceActive] Account {account_id} — grace period cancelled. "
            "Reactivating workspaces …"
        )
        account = self._accounts.get(account_id)
        if not account:
            self.logger.warning(f"  Account {account_id} not found — skipping.")
            return

        if account.status == Status.ACTIVE:
            self.logger.info("  Account already ACTIVE — nothing to do.")
            return

        inactive_ws = self._workspaces.list_by_owner(account_id, status_filter=Status.INACTIVE)
        if not inactive_ws:
            self.logger.info("  No inactive workspaces — setting account ACTIVE immediately.")
            self._accounts.update_status(account_id, Status.ACTIVE)
            return

        self.logger.info(f"  Found {len(inactive_ws)} inactive workspace(s) — scheduling async task.")
        self._accounts.update_status(account_id, Status.UPDATE_REQUESTED)

        if self.workflow_executor:
            self.workflow_executor.async_exec_core_function(
                service="account",
                function="reactivate_account",
                kwargs={"owner_account_id": account_id},
            )

    def reactivate_account(self, owner_account_id: str) -> None:
        """Called by async worker — reactivates all INACTIVE workspaces."""
        self.logger.info(f"  [async] reactivate_account({owner_account_id})")
        updated = self._workspaces.update_status_for_owner(
            owner_account_id, Status.ACTIVE
        )
        self.logger.info(f"  Reactivated {len(updated)} workspace(s): {[w.name for w in updated]}")
        self._accounts.update_status(owner_account_id, Status.ACTIVE)
        self.logger.info(f"  Account {owner_account_id} → ACTIVE ✓")

    def _handle_resource_deleting(self, account_id: str, event_data: dict) -> None:
        """
        LifecycleEvent.ResourceDeleting
        → Accounts Team is permanently deleting the account (grace period over).
        → We delete ALL workspaces.
        """
        self.logger.info(
            f"  [ResourceDeleting] Account {account_id} — permanent deletion triggered."
        )
        account = self._accounts.get(account_id)
        if not account:
            self.logger.warning(f"  Account {account_id} not found — skipping.")
            return

        if account.status == Status.DELETED:
            self.logger.info("  Account already DELETED — nothing to do.")
            return

        pending_ws = [
            w for w in self._workspaces.list_by_owner(account_id)
            if w.status != Status.DELETED
        ]
        if not pending_ws:
            self.logger.info("  No pending workspaces — marking account DELETED immediately.")
            self._accounts.update_status(account_id, Status.DELETED)
            return

        self.logger.info(f"  Found {len(pending_ws)} workspace(s) to delete — scheduling async task.")
        if self.workflow_executor:
            self.workflow_executor.async_exec_core_function(
                service="account",
                function="delete_account",
                kwargs={"owner_account_id": account_id},
            )

    def delete_account(self, owner_account_id: str) -> None:
        """Called by async worker — deletes all workspaces."""
        self.logger.info(f"  [async] delete_account({owner_account_id})")
        updated = self._workspaces.update_status_for_owner(
            owner_account_id, Status.DELETED
        )
        self.logger.info(f"  Deleted {len(updated)} workspace(s): {[w.name for w in updated]}")
        self._accounts.update_status(owner_account_id, Status.DELETED)
        self.logger.info(f"  Account {owner_account_id} → DELETED ✓")

    def _handle_resource_deleted(self, account_id: str, event_data: dict) -> None:
        """
        LifecycleEvent.ResourceDeleted
        → Accounts Team confirms deletion is complete.
        → We verify no residual workspaces remain.
        → If residuals exist, send alert via MonitoringService (email in production).
        """
        self.logger.info(
            f"  [ResourceDeleted] Account {account_id} — final cleanup verification."
        )
        account = self._accounts.get(account_id)
        if not account:
            self.logger.warning(f"  Account {account_id} not found in DB — skipping.")
            return

        remaining = [
            w for w in self._workspaces.list_by_owner(account_id)
            if w.status != Status.DELETED
        ]
        if not remaining:
            self.logger.info(
                f"  ✅  Account {account_id}: all resources cleaned up — no action needed."
            )
            return

        # Residual resources found — log error and send alert
        self.logger.error(
            f"  ❌  Account {account_id}: {len(remaining)} workspace(s) NOT yet deleted!"
        )

        workspace_details = "\n".join([
            f"    - Workspace: {w.name}  id={w.id}  status={w.status.value}"
            for w in remaining
        ])
        email_subject = f"Manual Cleanup Required: Account {account_id}"
        email_body = (
            f"Account {account_id} has been marked as DELETED by Accounts Team,\n"
            f"but {len(remaining)} workspace(s) could not be deleted automatically.\n\n"
            f"Remaining workspaces:\n{workspace_details}\n\n"
            f"Please investigate and manually clean up these resources."
        )

        if self.monitoring_service is not None:
            self.monitoring_service.send_notification(
                error_level="ERROR",
                email_subject=email_subject,
                email_body=email_body,
            )
        else:
            self.logger.warning(
                "  monitoring_service not set — alert NOT sent (would be sent in production)."
            )

    # ── helper for inspecting state ───────────────────────────────────────────

    def print_state(self, account_id: str) -> None:
        acc = self._accounts.get(account_id)
        ws_list = self._workspaces.list_by_owner(account_id)
        print(f"\n  {'-'*60}")
        print(f"  Account  : {account_id}")
        print(f"  Status   : {acc.status.value if acc else 'NOT FOUND'}")
        print(f"  Workspaces ({len(ws_list)}):")
        for w in ws_list:
            print(f"    * {w.name:<30} status={w.status.value}")
        print(f"  {'-'*60}\n")


# ══════════════════════════════════════════════════════════════════════════════
# PART 6 — CONSUMER CALLBACK FACTORY  (mirrors async/app.py)
# ══════════════════════════════════════════════════════════════════════════════

def build_consumer_callback(account_service: MockAccountService):
    """
    Mirrors _create_lifecycle_event_handler() in async/app.py.

    Returns the callback that the Consumer calls for every received message.

    IMPORTANT — "subject" vs "resourceowner":
      subject       = the RESOURCE UUID (workspace/artifact being acted on)
      resourceowner = the ACCOUNT UUID  (what we need to look up in our DB)

    This mirrors the fix in async/app.py _extract_account_id().
    """
    log = logging.getLogger("ConsumerCallback")

    def on_lifecycle_event(event: LifecycleEvent, message: CloudEventMessage) -> None:
        # ── Extract fields ────────────────────────────────────────────────────
        event_type = getattr(event, "type", "") or event.headers.get("type", "")

        # resourceowner = account UUID  (subject = resource UUID, NOT the account)
        account_id = (
            event.extensions.get("resourceowner")
            or event.headers.get("resourceowner")
            # fallback: parse ownerId SRN  "srn:sgcp:…:account:<uuid>"
            or (lambda r: r.split(":")[-1] if ":" in r else r)(
                event.data.get("resource", {}).get("ownerId", "")
            ) or event.data.get("resource", {}).get("owner_account_id", "")
        ) or None

        if not account_id:
            log.warning(f"  Received event with no account_id — dropping: {event_type}")
            return

        log.info(f"\n  +-- Received message ---------------------------------------------------")
        log.info(f"  |  type       : {event_type}")
        log.info(f"  |  account_id : {account_id}")
        log.info(f"  |  source     : {event.headers.get('source')}")
        log.info(f"  |  time       : {event.headers.get('time')}")
        log.info(f"  +-----------------------------------------------------------------------")

        result = account_service.handle_event(
            event_type=event_type,
            account_id=account_id,
            event_data=event.data,
        )

        if result["success"]:
            log.info(f"  ✅  handle_event → {result['message']}")
        else:
            log.error(f"  ❌  handle_event failed → {result['message']}")

    return on_lifecycle_event


# ══════════════════════════════════════════════════════════════════════════════
# PART 7 — FULL END-TO-END DEMO SCENARIOS
# ══════════════════════════════════════════════════════════════════════════════

def demo_publisher_side(adapter: EventBusAdapter, account_id: str) -> None:
    """
    PUBLISHER SIDE
    ─────────────
    Simulates Dataviz publishing outgoing lifecycle events when a Workspace
    changes state (e.g. after being provisioned → ACTIVE).

    Maps to: EventBusAdapter.send_status() in core/eventbus_adapter.py
    """
    section("PUBLISHER SIDE — Dataviz publishes outgoing Workspace lifecycle events")

    workspace_id = str(uuid.uuid4())
    print(f"  Workspace ID : {workspace_id}")

    transitions = [
        (None,             Status.CREATION_REQUESTED, {"resource": {"id": workspace_id, "status": "CREATION_REQUESTED"}}),
        (Status.CREATION_REQUESTED, Status.CREATING,  {"resource": {"id": workspace_id, "status": "CREATING"}}),
        (Status.CREATING,  Status.ACTIVE,              {"resource": {"id": workspace_id, "status": "ACTIVE"}}),
        (Status.ACTIVE,    Status.UPDATING,            {"resource": {"id": workspace_id, "status": "UPDATING"}}),
        (Status.UPDATING,  Status.ACTIVE,              {"resource": {"id": workspace_id, "status": "ACTIVE"}}),
        (Status.ACTIVE,    Status.DELETED,             {"resource": {"id": workspace_id, "status": "DELETED"}}),
    ]

    for old, new, data in transitions:
        event_type = get_lifecycle_event_type(old, new) if old else STATUS_TO_LIFECYCLE_EVENT_TYPE.get(new)
        print(f"\n  ── Transition: {(old.value if old else 'START'):<22} → {new.value:<22}"
              f"  publishes: {event_type}")
        adapter.send_status(
            resource_type="workspace",
            resource_id=workspace_id,
            old_status=old or new,
            new_status=new,
            data=data,
        )

    print(f"\n  Broker now contains {len(_BROKER)} message(s) from publisher demo.\n")


def demo_consumer_scenario(
    title: str,
    event_type: str,
    account_id: str,
    account_service: MockAccountService,
    consumer: Consumer,
    source_fqdn: str,
) -> None:
    """
    CONSUMER SIDE — run one scenario.

    1. Crafts a CloudEvent that the Accounts Team EventBus would send us.
    2. Places it on the in-memory broker.
    3. Consumer drains the broker (calls our callback).
    4. AccountService handles the event and mutates workspace/account state.
    """
    section(f"CONSUMER SIDE — Scenario: {title}")

    # Build the incoming CloudEvent matching the REAL format (from Accounts Team)
    # Key insight from real example.json:
    #   subject       = the RESOURCE UUID being acted on  (NOT the account)
    #   resourceowner = the ACCOUNT UUID  ← what we need to look up in our DB
    #   data.resource.ownerId = SRN:  "srn:sgcp:account.cloud.socgen:account:<account_uuid>"
    resource_id = str(uuid.uuid4())   # the account resource being acted on by Accounts Team
    lifecycle_status = event_type.split(".")[-1].lower()  # e.g. "resourcedisabled"

    event_dict = {
        # ── CloudEvent 1.0 core attributes ────────────────────────────────────
        "specversion"     : "1.0",
        "id"              : str(uuid.uuid4()),
        "time"            : datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "type"            : event_type,
        "source"          : source_fqdn,
        "subject"         : resource_id,           # ← RESOURCE UUID (NOT account)
        "datacontenttype" : "application/hal+json",
        "dataschema"      : "srn:sgcp:schema:resource:json-schema:1.0.0",
        # ── CloudEvent extension attributes ───────────────────────────────────
        "resourceowner"   : account_id,            # ← ACCOUNT UUID  (what we look up)
        "resourceservice" : source_fqdn,
        "resourcetype"    : "Account",
        "resourcesubtype" : "None",
        # ── HAL+JSON payload  (mirrors real Accounts Team payload) ────────────
        "data": {
            "resource": {
                "timestamp"        : datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "id"               : resource_id,
                "globalId"         : f"srn:sgcp:{source_fqdn}:account:{resource_id}",
                "uniqueName"       : f"account-{account_id[:8]}",
                "friendlyName"     : f"account-{account_id[:8]}",
                "resourceType"     : "account",
                "resourceSubType"  : "None",
                "serviceOffer"     : "None",
                # ownerId SRN — last segment is the account UUID
                "ownerId"          : f"srn:sgcp:account.cloud.socgen:account:{account_id}",
                "serviceId"        : f"srn:sgcp:refdata:service-instance:{source_fqdn}",
                "location"         : "global",
                "lifecycleStatus"  : lifecycle_status,
                "_links": {
                    "self": {"href": f"srn:sgcp:{source_fqdn}:account:{resource_id}"},
                    "sgcp:isOwnedBy": {
                        "href": f"srn:sgcp:account.cloud.socgen:account:{account_id}"
                    },
                },
            }
        },
    }

    print(f"\n  Incoming CloudEvent from Accounts Team EventBus:")
    print("  " + json.dumps(event_dict, indent=4).replace("\n", "\n  "))

    # Drop onto broker as if it arrived via AMQP
    _BROKER.append(CloudEventMessage(event_dict))

    print(f"\n  State BEFORE consuming:")
    account_service.print_state(account_id)

    # Consumer picks it up
    consumer.drain()

    print(f"\n  State AFTER consuming:")
    account_service.print_state(account_id)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN — wire everything together and run the demo
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    section("SETUP — wiring EventBus, Publisher, Consumer, AccountService")

    # ── Fixed demo IDs ────────────────────────────────────────────────────────
    ACCOUNT_ID  = "79fadc0d-90d9-45fe-9ab9-bbfbc4a2a28a"    # from example.json
    FQDN_SOURCE = "ocs-uat.eu-fr-paris.cloud.socgen"         # from example.json
    REGION      = "eu-fr-paris"

    # ── EventBus client (AMQP connection) ─────────────────────────────────────
    eb_client = Client(user="svc-dataviz", password="s3cr3t", region=REGION)

    # ── EventBusAdapter (publisher side) ──────────────────────────────────────
    adapter = EventBusAdapter(
        eventbus_account=eb_client,
        account_id=ACCOUNT_ID,
        fqdn_source=FQDN_SOURCE,
    )

    # ── In-memory repos + account service ────────────────────────────────────
    account_repo   = InMemoryAccountRepo()
    workspace_repo = InMemoryWorkspaceRepo()

    account_service = MockAccountService(
        account_repo=account_repo,
        workspace_repo=workspace_repo,
        eventbus_adapter=adapter,
    )

    # ── Inject mock workflow executor (mirrors Celery async tasks) ────────────
    executor = MockWorkflowExecutor(account_service_ref=account_service)
    account_service.workflow_executor = executor

    # ── Inject MonitoringService (sends email when residual workspaces found) ──
    # mirrors: core/core.py  self.account.set_monitoring_service(self.monitoring)
    monitoring = MockMonitoringService()
    account_service.set_monitoring_service(monitoring)

    # ── Consumer (subscriber side) ────────────────────────────────────────────
    queue = Queue(
        client=eb_client,
        alias="dataviz-lifecycle-consumer",
        topic="lifecycle",
        routing_key=[
            "LifecycleEvent.ResourceDisabled",
            "LifecycleEvent.ResourceActive",
            "LifecycleEvent.ResourceDeleting",
            "LifecycleEvent.ResourceDeleted",
        ],
    )

    callback = build_consumer_callback(account_service)
    consumer = Consumer(queue=queue, callback=callback, auto_ack=True, timeout=10)

    # ── Seed the database with an account and some workspaces ─────────────────
    account = AccountDetails(owner_account_id=ACCOUNT_ID, status=Status.ACTIVE)
    account_repo.save(account)

    for ws_name in ["grafana-prod", "grafana-dev", "grafana-staging"]:
        workspace_repo.save(Workspace(name=ws_name, owner_account_id=ACCOUNT_ID, status=Status.ACTIVE))

    print(f"\n  Initial state:")
    account_service.print_state(ACCOUNT_ID)

    # ════════════════════════════════════════════════════════════════════════
    # PUBLISHER SIDE DEMO
    # ════════════════════════════════════════════════════════════════════════
    demo_publisher_side(adapter, ACCOUNT_ID)

    # Clear broker so publisher messages don't pollute consumer scenarios
    _BROKER.clear()
    print("  (Broker cleared — ready for consumer scenarios)\n")

    # ════════════════════════════════════════════════════════════════════════
    # CONSUMER SIDE DEMO — 4 scenarios in account lifecycle order
    # ════════════════════════════════════════════════════════════════════════

    # Scenario 1: Accounts Team disables the account (grace period starts)
    demo_consumer_scenario(
        title         = "ResourceDisabled — grace period starts (deactivate workspaces)",
        event_type    = "LifecycleEvent.ResourceDisabled",
        account_id    = ACCOUNT_ID,
        account_service=account_service,
        consumer      = consumer,
        source_fqdn   = FQDN_SOURCE,
    )

    # Scenario 2: Accounts Team re-enables the account (customer paid — cancel grace period)
    demo_consumer_scenario(
        title         = "ResourceActive — grace period cancelled (reactivate workspaces)",
        event_type    = "LifecycleEvent.ResourceActive",
        account_id    = ACCOUNT_ID,
        account_service=account_service,
        consumer      = consumer,
        source_fqdn   = FQDN_SOURCE,
    )

    # Scenario 3: Grace period over — account being permanently deleted
    demo_consumer_scenario(
        title         = "ResourceDeleting — permanent deletion (delete all workspaces)",
        event_type    = "LifecycleEvent.ResourceDeleting",
        account_id    = ACCOUNT_ID,
        account_service=account_service,
        consumer      = consumer,
        source_fqdn   = FQDN_SOURCE,
    )

    # Scenario 4: Accounts Team confirms account is fully deleted — we verify
    demo_consumer_scenario(
        title         = "ResourceDeleted — final cleanup verification (clean)",
        event_type    = "LifecycleEvent.ResourceDeleted",
        account_id    = ACCOUNT_ID,
        account_service=account_service,
        consumer      = consumer,
        source_fqdn   = FQDN_SOURCE,
    )

    # ════════════════════════════════════════════════════════════════════════
    # BONUS SCENARIO: ResourceDeleted with RESIDUAL workspaces still present
    # This is the path that triggers the MonitoringService email alert.
    # ════════════════════════════════════════════════════════════════════════
    section("BONUS — ResourceDeleted with RESIDUAL workspaces (triggers email alert)")

    # Seed a second account that has workspaces still ACTIVE (deletion failed)
    STALE_ACCOUNT_ID = "deadbeef-dead-beef-dead-beefdeadbeef"
    account_repo.save(AccountDetails(owner_account_id=STALE_ACCOUNT_ID, status=Status.DELETED))
    workspace_repo.save(Workspace(name="grafana-orphaned", owner_account_id=STALE_ACCOUNT_ID, status=Status.ACTIVE))
    workspace_repo.save(Workspace(name="grafana-stuck",    owner_account_id=STALE_ACCOUNT_ID, status=Status.INACTIVE))

    print(f"\n  Account {STALE_ACCOUNT_ID} was marked DELETED but workspaces were NOT cleaned up.")
    print(f"  Accounts Team now sends ResourceDeleted — Dataviz must detect and alert.\n")

    demo_consumer_scenario(
        title         = "ResourceDeleted — RESIDUAL workspaces found (email alert sent!)",
        event_type    = "LifecycleEvent.ResourceDeleted",
        account_id    = STALE_ACCOUNT_ID,
        account_service=account_service,
        consumer      = consumer,
        source_fqdn   = FQDN_SOURCE,
    )

    if monitoring.sent_alerts:
        print(f"\n  MonitoringService.sent_alerts ({len(monitoring.sent_alerts)} total):")
        for i, alert in enumerate(monitoring.sent_alerts, 1):
            print(f"    [{i}] level={alert['error_level']}  subject={alert['email_subject']}")
    print()

    # ════════════════════════════════════════════════════════════════════════
    # UNKNOWN EVENT TYPE — shows the graceful ignore path
    # ════════════════════════════════════════════════════════════════════════
    section("EDGE CASE — Unknown event type (graceful ignore)")
    result = account_service.handle_event(
        event_type="LifecycleEvent.ResourceExpired",   # not in the routing table
        account_id=ACCOUNT_ID,
    )
    print(f"  handle_event result: {result}\n")

    # ════════════════════════════════════════════════════════════════════════
    # SUMMARY
    # ════════════════════════════════════════════════════════════════════════
    section("DEMO COMPLETE — Summary")
    print("""
  DEPLOYMENT ARCHITECTURE
  ───────────────────────────────────────────────────────
  Two separate Kubernetes pods — NOT running together:

    Pod 1: MAIN CP WORKER  (async/app.py + ccp_config.yaml)
      - Celery worker for REST API async tasks
      - Workspace provisioning, health checks, backups
      - Publishes outgoing workspace events via EventBusAdapter

    Pod 2: LIFECYCLE CONSUMER  (async/lifecycle_consumer_main.py + ccp_config_lifecycle.yaml)
      - Standalone blocking process: consumer.run() loops forever
      - Subscribes to Accounts Team lifecycle events from EventBus
      - Calls AccountService.handle_event() → fires Celery tasks back to Pod 1

  WHY SEPARATE?  consumer.run() is a blocking infinite loop.
  Running it inside the CP worker would starve other Celery tasks.

  PUBLISHER side  (Pod 1 → EventBus → other services)
  ───────────────────────────────────────────────────────
  When a Workspace changes status, the CP worker calls:
      EventBusAdapter.send_status(resource_type, resource_id, old_status, new_status, data)

  This builds a CloudEvent:
    - headers:    specversion, id, type, source, time, subject (resource UUID)
    - extensions: resourceowner (account UUID), resourceservice, resourcetype
    - data:       HAL+JSON resource payload
  Then publishes it to AMQP topic "lifecycle".

  CONSUMER side  (Pod 2 — Accounts Team → EventBus → Dataviz)
  ───────────────────────────────────────────────────────
  lifecycle_consumer_main.py reads env vars and calls:
      _create_lifecycle_consumers()  →  one Consumer per EVENTBUS_REGIONS entry
      consumer.run()                 →  blocking AMQP loop

  On each message, the callback in async/app.py:
    1. Extracts event_type  from headers["type"]
    2. Extracts account_id  from extensions["resourceowner"]   (NOT subject!)
       Fallback: data.resource.ownerId SRN last segment
    3. Calls AccountService.handle_event(event_type, account_id, event_data)

  Routing table:
      LifecycleEvent.ResourceDisabled   ->  deactivate all ACTIVE workspaces
      LifecycleEvent.ResourceActive     ->  reactivate all INACTIVE workspaces
      LifecycleEvent.ResourceDeleting   ->  delete ALL workspaces (permanent)
      LifecycleEvent.ResourceDeleted    ->  verify cleanup, alert if residuals

  CloudEvent field key (from real Accounts Team payload):
      "subject"       = resource UUID  (the object being acted on)
      "resourceowner" = account UUID   (what we look up in our DB)
      "data.resource.ownerId" = "srn:sgcp:account.cloud.socgen:account:<uuid>"
""")


# ── Argument normaliser ───────────────────────────────────────────────────────
# Accepts both short form ("ResourceDisabled") and full form
# ("LifecycleEvent.ResourceDisabled") so either works on the CLI.
_SHORT_TO_FULL = {
    "ResourceDisabled" : "LifecycleEvent.ResourceDisabled",
    "ResourceActive"   : "LifecycleEvent.ResourceActive",
    "ResourceDeleting" : "LifecycleEvent.ResourceDeleting",
    "ResourceDeleted"  : "LifecycleEvent.ResourceDeleted",
}

_SCENARIO_TITLES = {
    "LifecycleEvent.ResourceDisabled" : "ResourceDisabled — grace period starts (deactivate workspaces)",
    "LifecycleEvent.ResourceActive"   : "ResourceActive — grace period cancelled (reactivate workspaces)",
    "LifecycleEvent.ResourceDeleting" : "ResourceDeleting — permanent deletion (delete all workspaces)",
    "LifecycleEvent.ResourceDeleted"  : "ResourceDeleted — final cleanup verification",
}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="EventBus lifecycle demo — runs without a real EventBus/CCP connection.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Supported event types (short or full form both accepted):\n"
            "    ResourceDisabled   /  LifecycleEvent.ResourceDisabled\n"
            "    ResourceActive     /  LifecycleEvent.ResourceActive\n"
            "    ResourceDeleting   /  LifecycleEvent.ResourceDeleting\n"
            "    ResourceDeleted    /  LifecycleEvent.ResourceDeleted\n"
        ),
    )
    parser.add_argument(
        "--account-id",
        default=None,
        help="Account UUID to use (defaults to the hardcoded demo UUID)",
    )
    parser.add_argument(
        "--event-type",
        default=None,
        help="Run a single consumer scenario for this event type only",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Run the full lifecycle action (deactivate / reactivate / delete …) "
            "using hardcoded demo data — no CCP, no DB, no EventBus needed. "
            "Requires --event-type."
        ),
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Connect to real CCP infrastructure (requires env vars — see below)",
    )
    args = parser.parse_args()

    # ── --live flag: initialise the real Dataviz core and call the handler ─────
    # Mirrors async/lifecycle_demo.py _run_live() — merged here so there is
    # a single demo file.  Requires the same env vars as the production consumer:
    #   DATABASE_URI, ADMIN_ACCOUNTS, EVENTBUS_* etc.
    if args.live:
        if not args.event_type:
            print("\n  ❌  --live requires --event-type.\n")
            sys.exit(1)

        account_id_live = args.account_id or "79fadc0d-90d9-45fe-9ab9-bbfbc4a2a28a"
        event_type_live = _SHORT_TO_FULL.get(args.event_type, args.event_type)
        if event_type_live not in _SCENARIO_TITLES:
            print(f"\n  ❌  Unknown event type: '{args.event_type}'\n")
            sys.exit(1)

        _live_log = logging.getLogger("lifecycle_demo.live")
        _live_log.info("Account Lifecycle EventBus Demo — LIVE MODE")
        _live_log.info("  account_id : %s", account_id_live)
        _live_log.info("  event_type : %s", event_type_live)

        try:
            from sg_cacert_file import load_sg_certs  # type: ignore
            load_sg_certs()
        except ImportError:
            _live_log.warning("sg_cacert_file not available — skipping cert load.")

        try:
            from dataviz_async import core as async_core  # type: ignore
            from dataviz_async.app import app             # type: ignore
            async_core.init_app(app)
            lifecycle_service = async_core.get_core(app).account_lifecycle_consumer
        except Exception as exc:
            _live_log.error("Failed to initialise core: %s", exc, exc_info=True)
            _live_log.error(
                "Make sure all required env vars are set "
                "(DATABASE_URI, ADMIN_ACCOUNTS, EVENTBUS_*, ...) "
                "and all services are reachable."
            )
            sys.exit(1)

        _live_log.info("Core initialised. Calling lifecycle handler...")
        _live_log.info(
            "  handle_event(event_type='%s', account_id='%s')",
            event_type_live, account_id_live,
        )
        _live_log.info("  business: %s", _SCENARIO_TITLES.get(event_type_live, "unknown"))

        result = lifecycle_service.handle_event(
            event_type=event_type_live,
            account_id=account_id_live,
            event_data={},
        )

        _live_log.info("── RESULT ──────────────────────────────────────────────")
        if isinstance(result, dict):
            if result.get("success"):
                _live_log.info("  ✅  success=True — lifecycle event handled OK.")
            else:
                _live_log.error("  ❌  success=False — reason: %s", result.get("message", "unknown"))
        else:
            _live_log.info("  handler returned: %s", result)

        sys.exit(0)

    # ── --dry-run flag ────────────────────────────────────────────────────────
    # Runs the full lifecycle action (deactivate / reactivate / delete …) using
    # hardcoded demo data and CloudEvents — exactly what the old --live did when
    # CCP was not available.  No real EventBus, no DB, no Celery needed.
    if args.dry_run:
        if not args.event_type:
            print("\n  ❌  --dry-run requires --event-type.\n")
            sys.exit(1)

        event_type_dr = _SHORT_TO_FULL.get(args.event_type, args.event_type)
        if event_type_dr not in _SCENARIO_TITLES:
            print(f"\n  ❌  Unknown event type: '{args.event_type}'\n")
            sys.exit(1)

        ACCOUNT_ID_DR  = args.account_id or "79fadc0d-90d9-45fe-9ab9-bbfbc4a2a28a"
        FQDN_SOURCE_DR = "ocs-uat.eu-fr-paris.cloud.socgen"

        # Wire up in-memory stubs (same as single-event mode)
        eb_client_dr   = Client(user="svc-dataviz", password="s3cr3t", region="eu-fr-paris")
        adapter_dr     = EventBusAdapter(eventbus_account=eb_client_dr, account_id=ACCOUNT_ID_DR, fqdn_source=FQDN_SOURCE_DR)
        account_repo_dr   = InMemoryAccountRepo()
        workspace_repo_dr = InMemoryWorkspaceRepo()
        account_svc_dr = MockAccountService(
            account_repo=account_repo_dr,
            workspace_repo=workspace_repo_dr,
            eventbus_adapter=adapter_dr,
        )
        executor_dr = MockWorkflowExecutor(account_service_ref=account_svc_dr)
        account_svc_dr.workflow_executor = executor_dr
        monitoring_dr = MockMonitoringService()
        account_svc_dr.set_monitoring_service(monitoring_dr)

        queue_dr = Queue(
            client=eb_client_dr,
            alias="dataviz-lifecycle-consumer",
            topic="lifecycle",
            routing_key=list(_SCENARIO_TITLES.keys()),
        )
        consumer_dr = Consumer(
            queue=queue_dr,
            callback=build_consumer_callback(account_svc_dr),
            auto_ack=True,
        )

        # Seed demo account + workspaces
        account_repo_dr.save(AccountDetails(owner_account_id=ACCOUNT_ID_DR, status=Status.ACTIVE))
        for ws_name_dr in ["grafana-prod", "grafana-dev", "grafana-staging"]:
            workspace_repo_dr.save(Workspace(name=ws_name_dr, owner_account_id=ACCOUNT_ID_DR, status=Status.ACTIVE))

        # Run the scenario (CloudEvent → consumer → handler → before/after state)
        demo_consumer_scenario(
            title          = _SCENARIO_TITLES[event_type_dr],
            event_type     = event_type_dr,
            account_id     = ACCOUNT_ID_DR,
            account_service= account_svc_dr,
            consumer       = consumer_dr,
            source_fqdn    = FQDN_SOURCE_DR,
        )

        if monitoring_dr.sent_alerts:
            print(f"\n  MonitoringService.sent_alerts ({len(monitoring_dr.sent_alerts)} total):")
            for i, alert in enumerate(monitoring_dr.sent_alerts, 1):
                print(f"    [{i}] level={alert['error_level']}  subject={alert['email_subject']}")

        sys.exit(0)

    # ── single-event mode  (--account-id + --event-type) ──────────────────────
    if args.event_type:
        # Normalise short → full form
        event_type = _SHORT_TO_FULL.get(args.event_type, args.event_type)
        if event_type not in _SCENARIO_TITLES:
            print(
                f"\n  ❌  Unknown event type: '{args.event_type}'\n"
                "\n"
                "  Supported values:\n"
                "      ResourceDisabled   /  LifecycleEvent.ResourceDisabled\n"
                "      ResourceActive     /  LifecycleEvent.ResourceActive\n"
                "      ResourceDeleting   /  LifecycleEvent.ResourceDeleting\n"
                "      ResourceDeleted    /  LifecycleEvent.ResourceDeleted\n"
            )
            sys.exit(1)

        ACCOUNT_ID  = args.account_id or "79fadc0d-90d9-45fe-9ab9-bbfbc4a2a28a"
        FQDN_SOURCE = "ocs-uat.eu-fr-paris.cloud.socgen"

        # Wire up stubs
        eb_client   = Client(user="svc-dataviz", password="s3cr3t", region="eu-fr-paris")
        adapter     = EventBusAdapter(eventbus_account=eb_client, account_id=ACCOUNT_ID, fqdn_source=FQDN_SOURCE)
        account_repo   = InMemoryAccountRepo()
        workspace_repo = InMemoryWorkspaceRepo()
        account_service = MockAccountService(account_repo=account_repo, workspace_repo=workspace_repo, eventbus_adapter=adapter)
        executor = MockWorkflowExecutor(account_service_ref=account_service)
        account_service.workflow_executor = executor
        queue    = Queue(client=eb_client, alias="dataviz-lifecycle-consumer", topic="lifecycle",
                         routing_key=list(_SCENARIO_TITLES.keys()))
        consumer = Consumer(queue=queue, callback=build_consumer_callback(account_service), auto_ack=True)

        # Seed with an account + workspaces
        account_repo.save(AccountDetails(owner_account_id=ACCOUNT_ID, status=Status.ACTIVE))
        for ws_name in ["grafana-prod", "grafana-dev", "grafana-staging"]:
            workspace_repo.save(Workspace(name=ws_name, owner_account_id=ACCOUNT_ID, status=Status.ACTIVE))

        demo_consumer_scenario(
            title          = _SCENARIO_TITLES[event_type],
            event_type     = event_type,
            account_id     = ACCOUNT_ID,
            account_service= account_service,
            consumer       = consumer,
            source_fqdn    = FQDN_SOURCE,
        )
        sys.exit(0)

    # ── default: full demo ─────────────────────────────────────────────────────
    main()
