Sure! Here's a clean summary of all fixes you need to apply in `dataviz_core/services/workspace.py`:

## Fix 1 — `_activate_workspace`

Find and replace the entire function:

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

## Fix 2 — `reactivate_workspace`

Find and replace the entire function:

```python
def reactivate_workspace(self, workspace_id: uuid.UUID) -> Workspace:
    workspace = self.repositories.workspace.get_by_id(workspace_id)

    self.logger.info(f"Starting {logname(workspace)} Re-Activation")

    # Trust the DB status — do NOT call _refresh_workspace here
    # because if kube deletion partially failed during deactivation,
    # _refresh_workspace will report ACTIVE from Kubernetes even though
    # DB correctly says INACTIVE — causing activation to be rejected.
    if workspace.status is Status.ACTIVE:
        self.logger.error(
            f"Cannot activate {logname(workspace)} "
            f"while it's already {workspace.status}"
        )
        raise WorkspaceActivationFailedError(workspace.id)

    if workspace.status is Status.INACTIVE:
        # Trust DB — activate directly
        return self._activate_workspace(workspace)

    # Any other status — fail safely
    self.logger.error(
        f"Unknown status for {logname(workspace)}: {workspace.status}"
    )
    raise WorkspaceActivationFailedError(workspace.id)
```

## Fix 3 — `reactivate_workspaces_by_owner_account_id`

Find and replace the entire function:

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

## After applying all three

```bash
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
python app.py
celery -A dataviz_async.app:app worker
```

## What each fix does in simple terms

| Fix | What it fixes |
|---|---|
| `_activate_workspace` | Instead of creating a NEW kube stack, reuses the EXISTING one — preserving database and Grafana data |
| `reactivate_workspace` | Removes `_refresh_workspace` call that was asking Kubernetes for status — Kubernetes was reporting wrong status after partial deletion, causing reactivation to be rejected |
| `reactivate_workspaces_by_owner_account_id` | Calls `_activate_workspace` directly instead of going through `reactivate_workspace` — bypasses the Kubernetes status check entirely, trusts DB status instead |
