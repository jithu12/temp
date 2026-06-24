def _refresh_workspace(self, workspace_id: uuid.UUID) -> Workspace:
        """
        Re-derive the workspace status from its live dependencies and persist
        the result if it has changed.

        This is the canonical way to synchronise the workspace row with the
        real state of its underlying infrastructure (DNS, dataplane component,
        kube stack).  Called at the start of create_workspace and
        delete_workspace so that the worker always operates on an up-to-date
        status.
        """
        workspace = self.repositories.workspace.get_by_id(workspace_id)
        deps_status: Set[Status] = {
            workspace.dns.status,
            workspace.dataplane_component.status,
            workspace.kube_stack.status,
        }
        new_status = self.status_from_dependencies(workspace.status, deps_status)
        self._update_workspace_with(workspace, status=new_status)
        return self.repositories.workspace.get_by_id(workspace_id)

    def _poll_res_created(self, repository, resource_id: uuid.UUID) -> None:
        """
        Block until *resource_id* in *repository* reaches ACTIVE status.
        Raises if the resource reaches a terminal failure or the timeout
        expires (delegated to poll_resource_status).
        """
        poll_resource_status(
            pending=[Status.CREATION_REQUESTED, Status.CREATING],
            target=[Status.ACTIVE],
            refresh=lambda: repository.get_by_id(resource_id).status,
            timeout=POOLING_TIMEOUT,
        )

    def _poll_status(
        self,
        workspace: Workspace,
        pending: List[Status],
        target: List[Status],
    ) -> Workspace:
        """Block until the workspace leaves *pending* and enters *target*."""
        try:
            poll_resource_status(
                pending=pending,
                target=target,
                refresh=lambda: self._refresh_workspace(workspace_id=workspace.id).status,
                timeout=POOLING_TIMEOUT,
            )
        except Exception:
            self.logger.exception(
                f"There was an error while waiting for {logname(workspace)} "
                f"to reach '{[s.value for s in target]}'"
            )
        return self._refresh_workspace(workspace_id=workspace.id)
