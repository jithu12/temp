`_delete_deployment` only deletes the **Kubernetes deployment** (the pod/container) — it does NOT delete persistent volumes or the database. The database connection is stored separately via `stack.database.vault_secret_id` which is a reference to an external database, not data inside the pod.

**This means Grafana data IS preserved across deactivate/reactivate.** ✅

The database survives because:
- Grafana data lives in the **external database** (`stack.database`) — not in the pod
- `_delete_deployment` only removes the pod
- The database record and its vault secret are untouched during deactivation

Now I'm confident the fix is safe. Here's the final `_activate_workspace` to replace in `workspace.py`:

```python
def _activate_workspace(self, workspace: Workspace) -> Workspace:
    self.logger.info(f"Reactivating {logname(workspace)}...")
    try:
        kube_stack = workspace.kube_stack

        if kube_stack is None:
            self.logger.error(
                f"{logname(workspace)}: No kube stack found — cannot reactivate"
            )
            raise WorkspaceActivationFailedError(workspace.id)

        # Reset the existing stack back to CREATION_REQUESTED
        # so the async create_stack job will recreate all
        # Kubernetes resources (ingress, deployment, secrets etc.)
        # using the same stack ID, namespace and database — 
        # preserving all Grafana data
        self._kube_service._update_stack_with(
            kube_stack,
            status=Status.CREATION_REQUESTED,
        )

        # Fire async job using the EXISTING stack id
        # not a new one — this is key for data preservation
        self._kube_service.workflow_executor.async_exec_core_function(
            service="kube",
            function="create_stack",
            kwargs={
                "stack_id": kube_stack.id,
                "temp_ns_check": False,
            },
        )

        self.logger.info(
            f"{logname(workspace)}: Kube stack reactivation requested "
            f"for stack_id={kube_stack.id}"
        )

    except WorkspaceActivationFailedError:
        raise
    except Exception as e:
        self.logger.exception(
            f"{logname(workspace)}: '{workspace.name}' kube reactivation failed. "
            f"Error: {e}"
        )

    return self._update_workspace_with_and_return(
        workspace,
        status=Status.ACTIVE,
    )
```

## Why this is correct and safe

| Step | What happens |
|---|---|
| `kube_stack = workspace.kube_stack` | Gets the existing stack — same ID, same namespace, same DB |
| `_update_stack_with(status=CREATION_REQUESTED)` | Resets stack status so `create_stack` will process it |
| `async_exec_core_function(create_stack, stack_id=kube_stack.id)` | Fires job with EXISTING stack ID |
| Celery picks up `create_stack` | Calls `_refresh_stack` → returns early (CREATION_REQUESTED) → calls `_create_stack` |
| `_create_stack` | Recreates HPA, Ingress, Config, Service, Secrets, Deployment |
| Same database connected | All Grafana data intact ✅ |
| Ingress restored | Grafana login page works again ✅ |

## After applying

```bash
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
python app.py
celery -A dataviz_async.app:app worker
```

Then test the full lifecycle:
1. Add some data in Grafana
2. Call deactivate → Grafana shows 404
3. Call activate → wait ~30 seconds for async job
4. Refresh Grafana → login page loads, data still there

The 30 second wait is because `create_stack` is async — Celery picks it up and recreates all the Kubernetes resources in the background.
