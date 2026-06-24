#!/usr/bin/env python3
"""
Demo: Simulate an Account Lifecycle EventBus event without a real EventBus connection.

Builds a valid CloudEvent payload, runs it through the full consumer
parsing/validation pipeline, then calls the lifecycle handler directly.

Usage (dry-run — shows routing without touching the database):
    python lifecycle_demo.py --account-id <uuid> --event-type ResourceDisabled
    python lifecycle_demo.py --account-id <uuid> --event-type ResourceActive
    python lifecycle_demo.py --account-id <uuid> --event-type ResourceDeleting
    python lifecycle_demo.py --account-id <uuid> --event-type ResourceDeleted

Usage (live — calls real core/database):
    python lifecycle_demo.py --account-id <uuid> --event-type ResourceDisabled --live

Usage (ccp-live — calls real core via CCP deployment format):
    python lifecycle_demo.py --account-id <owner-uuid> --event-type ResourceCreating --ccp-live
    python lifecycle_demo.py --account-id <owner-uuid> --event-type ResourceReady --ccp-live
    python lifecycle_demo.py --account-id <owner-uuid> --event-type ResourceDisabled --ccp-live

    --account-id here is the OWNER ACCOUNT UUID (the UUID part of the ownerId SRN).
    e.g. for ownerId "srn:sgcp:account.cloud.socgen:account:cf37e39d-1775-4897-8076-36cf20282234"
         use --account-id cf37e39d-1775-4897-8076-36cf20282234

Supported event types (short or full form both accepted):
    ResourceDisabled   /  LifecycleEvent.ResourceDisabled
    ResourceActive     /  LifecycleEvent.ResourceActive
    ResourceDeleting   /  LifecycleEvent.ResourceDeleting
    ResourceDeleted    /  LifecycleEvent.ResourceDeleted
    ResourceCreating   /  LifecycleEvent.ResourceCreating
    ResourceReady      /  LifecycleEvent.ResourceReady
"""

import argparse
import datetime
import json
import logging
import sys
import uuid

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("lifecycle_demo")

# ---------------------------------------------------------------------------
# Supported event types
# ---------------------------------------------------------------------------
EVENT_TYPE_MAP = {
    "resourcedisabled": "LifecycleEvent.ResourceDisabled",
    "resourceactive": "LifecycleEvent.ResourceActive",
    "resourcedeleting": "LifecycleEvent.ResourceDeleting",
    "resourcedeleted": "LifecycleEvent.ResourceDeleted",
    "resourcecreating": "LifecycleEvent.ResourceCreating",
    "resourceready": "LifecycleEvent.ResourceReady",
}

BUSINESS_ACTION = {
    "LifecycleEvent.ResourceDisabled": "Shutdown linked workspaces (grace period starts)",
    "LifecycleEvent.ResourceActive":   "Reactivate linked workspaces (grace period cancelled)",
    "LifecycleEvent.ResourceDeleting": "Delete workspace resources (grace period over)",
    "LifecycleEvent.ResourceDeleted":  "Final cleanup verification + notification if residual resources remain",
    "LifecycleEvent.ResourceCreating": "Provision new CCP resource and register with linked workspaces",
    "LifecycleEvent.ResourceReady":    "Mark CCP resource as ready and notify linked workspaces",
}


def _resolve_event_type(raw: str) -> str:
    """Accept both short form (ResourceDisabled) and full form (LifecycleEvent.ResourceDisabled)."""
    normalised = raw.lower().replace("lifecycleevent.", "").replace("_", "")
    resolved = EVENT_TYPE_MAP.get(normalised)
    if resolved is None:
        logger.error(
            "Unknown event type '%s'. Supported: %s",
            raw,
            ", ".join(EVENT_TYPE_MAP.values()),
        )
        sys.exit(1)
    return resolved


def _build_ccp_cloudevent(account_id: str, event_type: str) -> dict:
    """
    Build a CloudEvent in the CCP (ccp.cloud.socgen) format.

    This matches the real event shape produced when the application is deployed
    on CCP via lifecycle_consumer.py / ccp_config_lifecycle.yaml.

    Key differences from the standard EventBus format:
      - specversion is "1.0.0"
      - Extension attribute keys are lowercase (resourceowner, resourceservice, …)
      - source / resourceservice are "ccp.cloud.socgen"
      - dataschema points to the CCP schema URI
      - data.resource carries the full CCP resource payload including the ownerId SRN:
            srn:sgcp:account.cloud.socgen:account:<account_id>

    The owner account UUID (account_id) is embedded in the ownerId SRN field;
    it is extracted at handler time via split(":").
    """
    resource_id = str(uuid.uuid4())
    now = datetime.datetime.utcnow()
    timestamp = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    lifecycle_status = event_type.split(".")[-1].lower().replace("resource", "")

    return {
        # ── CloudEvent core attributes (CCP format) ─────────────────────────
        "specversion":      "1.0.0",
        "id":               str(uuid.uuid4()),
        "time":             now.strftime("%Y-%m-%dT%H:%M:%S.%f") + "+00:00",
        "type":             event_type,
        "source":           "ccp.cloud.socgen",
        "subject":          resource_id,
        "datacontenttype":  "application/hal+json",
        "dataschema":       "srn:sgcp:schema:resource:json-schema:1.0.0",

        # ── CloudEvent extension attributes (lowercase — CCP convention) ────
        "resourceowner":    account_id,
        "resourceservice":  "ccp.cloud.socgen",
        "resourcetype":     "Artifact",
        "resourcesubtype":  "None",

        # ── Event payload (CCP resource structure) ──────────────────────────
        "data": {
            "resource": {
                "timestamp":        timestamp,
                "creationDate":     timestamp,
                "modificationDate": timestamp,
                "tags": [
                    f"sgcp:beneficiary={account_id}",
                ],
                "id":              resource_id,
                "globalId":        f"srn:sgcp:ccp.cloud.socgen:artifact:{resource_id}",
                "resourceType":    "artifact",
                "resourceSubType": "None",
                "serviceOffer":    "None",
                # ownerId SRN — the UUID after the last ":" is the owner account ID
                "ownerId":         f"srn:sgcp:account.cloud.socgen:account:{account_id}",
                "serviceId":       "srn:sgcp:refdata:service-instance:ccp.cloud.socgen",
                "location":        "global",
                "lifecycleStatus": lifecycle_status,
                "properties":      {},
                "_links": {
                    "self": {
                        "href": f"srn:sgcp:ccp.cloud.socgen:artifact:{resource_id}"
                    },
                    "sgcp:isOwnedBy": {
                        "href": f"srn:sgcp:account.cloud.socgen:account:{account_id}"
                    },
                },
            }
        },
    }


def _build_cloudevent(account_id: str, event_type: str) -> dict:
    """
    Build a valid CloudEvent dict that matches the official EventBus format.

    Header fields follow the CloudEvent 1.0 specification.
    Extension fields use official camelCase names.
    data.resource is populated with the minimum fields the consumer expects.
    """
    return {
        # ── CloudEvent core attributes ──────────────────────────────────────
        "specversion":     "1.0",
        "id":              str(uuid.uuid4()),
        "time":            datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "type":            event_type,
        "source":          "demo.lifecycle.local",
        "subject":         account_id,                # account UUID — primary ID source
        "datacontenttype": "application/hal+json",
        "dataschema":      "srn:sgcp:refdata:schema:schema-resource-json-1.0.0",

        # ── CloudEvent extension attributes ────────────────────────────────
        "resourceOwner":   account_id,
        "resourceService": "demo.lifecycle.local",
        "resourceType":    "account",
        "resourceSubType": "",

        # ── Event payload ──────────────────────────────────────────────────
        "data": {
            "resource": {
                "id":                account_id,
                "owner_account_id":  account_id,
                "status":            event_type.split(".")[-1],
            }
        },
    }


def _print_event_banner(event: dict) -> None:
    logger.info("=" * 64)
    logger.info("DEMO CloudEvent payload")
    logger.info("=" * 64)
    logger.info("%s", json.dumps(event, indent=2))
    logger.info("=" * 64)


# ---------------------------------------------------------------------------
# Dry-run: uses the same parsing helpers as the real consumer
# ---------------------------------------------------------------------------
def _run_dry(event: dict, event_type: str, account_id: str) -> None:
    """
    Exercise the validation and extraction pipeline without a real core.
    Useful to prove the CloudEvent will be accepted and routed correctly.
    """
    # Import only the pure helper functions — no Celery / DB needed
    sys.path.insert(0, ".")
    try:
        from dataviz_async.app import (
            _extract_event_fields,
            _validate_lifecycle_event,
            _extract_account_id,
        )
    except ImportError:
        # Fallback: inline minimal equivalents so the demo works standalone
        logger.warning(
            "Could not import from dataviz_async.app — using inline equivalents for dry-run."
        )
        _extract_event_fields   = lambda e: (e.get("type", ""), e.get("data", {}))
        _validate_lifecycle_event = lambda e, t, d: (True, "")
        _extract_account_id     = lambda e, d: e.get("subject") or e.get("resourceOwner")

    extracted_type, extracted_data = _extract_event_fields(event)
    is_valid, reason = _validate_lifecycle_event(event, extracted_type, extracted_data)
    extracted_id = _extract_account_id(event, extracted_data)

    logger.info("── PARSE RESULTS ──────────────────────────────────────")
    logger.info("  event_type  : %s", extracted_type)
    logger.info("  account_id  : %s", extracted_id)
    logger.info("  valid       : %s", is_valid)
    if not is_valid:
        logger.error("  reason      : %s", reason)
        logger.error("Event would be REJECTED by consumer.")
        sys.exit(1)

    logger.info("── ROUTING ────────────────────────────────────────────")
    logger.info("  handler     : AccountService.handle_event('%s', account_id='%s')", extracted_type, extracted_id)
    logger.info("  business    : %s", BUSINESS_ACTION.get(event_type, "unknown"))
    logger.info("── DRY-RUN COMPLETE — event would be accepted and routed correctly ──")


# ---------------------------------------------------------------------------
# CCP-live-run: initialises real core using CCP event format and fires the handler
# ---------------------------------------------------------------------------
def _run_ccp_live(event: dict, event_type: str, account_id: str) -> None:
    """
    Initialise the real Dataviz core and call the lifecycle handler using the
    CCP deployment format (as configured by lifecycle_consumer.py /
    ccp_config_lifecycle.yaml).

    The owner account ID is extracted from the ownerId SRN stored in
    event["data"]["resource"]["ownerId"]  —  the UUID is the last segment
    after splitting on ":".

    Requires the same environment variables as the CCP production consumer.
    """
    logger.info("Initialising Dataviz core (ccp-live mode)...")

    try:
        from sg_cacert_file import load_sg_certs
        load_sg_certs()
    except ImportError:
        logger.warning("sg_cacert_file not available — skipping cert load.")

    try:
        from dataviz_async import core as async_core
        from dataviz_async.app import app
        async_core.init_app(app)
        lifecycle_service = async_core.get_core(app).account_lifecycle_consumer
    except Exception as exc:
        logger.error("Failed to initialise core: %s", exc, exc_info=True)
        logger.error(
            "Make sure all required environment variables are set "
            "(DATABASE_URI, ADMIN_ACCOUNTS, etc.) and services are reachable."
        )
        sys.exit(1)

    # Extract owner account UUID from the ownerId SRN in the CCP event payload.
    # Format: "srn:sgcp:account.cloud.socgen:account:<uuid>"  →  last ":" segment.
    owner_id_srn = event.get("data", {}).get("resource", {}).get("ownerId", "")
    extracted_account_id = owner_id_srn.split(":")[-1] if owner_id_srn else account_id

    logger.info("Core initialised. Calling lifecycle handler (CCP mode)...")
    logger.info("── CCP HANDLER CALL ────────────────────────────────────")
    logger.info("  ownerId SRN : %s", owner_id_srn)
    logger.info("  account_id  : %s", extracted_account_id)
    logger.info("  event_type  : %s", event_type)
    logger.info("  business    : %s", BUSINESS_ACTION.get(event_type, "unknown"))

    result = lifecycle_service.handle_event(
        event_type=event_type,
        account_id=extracted_account_id,
        event_data=event.get("data", {}),
    )

    logger.info("── RESULT ──────────────────────────────────────────────")
    if isinstance(result, dict):
        logger.info("  success : %s", result.get("success"))
        if not result.get("success"):
            logger.error("  reason  : %s", result.get("reason", "unknown"))
        else:
            logger.info("  Lifecycle event handled successfully (CCP mode).")
    else:
        logger.warning("  Unexpected result type from handle_event: %s", type(result))


# ---------------------------------------------------------------------------
# Live-run: initialises real core and fires the handler
# ---------------------------------------------------------------------------
def _run_live(event: dict, event_type: str, account_id: str) -> None:
    """
    Initialise the real Dataviz core and call the lifecycle handler directly.
    Uses the CCP event format (new.json) — owner account ID is extracted from
    the ownerId SRN in event["data"]["resource"]["ownerId"].
    Requires the same environment variables as the production consumer.
    """
    logger.info("Initialising Dataviz core (live mode)...")

    try:
        from sg_cacert_file import load_sg_certs
        load_sg_certs()
    except ImportError:
        logger.warning("sg_cacert_file not available — skipping cert load.")

    try:
        from dataviz_async import core as async_core
        from dataviz_async.app import app
        async_core.init_app(app)
        lifecycle_service = async_core.get_core(app).account_lifecycle_consumer
    except Exception as exc:
        logger.error("Failed to initialise core: %s", exc, exc_info=True)
        logger.error(
            "Make sure all required environment variables are set "
            "(DATABASE_URI, ADMIN_ACCOUNTS, etc.) and services are reachable."
        )
        sys.exit(1)

    # Extract owner account UUID from the ownerId SRN in the CCP event payload.
    # Format: "srn:sgcp:account.cloud.socgen:account:<uuid>"  →  last ":" segment.
    owner_id_srn = event.get("data", {}).get("resource", {}).get("ownerId", "")
    extracted_account_id = owner_id_srn.split(":")[-1] if owner_id_srn else account_id

    logger.info("Core initialised. Calling lifecycle handler...")
    logger.info("── HANDLER CALL ────────────────────────────────────────")
    logger.info("  ownerId SRN : %s", owner_id_srn)
    logger.info("  account_id  : %s", extracted_account_id)
    logger.info("  event_type  : %s", event_type)
    logger.info("  business    : %s", BUSINESS_ACTION.get(event_type, "unknown"))

    result = lifecycle_service.handle_event(
        event_type=event_type,
        account_id=extracted_account_id,
        event_data=event.get("data", {}),
    )

    logger.info("── RESULT ──────────────────────────────────────────────")
    if isinstance(result, dict):
        logger.info("  success : %s", result.get("success"))
        if not result.get("success"):
            logger.error("  reason  : %s", result.get("reason", "unknown"))
        else:
            logger.info("  Lifecycle event handled successfully.")
    else:
        logger.warning("  Unexpected result type from handle_event: %s", type(result))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Demo: simulate an Account Lifecycle EventBus event.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--account-id",
        required=True,
        metavar="UUID",
        help="Account UUID to use as the event subject (the account to act on).",
    )
    parser.add_argument(
        "--event-type",
        required=True,
        metavar="EVENT",
        help=(
            "Lifecycle event type. Supported: ResourceDisabled, ResourceActive, "
            "ResourceDeleting, ResourceDeleted (short or full LifecycleEvent.* form)."
        ),
    )
    parser.add_argument(
        "--live",
        action="store_true",
        default=False,
        help="Call the real core lifecycle handler (requires DB and env vars). "
             "Default is dry-run (validates and routes without touching the database).",
    )
    parser.add_argument(
        "--ccp-live",
        action="store_true",
        default=False,
        help=(
            "Call the real core lifecycle handler using the CCP event format "
            "(as deployed via lifecycle_consumer.py / ccp_config_lifecycle.yaml). "
            "--account-id must be the OWNER ACCOUNT UUID "
            "(the UUID part of the ownerId SRN, e.g. cf37e39d-1775-4897-8076-36cf20282234). "
            "Requires DB and env vars. Cannot be combined with --live."
        ),
    )

    args = parser.parse_args()

    # Validate account-id
    try:
        uuid.UUID(args.account_id)
    except ValueError:
        logger.error("--account-id '%s' is not a valid UUID.", args.account_id)
        sys.exit(1)

    if args.live and args.ccp_live:
        logger.error("--live and --ccp-live are mutually exclusive. Choose one.")
        sys.exit(1)

    event_type = _resolve_event_type(args.event_type)

    if args.ccp_live:
        mode_label = "CCP-LIVE (real core, CCP format)"
    elif args.live:
        mode_label = "LIVE (real core)"
    else:
        mode_label = "DRY-RUN (no DB)"

    logger.info("Account Lifecycle EventBus Demo")
    logger.info("  account_id : %s", args.account_id)
    logger.info("  event_type : %s", event_type)
    logger.info("  mode       : %s", mode_label)

    if args.ccp_live or args.live:
        event = _build_ccp_cloudevent(args.account_id, event_type)
    else:
        event = _build_cloudevent(args.account_id, event_type)

    _print_event_banner(event)

    if args.ccp_live:
        _run_ccp_live(event, event_type, args.account_id)
    elif args.live:
        _run_live(event, event_type, args.account_id)
    else:
        _run_dry(event, event_type, args.account_id)


if __name__ == "__main__":
    main()
