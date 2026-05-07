Yes — replace the entire function with this:

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

        # Fire async job using the EXISTING stack id — not a new one
        # This is key for data preservation
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

## What changed vs your current version

**Before — creates a NEW kube stack:**
```python
self._kube_service.request_stack_creation(
    ws_name=workspace.name,
    name=workspace.kube_stack.name,  # uses old name but creates new record
    ...
)
```
This inserts a new `KubeStack` row in DB with a new ID. The workspace still points to the old deleted stack. Nothing reconnects. Grafana stays broken.

**After — reuses the EXISTING kube stack:**
```python
self._kube_service._update_stack_with(
    kube_stack,
    status=Status.CREATION_REQUESTED,  # resets existing stack
)
self._kube_service.workflow_executor.async_exec_core_function(
    service="kube",
    function="create_stack",
    kwargs={"stack_id": kube_stack.id},  # same ID as before
)
```

Same stack record, same namespace, same database connection — all Grafana data preserved. `create_stack` sees `CREATION_REQUESTED` and rebuilds all the Kubernetes resources (ingress, deployment, secrets) restoring Grafana access.

After applying, clear pycache and restart both API server and Celery worker.
