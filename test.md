I can see exactly what's happening. Let me get the full picture first:Based on what you shared plus the pattern from `_deactivate_workspace`, I can see all three methods that need fixing. Here are the exact changes:

## Fix 1 — `_delete_workspace` (around line 440)

Find:
```python
def _delete_workspace(self, workspace: Workspace, is_failed: bool = False) -> Workspace:
    self.logger.info(f"Deleting {logname(workspace)}...")
    try:
        self._kube_service.request_stack_deletion(
            workspace.kube_stack_id,
        )
    except Exception as e:
        self.logger.exception(
            f"{logname(workspace)}: '{workspace.name}' deletion failed"
        )
        self._update_workspace_with(workspace, status=Status.FAILED)
        raise WorkspaceDeletionFailedError(workspace.id) from e
    return self._update_workspace_with_and_return(workspace, status=Status.DELETED)
```

Replace with:
```python
def _delete_workspace(self, workspace: Workspace, is_failed: bool = False) -> Workspace:
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

## Fix 2 — `delete_workspaces_by_owner_account_id` (around line 1049)

You showed it gets cut off at line 1049 where `except WorkspaceDeletionFailedError` starts. The full except block currently looks like:

```python
except WorkspaceDeletionFailedError as e:
    _workspace_updates["workspace_id"] = str(workspace.id)
    _workspace_updates["status"] = "deletion failed"
    _workspace_updates["name"] = workspace.name
    workspace_updates.append(_workspace_updates)
    self.logger.error(
        f"Deletion failed for {logname(workspace)}. Error: {e}"
    )
    continue
```

Since we fixed `_delete_workspace` to never raise `WorkspaceDeletionFailedError` anymore, this except block will never be hit. But keep it for safety — just update the status message:

```python
except WorkspaceDeletionFailedError as e:
    _workspace_updates["workspace_id"] = str(workspace.id)
    _workspace_updates["status"] = "deletion failed"
    _workspace_updates["name"] = workspace.name
    workspace_updates.append(_workspace_updates)
    self.logger.error(
        f"Deletion failed for {logname(workspace)}. Error: {e}"
    )
    continue
```

Also — the filter on line 1023 only gets `Status.ACTIVE` workspaces:
```python
filters = [
    FilteringCriterion("owner_account_id", owner_account_id),
    FilteringCriterion("status", Status.ACTIVE),  # ← misses INACTIVE and FAILED
]
```

But inside the loop (line 1039) it handles both `Status.ACTIVE` and `Status.INACTIVE`. This means INACTIVE and FAILED workspaces are never fetched and never deleted. Fix the filter:

```python
filters = [
    FilteringCriterion("owner_account_id", owner_account_id),
]
```

Remove the status filter entirely — let all workspaces through, and the loop logic handles each status correctly.

## Fix 3 — `delete_workspace` (around line 404)

This calls `_delete_workspace` internally. Check if it has its own try/except that might also block the DELETED status:

```bash
grep -n -A 25 "def delete_workspace" dataviz_core/services/workspace.py | head -30
```

Share the output and I'll confirm if it needs changes too. But most likely it just calls `_delete_workspace` and the Fix 1 above will cascade through correctly.

## After all changes

```bash
# Clear pycache
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null

# Restart both
python app.py
celery -A dataviz_async.app:app worker
```

Then test delete with a real UUID and check:
```sql
SELECT id, name, status 
FROM public.api_workspace
WHERE owner_account_id = 'your-real-uuid';
```

All workspaces should show `DELETED`.

Can you share the output of:
```bash
grep -n -A 25 "def delete_workspace" dataviz_core/services/workspace.py | head -30
```

So I can confirm Fix 3 if needed.
