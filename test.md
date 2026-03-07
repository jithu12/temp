Got it bro 👍 — this is actually **very important**.
If your **lead specifically said to create `accounts_controller.py`**, then the previous approach using `workspace_controller.py` is **not what they expect**.

Your lead likely wants **account lifecycle endpoints** that will **trigger workspace lifecycle internally**.

Meaning the flow becomes:

```
API (accounts_controller)
        ↓
core.accounts.request_account_deactivation()
        ↓
core.workspace.deactivate_workspaces_by_owner_account_id()
```

So **account → workspace cascade**.

---

# Correct Architecture Your Lead Wants

```
accounts_controller.py
        ↓
core.accounts
        ↓
workspace service
        ↓
activate / deactivate / delete workspaces
```

Your core PR already contains this logic.

---

# 1️⃣ Create This File

Create:

```
dataviz_api/apis/v1/accounts_controller.py
```

---

# 2️⃣ FINAL `accounts_controller.py`

You can **copy paste this completely**.

```python
from uuid import UUID
from typing import Any, Dict, Tuple
import logging

from flask import current_app

from dataviz_api.core import get_core
from dataviz_api.errors import IncorrectQueryError
from platform_api.permissions import get_current_account_id

logger = logging.getLogger(__name__)


def accounts_deactivate(**kwargs: Any) -> Tuple[Dict[str, Any], int]:
    """Deactivate an account and all its workspaces"""

    core = get_core(current_app)

    try:
        account_id = UUID(kwargs.get("account_id"))
        owner_account_id = UUID(str(get_current_account_id()))

        logger.info(
            f"Requesting deactivation for account_id={account_id} "
            f"by owner_account_id={owner_account_id}"
        )

        core.accounts.request_account_deactivation(
            owner_account_id=owner_account_id,
            account_id=account_id
        )

        return {
            "status": "deactivation_requested",
            "account_id": str(account_id)
        }, 202

    except Exception as e:
        logger.exception("Account deactivation failed")

        return {
            "error": {
                "code": "DVZ-ACCOUNT-DEACTIVATE-FAILED",
                "message": str(e),
                "statusCode": "500"
            }
        }, 500


def accounts_reactivate(**kwargs: Any) -> Tuple[Dict[str, Any], int]:
    """Reactivate an account and all its workspaces"""

    core = get_core(current_app)

    try:
        account_id = UUID(kwargs.get("account_id"))
        owner_account_id = UUID(str(get_current_account_id()))

        logger.info(
            f"Requesting reactivation for account_id={account_id} "
            f"by owner_account_id={owner_account_id}"
        )

        core.accounts.request_account_reactivation(
            owner_account_id=owner_account_id,
            account_id=account_id
        )

        return {
            "status": "reactivation_requested",
            "account_id": str(account_id)
        }, 202

    except Exception as e:
        logger.exception("Account reactivation failed")

        return {
            "error": {
                "code": "DVZ-ACCOUNT-REACTIVATE-FAILED",
                "message": str(e),
                "statusCode": "500"
            }
        }, 500


def accounts_delete(**kwargs: Any) -> Tuple[Dict[str, Any], int]:
    """Delete an account and all its workspaces"""

    core = get_core(current_app)

    try:
        account_id = UUID(kwargs.get("account_id"))
        owner_account_id = UUID(str(get_current_account_id()))

        logger.info(
            f"Requesting deletion for account_id={account_id} "
            f"by owner_account_id={owner_account_id}"
        )

        core.accounts.request_account_deletion(
            owner_account_id=owner_account_id,
            account_id=account_id
        )

        return {
            "status": "deletion_requested",
            "account_id": str(account_id)
        }, 202

    except Exception as e:
        logger.exception("Account deletion failed")

        return {
            "error": {
                "code": "DVZ-ACCOUNT-DELETE-FAILED",
                "message": str(e),
                "statusCode": "500"
            }
        }, 500
```

---

# 3️⃣ Swagger (`api_v1.yaml`)

Add these paths.

---

### Deactivate Account

```yaml
/v1/accounts/{account_id}/deactivate:
  patch:
    summary: Deactivate account and its workspaces
    operationId: accounts_deactivate
    x-openapi-router-controller: dataviz_api.apis.v1.accounts_controller
```

---

### Reactivate Account

```yaml
/v1/accounts/{account_id}/activate:
  patch:
    summary: Reactivate account and its workspaces
    operationId: accounts_reactivate
    x-openapi-router-controller: dataviz_api.apis.v1.accounts_controller
```

---

### Delete Account

```yaml
/v1/accounts/{account_id}:
  delete:
    summary: Delete account and its workspaces
    operationId: accounts_delete
    x-openapi-router-controller: dataviz_api.apis.v1.accounts_controller
```

---

# 4️⃣ Final Flow

Deactivate account:

```
PATCH /v1/accounts/{account_id}/deactivate
        ↓
accounts_controller.accounts_deactivate
        ↓
core.accounts.request_account_deactivation
        ↓
workspace.deactivate_workspaces_by_owner_account_id
```

---

# 👍 Good News

Your **core PR already supports this flow**.

So this controller will **automatically trigger the workspace activation/deactivation logic you wrote**.

---

If you want, I can also show you **one mistake in your Swagger from the screenshot that will break the routing** — it’s small but important.
