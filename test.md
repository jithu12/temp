Good — the error in your screenshot clearly shows the problem. Let’s decode it simply.

## ❗ Error Meaning

Your OpenAPI validator says:

```
InvalidSpecification: 'responses' is a required property
```

And it points to this path:

```
/v1/workspaces/{workspace_id}/activate
```

Specifically this block:

```
patch:
  summary: Activate workspace
  operationId: workspace_activate
  x-openapi-router-controller: dataviz_api.apis.v1.workspace_controller
```

The issue is **you forgot the `responses:` section**, which is **mandatory in OpenAPI**.

Every endpoint **must define responses**, even if it's simple.

---

# ✅ Fix (Copy-Paste)

Open:

```
dataviz_api/specifications/api_v1.yaml
```

Add **responses** like this.

### ✔ Activate Workspace

```yaml
/v1/workspaces/{workspace_id}/activate:
  patch:
    summary: Activate workspace
    operationId: workspace_activate
    x-openapi-router-controller: dataviz_api.apis.v1.workspace_controller

    tags:
      - workspaces

    parameters:
      - name: workspace_id
        in: path
        required: true
        schema:
          type: string
          format: uuid

    responses:
      "200":
        description: Workspace activated successfully
      "404":
        description: Workspace not found
      default:
        description: Unexpected error
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/ErrorModel"
```

---

### ✔ Deactivate Workspace

Add this too if you created it.

```yaml
/v1/workspaces/{workspace_id}/deactivate:
  patch:
    summary: Deactivate workspace
    operationId: workspace_deactivate
    x-openapi-router-controller: dataviz_api.apis.v1.workspace_controller

    tags:
      - workspaces

    parameters:
      - name: workspace_id
        in: path
        required: true
        schema:
          type: string
          format: uuid

    responses:
      "200":
        description: Workspace deactivated successfully
      "404":
        description: Workspace not found
      default:
        description: Unexpected error
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/ErrorModel"
```

---

# 🧠 Why This Happened

OpenAPI requires **every HTTP method** to contain:

```
responses:
```

Without it, the spec fails validation.

Your delete endpoint works because it already has something like:

```yaml
responses:
  "204":
    description: Deleted workspace
```

---

# 🚀 After Fix

Restart API:

```bash
make run
```

or

```bash
python app.py
```

The error should disappear.

---

# 💡 Small Tip (Important for You)

Since you're working in **Connexion + OpenAPI**, every endpoint must have:

```
summary
operationId
parameters
responses   ← mandatory
```

Missing `responses` is the **#1 Connexion/OpenAPI error**.

---

If you want, I can also show you a **cleaner structure for your workspace API YAML** so it matches the **Dataviz repo style exactly** (it will also make your PR easier to approve).
