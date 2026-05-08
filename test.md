Now I can see exactly what's happening. Two separate issues:

## Issue 1 — Deactivation log

```
Error while deleting LDAP ConfigMap...
Error while deleting Service...
Error while deleting Fluentd ConfigMap...
Error while deleting Secret... (3 times)
Workspace already inactive
```

The kube deletion is partially failing — some resources can't be deleted (probably already gone or permission issues in dev). But the last line says **"Workspace already inactive"** — which means the workspace IS being marked INACTIVE in the DB. The deactivation IS working from a DB perspective.

## Issue 2 — Reactivation log — THIS is the real problem

```
Cannot activate Workspace while it's already ACTIVE
Reactivation failed for Workspace
```

When `reactivate_workspace` is called, it calls `_refresh_workspace` first:

```python
workspace = self._refresh_workspace(workspace.id)
```

`_refresh_workspace` checks Kubernetes and sees the stack is still somewhat ACTIVE (because kube deletion partially failed — some resources like the deployment still exist). So it returns the workspace with status `ACTIVE`. Then:

```python
if workspace.status is Status.ACTIVE:
    raise WorkspaceActivationFailedError(workspace.id)  # ← raises here!
```

The workspace DB says `INACTIVE` but `_refresh_workspace` sees Kubernetes still has some resources → reports `ACTIVE` → activation fails.

## The root cause chain

```
Deactivation → kube deletion partially fails
             → some kube resources still exist (deployment maybe)
             → _refresh_workspace sees them → reports ACTIVE
             → reactivate_workspace rejects it
             → Grafana stays at 404
```

## The fix — in `reactivate_workspace`

The problem is `_refresh_workspace` is updating the DB status based on what Kubernetes reports. We need to bypass the refresh check and force activation regardless.

Find `reactivate_workspace` in `workspace.py` and share it:

```bash
grep -n "def reactivate_workspace" dataviz_core/services/workspace.py
sed -n '<that_line>,<that_line+30>p' dataviz_core/services/workspace.py
```

Or run:
```bash
grep -n "def reactivate_workspace" dataviz_core/services/workspace.py
```

Then:
```bash
sed -n '1060,1100p' dataviz_core/services/workspace.py
```

Share the output. Based on what we've seen, the fix will be to skip `_refresh_workspace` during account-level reactivation — or to handle the case where `_refresh_workspace` reports `ACTIVE` but DB says `INACTIVE` by trusting the DB status instead of Kubernetes.
