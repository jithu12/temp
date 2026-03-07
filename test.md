You’re absolutely right to call that out 👍
If the requirement is **“delete everything related to a specific workspace”**, then the API **must accept only `workspace_id`**, not `account_id`. Your existing delete controller already follows that pattern, and we should keep the same style for activate/deactivate.

So **do NOT use `accounts_controller.py` for this feature**.
Everything should stay in **`workspace_controller.py`**, because the resource is **workspace**.

Below is the **final code consistent with your existing delete implementation**.

---

# 1️⃣ Keep Your Existing Delete (DO NOT CHANGE)

From your screenshot, this is correct:

```python
def workspace_delete(**kwargs: Any) -> Tuple[Dict[str, Any], int]:
    """Delete a workspace given its identifier"""

    try:
        workspace_id = UUID(kwargs.get("workspace_id"))
    except Exception as e:
        raise IncorrectQueryError() from e

    core = get_core(current_app)

    try:
        workspace = core.workspace.request_workspace_deletion(
            account_id=get_current_account_id(),
            workspace_id=workspace_id
        )

        return core.workspace.to_dict(workspace), 204

    except ValueError:
        return WORKSPACE_NOT_FOUND_ERROR_DATA, 404

    except WorkspaceNotFoundError:
        return WORKSPACE_NOT_FOUND_ERROR_DATA, 404

    except Exception as e:
        return {"error": str(e)}, 500
```

This correctly:

```
DELETE /workspaces/{workspace_id}
        ↓
workspace_controller
        ↓
core.workspace.request_workspace_deletion()
        ↓
delete_workspace()  (your core logic)
```

---

# 2️⃣ Add Activate Workspace

Add this **below your delete function**.

```python
def workspace_activate(**kwargs: Any) -> Tuple[Dict[str, Any], int]:
    """Activate a workspace given its identifier"""

    try:
        workspace_id = UUID(kwargs.get("workspace_id"))
    except Exception as e:
        raise IncorrectQueryError() from e

    core = get_core(current_app)

    try:
        workspace = core.workspace.reactivate_workspace(workspace_id)

        return core.workspace.to_dict(workspace), 200

    except ValueError:
        return WORKSPACE_NOT_FOUND_ERROR_DATA, 404

    except WorkspaceNotFoundError:
        return WORKSPACE_NOT_FOUND_ERROR_DATA, 404

    except Exception as e:
        logger.exception("Unexpected error in workspace_activate: %s", e)

        return {
            "error": {
                "code": "DVZ-WORKSPACE-ACTIVATE-FAILED",
                "message": str(e),
                "statusCode": "500"
            }
        }, 500
```

---

# 3️⃣ Add Deactivate Workspace

```python
def workspace_deactivate(**kwargs: Any) -> Tuple[Dict[str, Any], int]:
    """Deactivate a workspace given its identifier"""

    try:
        workspace_id = UUID(kwargs.get("workspace_id"))
    except Exception as e:
        raise IncorrectQueryError() from e

    core = get_core(current_app)

    try:
        workspace = core.workspace.deactivate_workspace(workspace_id)

        return core.workspace.to_dict(workspace), 200

    except ValueError:
        return WORKSPACE_NOT_FOUND_ERROR_DATA, 404

    except WorkspaceNotFoundError:
        return WORKSPACE_NOT_FOUND_ERROR_DATA, 404

    except Exception as e:
        logger.exception("Unexpected error in workspace_deactivate: %s", e)

        return {
            "error": {
                "code": "DVZ-WORKSPACE-DEACTIVATE-FAILED",
                "message": str(e),
                "statusCode": "500"
            }
        }, 500
```

---

# 4️⃣ Swagger Endpoints

These must use **workspace_id**, not account_id.

### Activate

```yaml
/v1/workspaces/{workspace_id}/activate:
  patch:
    summary: Activate workspace
    operationId: workspace_activate
    x-openapi-router-controller: dataviz_api.apis.v1.workspace_controller
```

### Deactivate

```yaml
/v1/workspaces/{workspace_id}/deactivate:
  patch:
    summary: Deactivate workspace
    operationId: workspace_deactivate
    x-openapi-router-controller: dataviz_api.apis.v1.workspace_controller
```

Delete already exists:

```yaml
DELETE /v1/workspaces/{workspace_id}
```

---

# 5️⃣ Final Flow

### Activate

```
PATCH /workspaces/{workspace_id}/activate
        ↓
workspace_controller.workspace_activate
        ↓
core.workspace.reactivate_workspace
```

### Deactivate

```
PATCH /workspaces/{workspace_id}/deactivate
        ↓
workspace_controller.workspace_deactivate
        ↓
core.workspace.deactivate_workspace
```

### Delete

```
DELETE /workspaces/{workspace_id}
        ↓
workspace_controller.workspace_delete
        ↓
core.workspace.request_workspace_deletion
        ↓
delete_workspace()
```

---

✅ **Workspace ID only**
✅ **Deletes everything related to that workspace**
✅ **Matches your existing delete logic**
✅ **No account ID required in API**

---

If you want, send me **one screenshot of `workspace.py` in core where `reactivate_workspace()` and `deactivate_workspace()` are defined**, and I’ll confirm **100% that the API will call the exact logic you wrote**.
