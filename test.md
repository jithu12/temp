OK let's trace this systematically. When you call **activate**, it should only change workspace status to `ACTIVE` — nothing should go to `DELETED`. The fact that one workspace goes `DELETED` during activation means something in the activate flow is accidentally calling delete logic.

## Step 1 — Find the exact workspace going DELETED

Before you call activate, note all workspace statuses:
```sql
SELECT id, name, status 
FROM public.api_workspace
WHERE owner_account_id = 'your-real-uuid'
ORDER BY name;
```

Save that output. Then call activate. Then run the same query again. Tell me:
- Which workspace changed to DELETED?
- What was its status BEFORE activate was called?

## Step 2 — Check the Celery logs carefully

When you call activate, the Celery worker log should show what's happening to each workspace. Share the full Celery output after calling activate. Look for lines mentioning the workspace that ended up DELETED.

## My strongest suspicion

Look at `reactivate_workspaces_by_owner_account_id` — it filters for `Status.INACTIVE` workspaces:

```python
filters = [
    FilteringCriterion("owner_account_id", account_activation_id),
    FilteringCriterion("status", Status.INACTIVE),
]
```

Then calls `reactivate_workspace(workspace.id)`. And look at `reactivate_workspace`:

```python
def reactivate_workspace(self, workspace_id):
    workspace = self.repositories.workspace.get_by_id(workspace_id)
    workspace = self._refresh_workspace(workspace.id)  # ← refreshes from external source

    if workspace.status is Status.ACTIVE:
        raise WorkspaceActivationFailedError(workspace.id)

    if workspace.status is Status.INACTIVE:
        return self._activate_workspace(workspace)

    self.logger.error(f"Unknown status for {logname(workspace)}: {workspace.status}")
    raise WorkspaceActivationFailedError(workspace.id)
```

The suspicious line is `self._refresh_workspace(workspace.id)`. This refreshes the workspace state from an external source (probably Kubernetes). If the workspace exists in your DB as `INACTIVE` but Kubernetes reports it as something else (like `DELETED` or not found), `_refresh_workspace` might update the status to something unexpected.

Check what `_refresh_workspace` does:
```bash
grep -n -A 20 "def _refresh_workspace" dataviz_core/services/workspace.py
```

Share the output — I think that's where the bug is.

## Also check — is the DELETED workspace the one that was FAILED before?

Remember one workspace was `FAILED` from the previous deactivation attempts. When activate runs, it filters for `INACTIVE` — so `FAILED` workspaces shouldn't be touched. But if `_refresh_workspace` changes a `FAILED` workspace to something else, it could get caught in unexpected logic.

Share:
1. SQL output before and after activate (which workspace changed to DELETED)
2. Full Celery log during activate
3. Output of `grep -n -A 20 "def _refresh_workspace" dataviz_core/services/workspace.py`

That'll pinpoint exactly where the DELETED status is being set during activation.
