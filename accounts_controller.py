from typing import Tuple, Dict, Any
from uuid import UUID

from flask import current_app, abort

from dataviz_api.core import get_core
from platform_api.permissions import get_current_account_id


# --------------------------------------------------------------------------
# Admin allowlist
# --------------------------------------------------------------------------
# Only these two accounts are permitted to invoke the mutating admin
# endpoints (deactivate / activate / delete). The same values should
# also be configured in the Core ADMIN_ACCOUNTS env var for defense
# in depth.
ALLOWED_ACCOUNT_IDS = {
    UUID("d3ac47ac-cc43-4da7-b935-d0c0b1d4c7b9"),
    UUID("3c24a85d-f148-485e-96a9-c21d47b42f54"),
}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _get_accounts_service(core: Any) -> Any:
    """Return the account service from core regardless of singular/plural naming."""
    service = getattr(core, "accounts", None) or getattr(core, "account", None)
    if service is None:
        raise RuntimeError("Account service is not available in core")
    return service


def _validate_allowed_account() -> UUID:
    """
    Block the request unless the logged-in account is one of the two
    hardcoded admin accounts. Returns the caller's UUID on success.
    """
    caller_account_id = get_current_account_id()
    try:
        caller_uuid = UUID(str(caller_account_id))
    except (ValueError, TypeError):
        abort(403, description="You are not authorized to perform this action")

    if caller_uuid not in ALLOWED_ACCOUNT_IDS:
        abort(403, description="You are not authorized to perform this action")

    return caller_uuid


def _parse_uuid(value: Any, field_name: str) -> UUID:
    """Helper to parse UUIDs with a clear error message."""
    try:
        return UUID(str(value))
    except Exception:
        raise ValueError(f"Invalid UUID provided for '{field_name}'")


def _get_target_account_id(kwargs: Dict[str, Any]) -> UUID:
    """
    Extract the target account id from the path parameter. The path
    parameter is named 'target_account_id' (not 'account_id') so that
    it does not collide with the caller 'account_id' injected by the
    auth middleware into kwargs.
    """
    raw = kwargs.get("target_account_id")
    if raw is None:
        raise ValueError("Missing required path parameter 'target_account_id'")
    return _parse_uuid(raw, "target_account_id")


def _account_to_response(account: Any) -> Dict[str, Any]:
    """
    Build the API response dict from an AccountDetails object returned
    by Core. AccountDetails.status is a Status enum so we serialize
    its .value as a string.
    """
    raw_status = getattr(account, "status", None)
    if raw_status is None:
        status = "UNKNOWN"
    elif hasattr(raw_status, "value"):
        status = str(raw_status.value)
    else:
        status = str(raw_status)

    response: Dict[str, Any] = {
        "id": str(getattr(account, "id", "")),
        "status": status,
    }
    owner = getattr(account, "owner_account_id", None)
    if owner is not None:
        response["owner_account_id"] = str(owner)
    name = getattr(account, "name", None)
    if name:
        response["name"] = name
    return response


def _handle_core_exception(e: Exception) -> Tuple[Dict[str, Any], int]:
    """
    Map Core exceptions to HTTP status codes. Matched by class name so
    the Core exceptions do not need to be imported here.
    """
    name = type(e).__name__
    if name == "NotOwnerError":
        return {"error": str(e) or "Not authorized"}, 403
    if name == "AccountNotFoundException":
        return {"error": str(e) or "Account not found"}, 404
    if name == "AccountNotInActiveException":
        return {"error": str(e) or "Account is not in the required state"}, 409
    return {"error": str(e) or "Unexpected error"}, 500


# --------------------------------------------------------------------------
# Admin lifecycle endpoints
# --------------------------------------------------------------------------
def account_deactivate(**kwargs: Any) -> Tuple[Dict[str, Any], int]:
    """
    PATCH /admin/v1/accounts/{target_account_id}/deactivate

    Requests deactivation of the target account. The admin caller is
    identified from the auth token and must be in ALLOWED_ACCOUNT_IDS.
    """
    core = get_core(current_app)

    try:
        # Admin gate (first line of defense)
        caller_account_id = _validate_allowed_account()

        accounts_service = _get_accounts_service(core)

        target_owner_account_id = _get_target_account_id(kwargs)

        # Core contract:
        #   owner_account_id -> target account being acted on
        #   account_id       -> admin caller (checked against ADMIN_ACCOUNTS)
        account = accounts_service.request_account_deactivation(
            owner_account_id=target_owner_account_id,
            account_id=caller_account_id,
        )

        return _account_to_response(account), 202

    except ValueError as e:
        return {"error": str(e)}, 400
    except Exception as e:
        return _handle_core_exception(e)


def account_activate(**kwargs: Any) -> Tuple[Dict[str, Any], int]:
    """
    PATCH /admin/v1/accounts/{target_account_id}/activate

    Requests reactivation of the target account. The admin caller is
    identified from the auth token and must be in ALLOWED_ACCOUNT_IDS.
    """
    core = get_core(current_app)

    try:
        caller_account_id = _validate_allowed_account()

        accounts_service = _get_accounts_service(core)

        target_owner_account_id = _get_target_account_id(kwargs)

        account = accounts_service.request_account_reactivation(
            owner_account_id=target_owner_account_id,
            account_id=caller_account_id,
        )

        return _account_to_response(account), 202

    except ValueError as e:
        return {"error": str(e)}, 400
    except Exception as e:
        return _handle_core_exception(e)


def account_delete(**kwargs: Any) -> Tuple[Dict[str, Any], int]:
    """
    DELETE /admin/v1/accounts/{target_account_id}

    Requests deletion of the target account and all its associated
    workspaces. The admin caller is identified from the auth token
    and must be in ALLOWED_ACCOUNT_IDS.
    """
    core = get_core(current_app)

    try:
        caller_account_id = _validate_allowed_account()

        accounts_service = _get_accounts_service(core)

        target_owner_account_id = _get_target_account_id(kwargs)

        account = accounts_service.request_account_deletion(
            owner_account_id=target_owner_account_id,
            account_id=caller_account_id,
        )

        return _account_to_response(account), 202

    except ValueError as e:
        return {"error": str(e)}, 400
    except Exception as e:
        return _handle_core_exception(e)


def account_status(**kwargs: Any) -> Tuple[Dict[str, Any], int]:
    """
    GET /admin/v1/accounts/{target_account_id}/status

    Returns the current lifecycle status of the specified account.
    Available to any authenticated user (no admin gate).

    Uses get_by_owner_id (which queries the Dataviz DB directly)
    rather than get_account_details_by_id (which calls the external
    account platform client and can return the caller's own data
    regardless of the requested id).
    """
    core = get_core(current_app)

    try:
        accounts_service = _get_accounts_service(core)

        target_account_id = _get_target_account_id(kwargs)

        # Look up the account by owner_account_id directly in the
        # Dataviz data store. Raises AccountNotFoundException if the
        # id does not match any existing account.
        account = accounts_service.get_by_owner_id(str(target_account_id))

        return _account_to_response(account), 200

    except ValueError as e:
        return {"error": str(e)}, 400
    except Exception as e:
        return _handle_core_exception(e)
