The `FAILED` status on some workspaces means the kube call is failing for those specific workspaces. This is the same issue we fixed for deactivation — the kube service fails in dev and marks the workspace as `FAILED` instead of `DELETED`.

## The fix — in `workspace.py`

Find `delete_workspaces_by_owner_account_id` and `_delete_workspace` (or however the deletion is implemented). It follows the same pattern as `_deactivate_workspace`.

Run this to find it:
```bash
grep -n "def delete_workspace\|def _delete_workspace\|delete_workspaces_by" dataviz_core/services/workspace.py
```

It will look something like this:

```python
def _delete_workspace(self, workspace: Workspace) -> Workspace:
    self.logger.info(f"Deleting {logname(workspace)}...")
    try:
        self._kube_service.request_stack_deletion(
            workspace.kube_stack_id,
        )
    except Exception as e:
        self.logger.exception(...)
        self._update_workspace_with(workspace, status=Status.FAILED)
        raise WorkspaceDeletionFailedError(workspace.id) from e  # ← blocks DELETED status
    return self._update_workspace_with_and_return(workspace, status=Status.DELETED)
```

**Apply the same fix as deactivation** — catch the kube exception, log it, but still mark the workspace as DELETED:

```python
def _delete_workspace(self, workspace: Workspace) -> Workspace:
    self.logger.info(f"Deleting {logname(workspace)}...")
    try:
        self._kube_service.request_stack_deletion(
            workspace.kube_stack_id,
        )
    except Exception as e:
        self.logger.exception(
            f"{logname(workspace)}: '{workspace.name}' kube deletion failed. "
            f"Marking workspace DELETED regardless. Error: {e}"
        )
    return self._update_workspace_with_and_return(workspace, status=Status.DELETED)
```

## Also check `delete_workspaces_by_owner_account_id`

The bulk method that loops over workspaces might also be catching `WorkspaceDeletionFailedError` and continuing without updating status. Find it and share it here — I'll tell you exactly what to change.

Run:
```bash
grep -n -A 30 "def delete_workspaces_by_owner_account_id" dataviz_core/services/workspace.py
```

Share the output and I'll give you the precise fix. But the pattern will be identical to what we did for deactivation — remove the `raise` from the `except` block so the workspace still gets marked `DELETED` even if kube fails.
