Yes absolutely. Let me think back carefully to what the code looked like **before** we touched any delete functions.

## What we changed in `workspace.py`

We modified three functions:
1. `_delete_workspace`
2. `delete_workspaces_by_owner_account_id`
3. `delete_workspace`

Here are the **original versions** before any of our changes:

## `_delete_workspace` — original

```python
def _delete_workspace(self, workspace: Workspace, is_failed: bool = False) -> Workspace:
    if not is_failed:
        self.logger.info(f"Deleting {logname(workspace)}...")
        workspace = self._update_workspace_with_and_return(
            workspace,
            status=Status.DELETING
        )

    try:
        self.sg_connect_service.remove_redirect_url(
            workspace.sg_connect,
            workspace.dns.fqdn
        )

        self._dataplane.request_component_deletion(
            component_id=workspace.dataplane_component.id
        )

        self._dataplane.vault.delete_secret(
            secret_id=workspace.dataplane_component.vault_secret_id
        )

        # TODO: Remove this once all workspace certificate migration completed
        if workspace.dns.certificate:
            self._dataplane.vault.delete_secret(
                secret_id=workspace.dns.certificate.vault_secret_id
            )

        if workspace.kube_stack.vault_secret_id:
            self._dataplane.vault.delete_secret(
                secret_id=workspace.kube_stack.vault_secret_id
            )
        else:
            self.logger.warning(
                "No vault_secret_id found for kube stack. Skipping secret deletion."
            )

        self._dns.request_dns_deletion(dns_id=workspace.dns_id)

        self._kube_service.request_namespace_deletion(
            namespace_id=workspace.kube_stack.kube_namespace.id,
            stack_id=workspace.kube_stack.id,
        )

        if not is_failed:
            workspace = self._update_workspace_with_and_return(
                workspace,
                status=Status.DELETED,
            )

    except Exception as e:
        self.logger.exception(
            f"{logname(workspace)}: '{workspace.name}' deletion failed"
        )
        self._update_workspace_with(workspace, status=Status.FAILED)
        raise WorkspaceDeletionFailedError(workspace.id) from e

    return self._update_workspace_with_and_return(
        workspace,
        status=Status.DELETED
    )
```

## `delete_workspace` — original

```python
def delete_workspace(
    self,
    workspace_id: uuid.UUID,
    is_failed: bool = False
) -> Workspace:

    workspace = self.repositories.workspace.get_by_id(workspace_id)
    workspace = self._refresh_workspace(workspace.id)

    self.logger.info(f"Starting {logname(workspace)} deletion")

    if workspace.status is Status.CREATING:
        self.logger.error(f"Cannot delete {logname(workspace)} while it's creating")
        raise WorkspaceDeletionFailedError(workspace.id)

    if workspace.status in [
        Status.ACTIVE,
        Status.DELETION_REQUESTED,
        Status.CREATION_REQUESTED,
    ]:
        return self._delete_workspace(workspace)

    if workspace.status is Status.DELETING:
        self.logger.error(f"{logname(workspace)} deletion already started")
        return self._poll_deletion(workspace)

    if workspace.status is Status.DELETED:
        self.logger.error(f"{logname(workspace)} already deleted")
        return workspace

    if workspace.status in [Status.FAILED, Status.RETRYING]:
        if is_failed:
            return self._delete_workspace(workspace, is_failed=is_failed)

        self.logger.error(
            f"Cannot delete {logname(workspace)} while it is in {workspace.status} status"
        )
        raise WorkspaceDeletionFailedError(workspace.id)

    self.logger.error(
        f"Unknown status for {logname(workspace)}: '{workspace.status}'"
    )
    raise WorkspaceDeletionFailedError(workspace.id)
```

## `delete_workspaces_by_owner_account_id` — original

```python
def delete_workspaces_by_owner_account_id(
    self,
    owner_account_id: uuid.UUID
) -> List[Dict]:

    workspace_updates = []

    filters = [
        FilteringCriterion("owner_account_id", owner_account_id),
        FilteringCriterion("status", Status.ACTIVE),
    ]

    workspaces = self.repositories.workspace.list(filters=filters)

    for workspace in workspaces:
        _workspace_updates = {}
        self.logger.info(f"Starting {logname(workspace)} deletion")

        if workspace.status is Status.DELETED:
            self.logger.error(f"{logname(workspace)} already deleted")
            _workspace_updates["workspace_id"] = str(workspace.id)
            _workspace_updates["status"] = "account status already in deleted state"
            _workspace_updates["name"] = workspace.name
            workspace_updates.append(_workspace_updates)
            continue

        if workspace.status in [
            Status.ACTIVE,
            Status.INACTIVE,
        ]:
            try:
                workspace_res = self.delete_workspace(workspace.id)
                _workspace_updates["workspace_id"] = str(workspace_res.id)
                _workspace_updates["status"] = "deleted"
                _workspace_updates["name"] = workspace_res.name
                workspace_updates.append(_workspace_updates)

            except WorkspaceDeletionFailedError as e:
                _workspace_updates["workspace_id"] = str(workspace.id)
                _workspace_updates["status"] = "deletion failed"
                _workspace_updates["name"] = workspace.name
                workspace_updates.append(_workspace_updates)
                self.logger.error(
                    f"Deletion failed for {logname(workspace)}. Error: {e}"
                )
                continue

    return workspace_updates
```

## Put all three back into `workspace.py`

Replace your current versions with these exact originals. Then:

```bash
# Clear pycache
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null

# Restart both
python app.py
celery -A dataviz_async.app:app worker
```

## Then test activate only

Before touching delete again, test just activate and deactivate with a real UUID. Check if the "workspace going DELETED during activate" issue still happens with the original code. If it does, the bug is **not** in the delete functions we changed — it's somewhere else (likely `_refresh_workspace` as I suspected).

Once you confirm activate/deactivate work cleanly with the original code, we can approach delete more carefully — one small change at a time, testing after each change.

Share the Celery log and the SQL before/after activate once you've reverted. That'll tell us exactly where the DELETED status is coming from.
