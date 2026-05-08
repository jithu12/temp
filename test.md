Now I can see it clearly. The issue is in `reactivate_workspace` which calls `_refresh_workspace` and gets back `ACTIVE` status from Kubernetes (because kube deletion partially failed). 

Share `reactivate_workspace` function:

```bash
grep -n "def reactivate_workspace\b" dataviz_core/services/workspace.py
```

Then:
```bash
sed -n '<line_number>,<line_number+35>p' dataviz_core/services/workspace.py
```

While you get that, based on what we know the fix will be in `reactivate_workspaces_by_owner_account_id` — bypass `reactivate_workspace` entirely and call `_activate_workspace` directly since we already know from the DB filter that these workspaces are `INACTIVE`:

```python
def reactivate_workspaces_by_owner_account_id(
    self,
    account_activation_id: uuid.UUID,
) -> List[Dict]:
    workspace_updates = []
    filters = [
        FilteringCriterion("owner_account_id", account_activation_id),
        FilteringCriterion("status", Status.INACTIVE),
    ]
    workspaces = self.repositories.workspace.list(filters=filters)
    for workspace in workspaces:
        _workspace_updates = {}
        self.logger.info(
            f"Requesting {logname(workspace)} Activation due to "
            f"Account Activation request from account event lifecycle"
        )
        self.logger.debug(f"Requesting {logname(workspace)} Activation...")
        try:
            # Call _activate_workspace directly instead of reactivate_workspace
            # because reactivate_workspace calls _refresh_workspace which checks
            # Kubernetes status — if kube deletion partially failed during
            # deactivation, Kubernetes may still report ACTIVE even though
            # DB says INACTIVE, causing reactivate_workspace to reject it.
            # We trust the DB filter above (status=INACTIVE) and force activation.
            workspace_res = self._activate_workspace(workspace)

            _workspace_updates["workspace_id"] = str(workspace_res.id)
            _workspace_updates["status"] = "reactivated"
            _workspace_updates["name"] = workspace_res.name
            workspace_updates.append(_workspace_updates)

        except WorkspaceActivationFailedError as e:
            _workspace_updates["workspace_id"] = str(workspace.id)
            _workspace_updates["status"] = "reactivation failed"
            _workspace_updates["name"] = workspace.name
            workspace_updates.append(_workspace_updates)
            self.logger.error(
                f"Reactivation failed for {logname(workspace)}. Error: {e}"
            )
            continue

    self.logger.info(
        f"Workspace reactivation process completed "
        f"and workspace updates collected {workspace_updates}."
    )
    return workspace_updates
```

## Why this fixes it

**Before:**
```
reactivate_workspaces_by_owner_account_id
    → reactivate_workspace(workspace.id)
        → _refresh_workspace()  ← checks Kubernetes
        → Kubernetes says ACTIVE (partial deletion)
        → raises WorkspaceActivationFailedError ❌
```

**After:**
```
reactivate_workspaces_by_owner_account_id
    → DB filter already confirmed status=INACTIVE ✅
    → _activate_workspace(workspace) directly
        → resets kube_stack to CREATION_REQUESTED
        → fires create_stack async job
        → Grafana comes back ✅
```

We skip `_refresh_workspace` entirely because we already know from the DB query that these workspaces are `INACTIVE`. We trust our own DB over what Kubernetes reports — especially in cases where kube deletion was partial.

Apply this change, clear pycache, restart both API and Celery, then test deactivate followed by activate. The 404 should resolve after ~30 seconds while Celery rebuilds the Kubernetes resources.
