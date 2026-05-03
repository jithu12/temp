Sure, here are the two targeted fixes you can make yourself.

## Fix 1 — `_deactivate_workspace` in `workspace.py`

Skip the kube call failure blocking the status change. The workspace should still go INACTIVE even if kube fails in dev:

**Find this:**
```python
def _deactivate_workspace(self, workspace: Workspace) -> Workspace:
    self.logger.info(f"Deactivating {logname(workspace)}...")
    try:
        self._kube_service.request_stack_deletion(
            workspace.kube_stack_id,
        )
    except Exception as e:
        self.logger.exception(f"{logname(workspace)}: '{workspace.name}' deactivation failed")
        self._update_workspace_with(workspace, status=Status.FAILED)
        raise WorkspaceDeActivationFailedError(workspace.id) from e
    return self._update_workspace_with_and_return(workspace, status=Status.INACTIVE)
```

**Replace with:**
```python
def _deactivate_workspace(self, workspace: Workspace) -> Workspace:
    self.logger.info(f"Deactivating {logname(workspace)}...")
    try:
        self._kube_service.request_stack_deletion(
            workspace.kube_stack_id,
        )
    except Exception as e:
        self.logger.exception(
            f"{logname(workspace)}: '{workspace.name}' kube deletion failed. "
            f"Marking workspace INACTIVE regardless. Error: {e}"
        )
    return self._update_workspace_with_and_return(workspace, status=Status.INACTIVE)
```

What changed: removed the `status=FAILED` and `raise WorkspaceDeActivationFailedError` from the except block. Now even if kube fails, the workspace still gets marked INACTIVE. The error is still logged so your Lead can see it happened.

## Fix 2 — `reactivate_workspace` in `workspace.py`

**Find this:**
```python
def reactivate_workspace(self, workspace_id: uuid.UUID) -> Workspace:
    workspace = self.repositories.workspace.get_by_id(workspace_id)
    workspace = self._refresh_workspace(workspace.id)
    self.logger.info(f"Starting {logname(workspace)} Re-Activation")
    if workspace.status is not Status.ACTIVE:
        self.logger.error(
            f"Cannot Activated {logname(workspace)} while it's {workspace.status}"
        )
        raise WorkspaceActivationFailedError(workspace.id)
    if workspace.status is Status.INACTIVE:
        return self._activate_workspace(workspace)
    if workspace.status is Status.INACTIVE:
        self.logger.error(f"{logname(workspace)} already inactive")
        return workspace
    self.logger.error(f"Unknown status for {logname(workspace)}: {workspace.status}")
    raise WorkspaceActivationFailedError(workspace.id)
```

**Replace with:**
```python
def reactivate_workspace(self, workspace_id: uuid.UUID) -> Workspace:
    workspace = self.repositories.workspace.get_by_id(workspace_id)
    workspace = self._refresh_workspace(workspace.id)
    self.logger.info(f"Starting {logname(workspace)} Re-Activation")

    if workspace.status is Status.ACTIVE:
        # Already active — nothing to do
        self.logger.error(
            f"Cannot activate {logname(workspace)} while it's already {workspace.status}"
        )
        raise WorkspaceActivationFailedError(workspace.id)

    if workspace.status is Status.INACTIVE:
        # This is what we want — activate it
        return self._activate_workspace(workspace)

    # Any other status — fail safely
    self.logger.error(
        f"Unknown status for {logname(workspace)}: {workspace.status}"
    )
    raise WorkspaceActivationFailedError(workspace.id)
```

What changed: flipped the first condition from `is not Status.ACTIVE` to `is Status.ACTIVE`. Now INACTIVE workspaces correctly flow through to `_activate_workspace` instead of being rejected.

## After making both changes

**1. Clear pycache:**
```bash
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
```

**2. Restart both:**
```bash
# Terminal 1
python app.py

# Terminal 2
celery -A dataviz_async.app:app worker
```

**3. Test deactivate with a real UUID that has workspaces.**

**4. Check the DB:**
```sql
SELECT id, name, status 
FROM public.api_workspace
WHERE owner_account_id = 'your-real-uuid';
```

All workspaces should now show `INACTIVE`.

**5. Test activate** — workspaces should go back to `ACTIVE`.

Note — the `_activate_workspace` also calls kube (`request_stack_creation`). If that also fails in dev, apply the same pattern as Fix 1 — catch the exception, log it, but still update the status to `ACTIVE`. Let me know if you hit that and I'll give you the exact change.
