Got you bro — I’ll give you a **clean full version** of your file with:

✅ Hardcoded allowed accounts
✅ Central validation
✅ Clean structure
✅ Applied to all APIs

---

# 🚀 FULL CLEAN CODE

```python
from typing import Tuple, Dict, Any
from types import SimpleNamespace
from uuid import UUID

from flask import current_app

from dataviz_api.core import get_core


# ✅ HARD CODED ALLOWED ACCOUNTS
ALLOWED_ACCOUNT_IDS = {
    UUID("11111111-2222-3333-4444-555555555555"),
    UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
}


# ✅ GET SERVICE
def _get_accounts_service(core: Any) -> Any:
    service = getattr(core, "accounts", None) or getattr(core, "account", None)
    if service is None:
        raise RuntimeError("Account service is not available in core")
    return service


# ✅ UUID PARSER
def _parse_uuid(value: Any, field_name: str) -> UUID:
    try:
        return UUID(str(value))
    except Exception:
        raise ValueError(f"Invalid UUID provided for '{field_name}'")


# ✅ ACCESS VALIDATION
def _validate_account_access(account_id: UUID):
    if account_id not in ALLOWED_ACCOUNT_IDS:
        raise PermissionError(f"Account {account_id} is not allowed to perform this action")


# =========================================================
# 🔻 DEACTIVATE ACCOUNT
# =========================================================
def account_deactivate(**kwargs: Any) -> Tuple[Dict[str, Any], int]:
    core = get_core(current_app)

    try:
        accounts_service = _get_accounts_service(core)

        owner_account_id = _parse_uuid(kwargs.get("owner_account_id"), "owner_account_id")
        account_id = _parse_uuid(kwargs.get("account_id"), "account_id")

        # ✅ VALIDATION
        _validate_account_access(owner_account_id)
        _validate_account_access(account_id)

        account = accounts_service.request_account_deactivation(
            owner_account_id=owner_account_id,
            account_id=account_id,
        )

        return accounts_service.to_dict(account), 200

    except PermissionError as e:
        return {"error": str(e)}, 403
    except Exception as e:
        return {"error": str(e)}, 400


# =========================================================
# 🔻 ACTIVATE ACCOUNT
# =========================================================
def account_activate(**kwargs: Any) -> Tuple[Dict[str, Any], int]:
    core = get_core(current_app)

    try:
        accounts_service = _get_accounts_service(core)

        owner_account_id = _parse_uuid(kwargs.get("owner_account_id"), "owner_account_id")
        account_id = _parse_uuid(kwargs.get("account_id"), "account_id")

        # ✅ VALIDATION
        _validate_account_access(owner_account_id)
        _validate_account_access(account_id)

        account = accounts_service.request_account_reactivation(
            owner_account_id=owner_account_id,
            account_id=account_id,
        )

        return accounts_service.to_dict(account), 200

    except PermissionError as e:
        return {"error": str(e)}, 403
    except Exception as e:
        return {"error": str(e)}, 400


# =========================================================
# 🔻 DELETE ACCOUNT
# =========================================================
def account_delete(**kwargs: Any) -> Tuple[Dict[str, Any], int]:
    core = get_core(current_app)

    try:
        accounts_service = _get_accounts_service(core)

        owner_account_id = _parse_uuid(kwargs.get("owner_account_id"), "owner_account_id")
        account_id = _parse_uuid(kwargs.get("account_id"), "account_id")

        # ✅ VALIDATION
        _validate_account_access(owner_account_id)
        _validate_account_access(account_id)

        account = accounts_service.request_account_deletion(
            owner_account_id=owner_account_id,
            account_id=account_id,
        )

        return accounts_service.to_dict(account), 200

    except PermissionError as e:
        return {"error": str(e)}, 403
    except Exception as e:
        return {"error": str(e)}, 400


# =========================================================
# 🔻 ACCOUNT STATUS
# =========================================================
def account_status(**kwargs: Any) -> Tuple[Dict[str, Any], int]:
    core = get_core(current_app)

    try:
        accounts_service = _get_accounts_service(core)

        account_id = _parse_uuid(kwargs.get("account_id"), "account_id")

        # ✅ VALIDATION (optional but recommended)
        _validate_account_access(account_id)

        # service expects owner_account_id-like object
        workspace = SimpleNamespace(owner_account_id=account_id)

        account = accounts_service.get_account_details_by_id(workspace)

        raw_status = getattr(account, "status", None)
        status = raw_status if isinstance(raw_status, str) and raw_status else "ACTIVE"

        response: Dict[str, Any] = {
            "id": str(getattr(account, "id", account_id)),
            "status": status,
        }

        if hasattr(account, "name"):
            response["name"] = account.name

        return response, 200

    except PermissionError as e:
        return {"error": str(e)}, 403
    except Exception as e:
        return {"error": str(e)}, 400
```

---

# ✅ What you achieved now

✔ Only 2 accounts can perform actions
✔ Swagger input is respected
✔ Unauthorized users → **403 Forbidden**
✔ Clean reusable validation
✔ No duplication

---

# ⚡ If you want next level (recommended later)

Instead of hardcoding:

* Move IDs to **env variable / config**
* Or fetch from DB

---

If you want, next I can help you:

👉 Fix that Swagger wrong ID issue
👉 Or add **logging + debugging trace**
👉 Or convert this into **middleware (clean architecture)**
