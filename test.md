Yes you are correct! All three should show FAILED if anything goes wrong. Here are all three fixes:
Fix 1 — _deactivate_workspace

def _deactivate_workspace(self, workspace: Workspace) -> Workspace:
    self.logger.info(f"Deactivating {logname(workspace)}...")
    try:
        self._kube_service.request_stack_deletion(
            workspace.kube_stack,
        )
    except Exception as e:
        self.logger.exception(
            f"{logname(workspace)}: '{workspace.name}' kube deletion failed."
        )
        return self._update_workspace_with_and_return(
            workspace, status=Status.FAILED
        )
    return self._update_workspace_with_and_return(
        workspace, status=Status.INACTIVE
    )


Fix 2 — _activate_workspace

def _activate_workspace(self, workspace: Workspace) -> Workspace:
    self.logger.info(f"Reactivating {logname(workspace)}...")
    try:
        kube_stack = workspace.kube_stack

        if kube_stack is None:
            self.logger.error(
                f"{logname(workspace)}: No kube stack found — cannot reactivate"
            )
            raise WorkspaceActivationFailedError(workspace.id)

        self._kube_service._update_stack_with(
            kube_stack,
            status=Status.CREATION_REQUESTED,
        )

        self._kube_service.workflow_executor.async_exec_core_function(
            service="kube",
            function="reactivate_stack",
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
            f"{logname(workspace)}: '{workspace.name}' kube reactivation failed."
        )
        return self._update_workspace_with_and_return(
            workspace, status=Status.FAILED
        )

    return self._update_workspace_with_and_return(
        workspace, status=Status.ACTIVE,
    )


Fix 3 — _delete_workspace

def _delete_workspace(self, workspace: Workspace, is_failed: bool = False) -> Workspace:
    if not is_failed:
        self.logger.info(f"Deleting {logname(workspace)}...")
        workspace = self._update_workspace_with_and_return(
            workspace,
            status=Status.DELETING
        )

    try:
        if workspace.sg_connect is not None:
            self.sg_connect_service.remove_redirect_url(
                workspace.sg_connect,
                workspace.dns.fqdn
            )
        else:
            self.logger.warning(
                f"{logname(workspace)}: No sg_connect found — "
                f"skipping redirect URL removal."
            )

        self._dataplane.request_component_deletion(
            component_id=workspace.dataplane_component.id
        )

        self._dataplane.vault.delete_secret(
            secret_id=workspace.dataplane_component.vault_secret_id
        )

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

    except Exception as e:
        self.logger.exception(
            f"{logname(workspace)}: '{workspace.name}' deletion failed."
        )
        return self._update_workspace_with_and_return(
            workspace, status=Status.FAILED
        )

    return self._update_workspace_with_and_return(
        workspace, status=Status.DELETED
    )


Full status truth table after all three fixes



|Operation |Success                                     |Failure                                 |
|----------|--------------------------------------------|----------------------------------------|
|Deactivate|workspace → `INACTIVE`, account → `INACTIVE`|workspace → `FAILED`, account → `FAILED`|
|Activate  |workspace → `ACTIVE`, account → `ACTIVE`    |workspace → `FAILED`, account → `FAILED`|
|Delete    |workspace → `DELETED`, account → `DELETED`  |workspace → `FAILED`, account → `FAILED`|

And from FAILED — admin can always retry



|Current state|Can do            |
|-------------|------------------|
|`FAILED`     |deactivate again ✅|
|`FAILED`     |activate again ✅  |
|`FAILED`     |delete ✅          |

Status is now always honest. No more silent failures. 🎉​​​​​​​​​​​​​​​​
