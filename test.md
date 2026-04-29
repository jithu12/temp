Yes, you're right. Let me trace through exactly why the workspace status isn't changing.

## The flow when deactivate is called with workspaces

```
API → request_account_deactivation → fires async job
                                          ↓
                              Celery picks up job
                                          ↓
                              deactivate_account(owner_account_id)
                                          ↓
                    workspace_service.deactivate_workspaces_by_owner_account_id()
                                          ↓
                              _deactivate_workspace(workspace)
                                          ↓
                    _kube_service.request_stack_deletion() ← THIS FAILS
                                          ↓
                              raises WorkspaceDeActivationFailedError
                                          ↓
                    workspace status set to "deactivation failed" NOT INACTIVE
```

## Look at `deactivate_workspaces_by_owner_account_id` in workspace.py

From the screenshots earlier (lines 890-912):

```python
try:
    workspace_res = self._deactivate_workspace(workspace)
    _workspace_updates["status"] = "deactivated"
except WorkspaceDeActivationFailedError as e:
    _workspace_updates["status"] = "deactivation failed"  # ← sets failed, not INACTIVE
    self.logger.error(...)
    continue
```

And `_deactivate_workspace`:
```python
try:
    self._kube_service.request_stack_deletion(
        stack_id=workspace.kube_stack_id,  # ← this was failing
    )
except Exception as e:
    self._update_workspace_with(workspace, status=Status.FAILED)
    raise WorkspaceDeActivationFailedError(workspace.id) from e

return self._update_workspace_with_and_return(workspace, status=Status.INACTIVE)
```

So the workspace only gets set to `INACTIVE` if `_kube_service.request_stack_deletion` **succeeds**. If it fails (which it was, due to the wrong param name), the workspace gets set to `FAILED` and the status never reaches `INACTIVE`.

## What to check now

You said you fixed the `request_stack_deletion` param bug. After that fix, the flow should work. But let me ask:

**Did you restart BOTH the API server AND the Celery worker after fixing the param?**

```bash
# Terminal 1 - restart API
python app.py

# Terminal 2 - restart Celery worker
celery -A dataviz_async.app:app worker
```

Both need to restart to pick up the Core code change.

## Also check — what does the Celery log say NOW after the param fix?

Run deactivate again on a real UUID with workspaces and share the Celery worker terminal output. It should now show either:

**Success:**
```
INFO: Starting Workspace (xxx) deactivation
INFO: Deactivation completed for owner_account_id: xxx
```

**Or a different error** — which means there's another issue in the kube call chain.

## Also — in dev, does kube actually work?

This is important. `_deactivate_workspace` calls `_kube_service.request_stack_deletion` which actually tries to **delete a Kubernetes stack**. In a dev environment, this might fail because:

1. No Kubernetes cluster is connected
2. The stack doesn't actually exist in kube
3. Dev kube credentials aren't configured

If that's the case, every workspace deactivation will fail at the kube level — not because of your code, but because the infrastructure isn't there in dev.

Check the Celery logs after the restart. If you see something like:

```
ConnectionRefusedError: [Errno 111] Connection refused
# or
kubernetes.client.exceptions.ApiException
# or
KubeError: stack not found
```

Then it's a kube infrastructure issue in dev, not a code issue.

**Share the Celery worker logs after restarting both processes and trying deactivate again.** That'll tell us definitively what's happening at the workspace level.
