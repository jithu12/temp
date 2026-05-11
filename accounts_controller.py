import os
from typing import Tuple, Dict, Any
from uuid import UUID

from werkzeug.exceptions import HTTPException
from flask import current_app, abort

from dataviz_api.core import get_core
from platform_api.permissions import get_current_account_id


# --------------------------------------------------------------------------
# Admin accounts configuration
# --------------------------------------------------------------------------
_ADMIN_ACCOUNT_1 = "3c24a85d-f148-485e-96a9-c21d47b42f54"
_ADMIN_ACCOUNT_2 = "d3ac47ac-cc43-4da7-b935-d0c0b1d4c7b9"

os.environ.setdefault(
    "ADMIN_ACCOUNTS",
    f'["{_ADMIN_ACCOUNT_1}","{_ADMIN_ACCOUNT_2}"]',
)

ALLOWED_ACCOUNT_IDS = {
    UUID(_ADMIN_ACCOUNT_1),
    UUID(_ADMIN_ACCOUNT_2),
}

# Valid status transitions via the PATCH endpoint
ALLOWED_STATUS_TRANSITIONS = {"ACTIVE", "INACTIVE"}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _get_accounts_service(core: Any) -> Any:
    service = getattr(core, "accounts", None) or getattr(core, "account", None)
    if service is None:
        raise RuntimeError("Account service is not available in core")
    return service


def _validate_allowed_account() -> UUID:
    caller_account_id = get_current_account_id()
    try:
        caller_uuid = UUID(str(caller_account_id))
    except (ValueError, TypeError):
        abort(403, description="You are not authorized to perform this action")

    if caller_uuid not in ALLOWED_ACCOUNT_IDS:
        abort(403, description="You are not authorized to perform this action")

    return caller_uuid


def _parse_uuid(value: Any, field_name: str) -> UUID:
    try:
        return UUID(str(value))
    except Exception:
        raise ValueError(
            f"Invalid UUID provided for '{field_name}'. "
            f"Expected format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
        )


def _get_target_account_id(kwargs: Dict[str, Any]) -> UUID:
    raw = kwargs.get("target_account_id")
    if raw is None:
        raise ValueError(
            "Missing required path parameter 'target_account_id'."
        )
    return _parse_uuid(raw, "target_account_id")


def _get_caller_account_id(kwargs: Dict[str, Any]) -> UUID:
    raw = kwargs.get("account_id")
    if raw is None:
        raise ValueError("Missing caller account_id from token")
    return _parse_uuid(raw, "account_id")


def _get_account_status(account: Any) -> str:
    raw_status = getattr(account, "status", None)
    if raw_status is None:
        return "UNKNOWN"
    if hasattr(raw_status, "value"):
        return str(raw_status.value)
    return str(raw_status)


def _account_to_response(account: Any) -> Dict[str, Any]:
    response: Dict[str, Any] = {
        "id": str(getattr(account, "id", "")),
        "status": _get_account_status(account),
    }
    owner = getattr(account, "owner_account_id", None)
    if owner is not None:
        response["owner_account_id"] = str(owner)
    name = getattr(account, "name", None)
    if name:
        response["name"] = name
    return response


def _check_account_exists(accounts_service: Any, owner_account_id: UUID) -> Any:
    try:
        return accounts_service.get_by_owner_id(str(owner_account_id))
    except Exception as e:
        if type(e).__name__ == "AccountNotFoundException":
            raise
        raise


def _handle_core_exception(e: Exception) -> Tuple[Dict[str, Any], int]:
    name = type(e).__name__

    if name == "NotOwnerError":
        return {
            "error": "You are not authorized to perform admin operations.",
            "code": "ADMIN_ACCESS_REQUIRED",
        }, 403

    if name == "AccountNotFoundException":
        return {
            "error": "Account not found. Please check the owner_account_id and try again.",
            "code": "ACCOUNT_NOT_FOUND",
        }, 404

    if name == "AccountNotInActiveException":
        return {
            "error": "Account is not in the required state for this operation.",
            "code": "ACCOUNT_INVALID_STATE",
        }, 409

    return {
        "error": f"An unexpected error occurred: {str(e)}",
        "code": "INTERNAL_ERROR",
    }, 500


# --------------------------------------------------------------------------
# Pre-flight status checks
# --------------------------------------------------------------------------
def _assert_account_is_active(account: Any, owner_account_id: UUID) -> None:
    status = _get_account_status(account)
    if status == "INACTIVE":
        raise ValueError(
            f"ACCOUNT_ALREADY_INACTIVE:"
            f"Account '{owner_account_id}' is already inactive. "
            f"No changes were made."
        )
    if status == "DELETED":
        raise ValueError(
            f"ACCOUNT_ALREADY_DELETED:"
            f"Account '{owner_account_id}' has already been deleted. "
            f"No changes were made."
        )
    if status != "ACTIVE":
        raise ValueError(
            f"ACCOUNT_INVALID_STATE:"
            f"Account '{owner_account_id}' is in state '{status}' "
            f"and cannot be deactivated. Only ACTIVE accounts can be deactivated."
        )


def _assert_account_is_inactive(account: Any, owner_account_id: UUID) -> None:
    status = _get_account_status(account)
    if status == "ACTIVE":
        raise ValueError(
            f"ACCOUNT_ALREADY_ACTIVE:"
            f"Account '{owner_account_id}' is already active. "
            f"No changes were made."
        )
    if status == "DELETED":
        raise ValueError(
            f"ACCOUNT_ALREADY_DELETED:"
            f"Account '{owner_account_id}' has been deleted and cannot be reactivated. "
            f"No changes were made."
        )
    if status != "INACTIVE":
        raise ValueError(
            f"ACCOUNT_INVALID_STATE:"
            f"Account '{owner_account_id}' is in state '{status}' "
            f"and cannot be reactivated. Only INACTIVE accounts can be reactivated."
        )


def _assert_account_is_deletable(account: Any, owner_account_id: UUID) -> None:
    status = _get_account_status(account)
    if status == "DELETED":
        raise ValueError(
            f"ACCOUNT_ALREADY_DELETED:"
            f"Account '{owner_account_id}' has already been deleted. "
            f"No changes were made."
        )
    if status not in ("ACTIVE", "INACTIVE"):
        raise ValueError(
            f"ACCOUNT_INVALID_STATE:"
            f"Account '{owner_account_id}' is in state '{status}' "
            f"and cannot be deleted. Only ACTIVE or INACTIVE accounts can be deleted."
        )


def _handle_state_error(e: ValueError) -> Tuple[Dict[str, Any], int]:
    message = str(e)
    code_map = {
        "ACCOUNT_ALREADY_INACTIVE": (
            "Account is already inactive. No changes were made.", 409
        ),
        "ACCOUNT_ALREADY_ACTIVE": (
            "Account is already active. No changes were made.", 409
        ),
        "ACCOUNT_ALREADY_DELETED": (
            "Account has been deleted and cannot be modified.", 409
        ),
        "ACCOUNT_INVALID_STATE": (
            "Account is not in the required state for this operation.", 409
        ),
    }
    for prefix, (friendly_message, status_code) in code_map.items():
        if message.startswith(f"{prefix}:"):
            detail = message.split(":", 1)[1].strip()
            return {
                "error": friendly_message,
                "detail": detail,
                "code": prefix,
            }, status_code
    return {"error": message, "code": "VALIDATION_ERROR"}, 400


# --------------------------------------------------------------------------
# Admin lifecycle endpoints
# --------------------------------------------------------------------------
def account_update_status(**kwargs: Any) -> Tuple[Dict[str, Any], int]:
    """
    PATCH /admin/v1/accounts/{target_account_id}

    Updates account status to ACTIVE or INACTIVE.
    Cascades the change to all associated workspaces.

    Request body:
        { "status": "INACTIVE" } -> deactivates the account and all workspaces
        { "status": "ACTIVE" }   -> reactivates the account and all workspaces
    """
    core = get_core(current_app)

    try:
        _validate_allowed_account()

        accounts_service = _get_accounts_service(core)
        target_owner_account_id = _get_target_account_id(kwargs)
        caller_account_id = _get_caller_account_id(kwargs)

        # Get desired status from request body
        body = kwargs.get("body") or {}
        desired_status = body.get("status", "").upper()

        if desired_status not in ALLOWED_STATUS_TRANSITIONS:
            return {
                "error": (
                    f"Invalid status '{desired_status}'. "
                    f"Must be one of: {', '.join(sorted(ALLOWED_STATUS_TRANSITIONS))}"
                ),
                "code": "INVALID_STATUS",
            }, 400

        # Pre-flight check
        account = _check_account_exists(accounts_service, target_owner_account_id)

        if desired_status == "INACTIVE":
            _assert_account_is_active(account, target_owner_account_id)
            account = accounts_service.request_account_deactivation(
                owner_account_id=target_owner_account_id,
                account_id=caller_account_id,
            )

        elif desired_status == "ACTIVE":
            _assert_account_is_inactive(account, target_owner_account_id)
            account = accounts_service.request_account_reactivation(
                owner_account_id=target_owner_account_id,
                account_id=caller_account_id,
            )

        return _account_to_response(account), 202

    except ValueError as e:
        return _handle_state_error(e)
    except HTTPException:
        raise
    except Exception as e:
        return _handle_core_exception(e)


def account_delete(**kwargs: Any) -> Tuple[Dict[str, Any], int]:
    """
    DELETE /admin/v1/accounts/{target_account_id}

    Deletes the target account and all its associated workspaces.
    Soft delete — records remain in DB with status DELETED.
    """
    core = get_core(current_app)

    try:
        _validate_allowed_account()

        accounts_service = _get_accounts_service(core)
        target_owner_account_id = _get_target_account_id(kwargs)
        caller_account_id = _get_caller_account_id(kwargs)

        account = _check_account_exists(accounts_service, target_owner_account_id)
        _assert_account_is_deletable(account, target_owner_account_id)

        account = accounts_service.request_account_deletion(
            owner_account_id=target_owner_account_id,
            account_id=caller_account_id,
        )

        return _account_to_response(account), 202

    except ValueError as e:
        return _handle_state_error(e)
    except HTTPException:
        raise
    except Exception as e:
        return _handle_core_exception(e)


def account_status(**kwargs: Any) -> Tuple[Dict[str, Any], int]:
    """
    GET /admin/v1/accounts/{target_account_id}/status

    Returns the current lifecycle status of the specified account.
    Available to any authenticated user (no admin gate).
    """
    core = get_core(current_app)

    try:
        accounts_service = _get_accounts_service(core)
        target_account_id = _get_target_account_id(kwargs)

        account = accounts_service.get_by_owner_id(str(target_account_id))

        return _account_to_response(account), 200

    except ValueError as e:
        return _handle_state_error(e)
    except HTTPException:
        raise
    except Exception as e:
        return _handle_core_exception(e)
