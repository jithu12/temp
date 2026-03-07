Got it 👍 — what you want is **exactly how Swagger groups APIs**.

In Swagger UI, sections like:

* **workspaces**
* **grafana**

come from the **`tags:` field in OpenAPI**.

So to create a **new section called `accounts`**, you must:

1️⃣ Define a **new tag**
2️⃣ Use that tag in your endpoints.

---

# 1️⃣ Add Accounts Tag (Top of api_v1.yaml)

At the top of `api_v1.yaml` you will see something like:

```yaml
tags:
  - name: workspaces
    description: Grafana Workspaces

  - name: grafana
    description: Grafana Image and Plugins Details
```

Add **accounts** below it:

```yaml
tags:
  - name: workspaces
    description: Grafana Workspaces

  - name: grafana
    description: Grafana Image and Plugins Details

  - name: accounts
    description: Account lifecycle operations
```

Now Swagger knows **there is a new section**.

---

# 2️⃣ Add Accounts Endpoints

Add these under `paths:`.

### Deactivate Account

```yaml
/v1/accounts/{account_id}/deactivate:
  patch:
    tags:
      - accounts
    summary: Deactivate account
    operationId: account_deactivate
    x-openapi-router-controller: dataviz_api.apis.v1.accounts_controller

    parameters:
      - name: account_id
        in: path
        required: true
        schema:
          type: string
          format: uuid

    responses:
      "200":
        description: Account deactivated
      default:
        description: Unexpected error
```

---

### Activate Account

```yaml
/v1/accounts/{account_id}/activate:
  patch:
    tags:
      - accounts
    summary: Reactivate account
    operationId: account_activate
    x-openapi-router-controller: dataviz_api.apis.v1.accounts_controller

    parameters:
      - name: account_id
        in: path
        required: true
        schema:
          type: string
          format: uuid

    responses:
      "200":
        description: Account activated
      default:
        description: Unexpected error
```

---

### Delete Account

```yaml
/v1/accounts/{account_id}:
  delete:
    tags:
      - accounts
    summary: Delete account
    operationId: account_delete
    x-openapi-router-controller: dataviz_api.apis.v1.accounts_controller

    parameters:
      - name: account_id
        in: path
        required: true
        schema:
          type: string
          format: uuid

    responses:
      "200":
        description: Account deletion requested
      default:
        description: Unexpected error
```

---

# 3️⃣ Result in Swagger UI

After restarting API:

```
Accounts
   PATCH /accounts/{account_id}/deactivate
   PATCH /accounts/{account_id}/activate
   DELETE /accounts/{account_id}
```

So your Swagger will look like:

```
workspaces
grafana
accounts   ← new section
```

Exactly like you wanted.

---

# 4️⃣ Restart API

After editing yaml:

```bash
make run
```

or

```bash
python app.py
```

Refresh:

```
http://127.0.0.1:5000/v1/documentation
```

---

# 5️⃣ Small Tip (Important)

Make sure your path **matches existing pattern**.

Your repo uses:

```
/v1/workspaces/*
```

So accounts should be:

```
/v1/accounts/*
```

not

```
/accounts/*
```

---

If you want, I can also show you a **very small change that will make your PR cleaner and easier for your lead to approve** (it's something senior backend engineers always do).


You're correct now 👍 — and your screenshots confirm exactly how it should work.

Your **core layer already provides the lifecycle functions**, so the **API should only trigger them**, not re-implement logic.

From your screenshots inside
`dataviz_core/services/accounts.py` you have:

1️⃣ `request_account_deactivation(...)`
2️⃣ `request_account_reactivation(...)`
3️⃣ `request_account_deletion(...)`

These are **the exact functions the API must call**.

So the API controller should be **very thin** and just forward the request.

---

# 1️⃣ Correct API Controller

Create / update:

```
dataviz_api/apis/v1/accounts_controller.py
```

Copy-paste this final version:

```python
from typing import Tuple, Dict, Any
from uuid import UUID

from flask import current_app

from dataviz_api.apis.v1.utils import get_core
from dataviz_api.apis.v1.utils import get_current_account_id


def account_deactivate(**kwargs: Any) -> Tuple[Dict[str, Any], int]:
    """Deactivate an account"""

    core = get_core(current_app)

    try:
        account_id = UUID(kwargs.get("account_id"))
        owner_account_id = get_current_account_id()

        account = core.accounts.request_account_deactivation(
            owner_account_id=owner_account_id,
            account_id=account_id
        )

        return core.accounts.to_dict(account), 200

    except Exception as e:
        return {"error": str(e)}, 500


def account_activate(**kwargs: Any) -> Tuple[Dict[str, Any], int]:
    """Reactivate an account"""

    core = get_core(current_app)

    try:
        account_id = UUID(kwargs.get("account_id"))
        owner_account_id = get_current_account_id()

        account = core.accounts.request_account_reactivation(
            owner_account_id=owner_account_id,
            account_id=account_id
        )

        return core.accounts.to_dict(account), 200

    except Exception as e:
        return {"error": str(e)}, 500


def account_delete(**kwargs: Any) -> Tuple[Dict[str, Any], int]:
    """Delete an account"""

    core = get_core(current_app)

    try:
        account_id = UUID(kwargs.get("account_id"))
        owner_account_id = get_current_account_id()

        account = core.accounts.request_account_deletion(
            owner_account_id=owner_account_id,
            account_id=account_id
        )

        return core.accounts.to_dict(account), 200

    except Exception as e:
        return {"error": str(e)}, 500
```

---

# 2️⃣ Swagger (api_v1.yaml)

Add a **new accounts section**.

---

## Deactivate Account

```yaml
/v1/accounts/{account_id}/deactivate:
  patch:
    summary: Deactivate account
    operationId: account_deactivate
    x-openapi-router-controller: dataviz_api.apis.v1.accounts_controller

    tags:
      - accounts

    parameters:
      - name: account_id
        in: path
        required: true
        schema:
          type: string
          format: uuid

    responses:
      "200":
        description: Account deactivated
      default:
        description: Unexpected error
```

---

## Reactivate Account

```yaml
/v1/accounts/{account_id}/activate:
  patch:
    summary: Reactivate account
    operationId: account_activate
    x-openapi-router-controller: dataviz_api.apis.v1.accounts_controller

    tags:
      - accounts

    parameters:
      - name: account_id
        in: path
        required: true
        schema:
          type: string
          format: uuid

    responses:
      "200":
        description: Account activated
      default:
        description: Unexpected error
```

---

## Delete Account

```yaml
/v1/accounts/{account_id}:
  delete:
    summary: Delete account
    operationId: account_delete
    x-openapi-router-controller: dataviz_api.apis.v1.accounts_controller

    tags:
      - accounts

    parameters:
      - name: account_id
        in: path
        required: true
        schema:
          type: string
          format: uuid

    responses:
      "200":
        description: Account deletion requested
      default:
        description: Unexpected error
```

---

# 3️⃣ What Will Happen Internally

Example flow:

```
PATCH /v1/accounts/{account_id}/deactivate
```

↓

```
accounts_controller.account_deactivate()
```

↓

```
core.accounts.request_account_deactivation()
```

↓

```
Find workspaces linked to account
Shutdown workspaces
Update account status
```

Exactly matching your architecture slide.

---

# 4️⃣ Why This Is Correct

Your core already implements:

```
request_account_deactivation()
request_account_reactivation()
request_account_deletion()
```

So API must **only trigger them**, not recreate logic.

This keeps:

✅ **Core = business logic**
✅ **API = transport layer**

Which is exactly how **Dataviz architecture is designed**.

---

# 5️⃣ One Important Detail

You noticed this correctly in the screenshot:

```
owner_account_id
account_id
```

So API must pass **both**, and we obtain owner using:

```
get_current_account_id()
```

---

# 6️⃣ What Your PR Will Look Like

Files changed:

```
dataviz_api/apis/v1/accounts_controller.py   ← new
dataviz_api/specifications/api_v1.yaml       ← new endpoints
```

Core stays untouched.

---

✅ Your understanding is **now 100% aligned with the repo design**.

---

If you want, I can also show you **one improvement your lead will definitely like in the PR review** (it's a small change but very senior-level).
