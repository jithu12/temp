import datetime
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    Iterable,
    List,
    Optional,
    Set,
    Sequence,
    Union,
)
import time
import logging
import uuid
from requests.exceptions import ConnectionError
from uuid import UUID
from dataviz_core.adapters.workflow_executor import WorkflowExecutor
from dataviz_core.config.const import (
    POOLING_TIMEOUT,
    # WORKSPACE_LIMIT_PER_ACCOUNT,
    RETRY_SLEEP,
    GRAFANA_CUSTOM_CONFIGURATION,
    GRAFANA_DEFAULT_CONFIGURATION,
    ALIAS_GRAFANA_DEFAULT_CONFIGURATION,
    ADMIN_ACCOUNTS,
)
from dataviz_core.utils import logname, update_resource_with, _inject_owner_in_filters
from dataviz_core.utils.polling import poll_resource_status
from dataviz_core.models import Status
from dataviz_core.models.utils import format_date

from dataviz_core.services.session import SessionManagerMixin
from dataviz_core.services.filtering import FilteringCriterion

from dataviz_core.errors.exceptions import (
    WorkspaceLimitReachedError,
    WorkspaceCreationFailedError,
    WorkspaceDeletionFailedError,
    GenericInputError,
    NotOwnerError,
    WorkspaceAlreadyUsedError,
    NotFoundError,
    WorkspaceNotFoundError,
    BackupNotFoundError,
    WorkspaceStatusNotActiveError,
    GrafanaCustomConfigurationInputError,
    WorkspaceActivationFailedError,
    WorkspaceDeActivationFailedError,
)
from dataviz_core.models.restore import RestoreWorkspace

from dataviz_core.models.workspace import Workspace
from dataviz_core.utils.logging import get_default_logger

if TYPE_CHECKING:  # pragma: no cover:
    from dataviz_core.repositories.context import RepositoryContext
    from dataviz_core.repositories.base import RepositoryBase
    from dataviz_core.services.interfaces import SessionProvider
    from dataviz_core.services.dataplane import DataplaneService
    from dataviz_core.services.dns import DNSService
    from dataviz_core.services.kube import KubeService
    from dataviz_core.services.ldo import LDOService
    from dataviz_core.services.grafana import GrafanaService
    from dataviz_core.services.eventbus import EventBusService
    from dataviz_core.services.restore import RestoreService
    from dataviz_core.models.backup import BackupWorkspace
    from dataviz_core.services.accounts import AccountService
    from dataviz_core.services.sg_connect import SGConnectService

LoggerType = Union[logging.Logger, logging.LoggerAdapter]


class WorkspaceService(SessionManagerMixin):
    def __init__(
        self,
        dataplane_cluster_id: str,
        dataplane_service: "DataplaneService",
        dns_service: "DNSService",
        kube_service: "KubeService",
        ldo_service: "LDOService",
        grafana_service: "GrafanaService",
        eventbus_service: "EventBusService",
        restore_backup: "RestoreService",
        account_service: "AccountService",
        sg_connect: "SGConnectService",
        workflow_executor: WorkflowExecutor,
        session_provider: "SessionProvider",
        repository_context: Optional["RepositoryContext"] = None,
        logger: Optional[LoggerType] = None,
    ):
        self._dataplane_cluster_id = dataplane_cluster_id
        self._dataplane = dataplane_service
        self._dns = dns_service
        self._kube_service = kube_service
        self._ldo_service = ldo_service
        self._grafana = grafana_service
        self._eventbus = eventbus_service
        self._account = account_service
        self._restore_backup = restore_backup
        self._sg_connect_service = sg_connect
        self.workflow_executor = workflow_executor
        super().__init__(session_provider, repository_context)
        self.logger = logger if logger else get_default_logger(__name__)

    def get_workspace(self, workspace_id: uuid.UUID) -> Workspace:
        try:
            return self.repositories.workspace.get_by_id(id=workspace_id)
        except Exception:
            raise WorkspaceNotFoundError()

    def list(
        self,
        account_id: uuid.UUID,
        limit=None,
        offset=0,
        filters: Iterable[FilteringCriterion] = (),
    ) -> List[Workspace]:
        filters = _inject_owner_in_filters(account_id, filters)
        return [
            workspace
            for workspace in self.repositories.workspace.list(
                limit=limit,
                offset=offset,
                filters=filters,
            )
        ]

    def _replace_tags(self, id: UUID, tags: List[str]) -> Set[str]:
        return self._update_tags(id, tags)

    def _get_tags(self, id: UUID) -> Set[str]:
        return self.repositories.workspace.get_by_id(id).tags

    def _update_tags(self, id: UUID, tags: List[str]) -> Set[str]:
        uniq_tags = set(tags)
        if len(uniq_tags) > 50:
            raise GenericInputError("A resource cannot have more than 50 tags.")
        workspace = self.get_workspace(id)
        workspace = self._update_workspace_with_and_return(workspace, status=Status.UPDATING)
        with self.autocommit():
            self.repositories.workspace.update(id, db_tags=uniq_tags)
        workspace = self._update_workspace_with_and_return(workspace, status=Status.ACTIVE)
        # TODO: return direct tags from workspace
        return self._get_tags(id)

    def replace_tags(self, workspace_id: uuid.UUID, tags: List[str]) -> List[str]:
        return list(self._replace_tags(workspace_id, tags))

    def request_creation(
        self,
        name: str,
        tags: List[str],
        owner_account_id: str,
        description: str = None,
        grafana_version: UUID = None,
        backup_id: UUID = None,
    ) -> Workspace:
        backup: Union[BackupWorkspace, None] = None
        restore_backup_id: Union[None, UUID] = None
        # FIXME: Need to check BOTH kube namespace and DNS availability on DNS API
        # existing_ws = self.repositories.kube_namespace.query().filter_by(name=name)
        filters = [
            FilteringCriterion("name", name),
            FilteringCriterion(
                "status",
                [
                    Status.CREATION_REQUESTED,
                    Status.ACTIVE,
                    Status.CREATING,
                    Status.DELETING,
                    Status.DELETION_REQUESTED,
                ],
                op="in",
            ),
        ]
        existing_ws = self.repositories.workspace.list(filters=filters)
        if not grafana_version:
            filters = [
                FilteringCriterion("default", True),
            ]
            images = self.repositories.grafana_image.list(filters=filters)
            if images:
                grafana_image = images[0]
        else:
            grafana_image = self._grafana._get_grafana_image(grafana_version)
            grafana_image = self._grafana._check_image_status(grafana_image)

        if backup_id:
            backup = self.get_backup(backup_id)

        if existing_ws:
            raise WorkspaceAlreadyUsedError()
        workspace_count_per_account_id = self._get_workspace_count_per_account_id(owner_account_id)

        if workspace_count_per_account_id >= self._account.get_soft_limit(owner_account_id):
            raise WorkspaceLimitReachedError()

        # Setup useful variables
        common_uuid_prefix = uuid.uuid4()
        ns_suffix = f"dv-{str(uuid.uuid4())[:5]}"
        dns_fqdn = f"{name}-dataviz.eu-fr-paris.cloud.socgen"
        dataplane_component_name = f"a_{str(common_uuid_prefix)[:6]}"
        kube_stack_name = f"a-{str(common_uuid_prefix)[:6]}"

        # TODO: regroup dependencies creation requests using a single helper
        # TODO: regroup dependencies creation requests using a single helper

        # FIXME: Unify IDs on all workspace dependencies
        self.logger.info(
            f"Requesting kube namespace creation (name='{name}' suffix='{ns_suffix}')..."
        )
        kube_ns = self._kube_service.request_namespace_creation(
            suffix=ns_suffix,
        )
        kube_ns = self.repositories.kube_namespace.get_by_id(kube_ns.id)

        # FIXME: Add name attribute unified with the random generated ID
        self.logger.info(f"Requesting DNS creation (fqdn='{dns_fqdn}')...")
        dns = self._dns.request_dns_creation(
            fqdn=dns_fqdn, account_id=uuid.UUID(owner_account_id), namespace=kube_ns.id
        )

        self.logger.info(
            f"Requesting dataplane component creation (name='{dataplane_component_name}')..."
        )
        database = self._dataplane.request_component_creation(
            kube_ns.dataplane_cluster.id,
            database_name=dataplane_component_name,
        )

        self.logger.info(f"Requesting kube stack creation (name='{kube_stack_name}')...")
        stack = self._kube_service.request_stack_creation(
            ws_name=name,
            name=kube_stack_name,
            dns_id=dns.id,
            database_id=database.id,
            kube_namespace_id=kube_ns.id,
        )

        self.logger.info(f"Requesting SG Connect Client creation (dns='{dns_fqdn}')...")
        _sg_connect = self._sg_connect_service.request_sg_connect_client_id(dns_fqdn)

        workspace = Workspace(
            name=name,
            description=description,
            db_tags=tags,
            owner_account_id=owner_account_id,
            dns_id=dns.id,
            dataplane_component_id=database.id,
            kube_stack_id=stack.id,
            grafana_image_id=grafana_image.id,
            sg_connect_id=_sg_connect.id,
        )

        self.logger.info(f"Requesting Workspace creation (name='{name}')...")
        self.logger.debug(f"Inserting workspace in the database (name='{workspace.name}')...")
        with self.autocommit():
            workspace = self.repositories.workspace.insert(workspace)

        if backup:
            self.logger.info("Requesting for backup restore")
            restore_backup = self._restore_backup.request_restore_backup(
                backup_id=backup_id,
                workspace_id=workspace.id,
                owner_account_id=workspace.owner_account_id,
            )
            restore_backup_id = restore_backup.id

        self.logger.debug(
            f"Adding workspace(name='{workspace.name}' creation to the broker queue...)"
        )
        self.workflow_executor.async_exec_core_function(
            service="workspace",
            function="create_workspace",
            kwargs={"workspace_id": workspace.id, "restore_id": restore_backup_id},
        )
        self.logger.info(f"Workspace (name='{name}') and all of its dependencies are requested")

        return workspace
    


    def create_workspace(self, workspace_id: uuid.UUID, restore_id: uuid.UUID = None) -> Workspace:
        restore_ws = None
        workspace = self.repositories.workspace.get_by_id(workspace_id)
        if restore_id:
            restore_ws = self._restore_backup.get_restore(restore_id)
        workspace = self._refresh_workspace(workspace_id=workspace_id)
        if workspace.status in [Status.CREATION_REQUESTED, Status.CREATING]:
            self.logger.info(f"{logname(workspace)} will start being created")
            return self._create_workspace(workspace, restore_ws)
        if workspace.status == Status.ACTIVE:
            self.logger.info(f"{logname(workspace)} already created")
            return workspace
        else:
            self.logger.error(
                f"{logname(workspace)} cannot be created due to its status: {workspace.status}"
            )
            self._update_workspace_with(
                workspace=workspace,
                status=Status.FAILED,
            )
            raise WorkspaceCreationFailedError(workspace.id)

    def _create_workspace(
        self, workspace: Workspace, restore_ws: Union[RestoreWorkspace, None]
    ) -> Workspace:
        self.logger.info("waiting for dependencies to be created and in ACTIVE state")
        workspace = self._update_workspace_with_and_return(
            workspace=workspace,
            status=Status.CREATING,
        )
        try:
            # waiting for dependencies to be created and in ACTIVE state
            self.logger.debug("Waiting for dns creation...")
            self._poll_res_created(self.repositories.dns, workspace.dns.id)
            self.logger.debug("Waiting for certificate creation...")
            self._poll_res_created(self.repositories.certificate, workspace.dns.certificate.id)
            self.logger.debug(">>>>>: Waiting for dataplane component creation...")
            self._poll_res_created(
                self.repositories.dataplane_component, workspace.dataplane_component.id
            )
            self.logger.debug(">>>>>: Waiting for kube stack creation...")
            self._poll_res_created(self.repositories.kube_stack, workspace.kube_stack.id)
        except Exception as e:
            self.logger.info(
                f"{logname(workspace)}: Error has happened while waiting for "
                f"dependencies to be created"
            )
            self.logger.error(e, exc_info=True)
            workspace = self._update_workspace_with_and_return(
                workspace=workspace, status=Status.FAILED
            )
            self.request_workspace_deletion(
                workspace.id, workspace.owner_account_id, is_failed=True
            )
            return workspace

        if restore_ws:
            self.logger.debug(">>>>>: Waiting for Workspace restore creation...")
            self._poll_res_created(self.repositories.restore_workspace, restore_ws.id)
        else:
            self._ldo_service.request_ldo_account_creation(workspace)
            self.check_grafana_url_available(workspace)
            self._grafana.create_default_druid_datasources(workspace)
            self._grafana.create_default_infinity_datasources(workspace)
        workspace = self._update_workspace_with_and_return(
            workspace=workspace, status=Status.ACTIVE
        )

        # TODO : Remove after kube fix
        temp_kube_namespace = (
            self.repositories.temp_kube_namespace.query()
            .filter_by(kube_namespace_id=workspace.kube_stack.kube_namespace.id)
            .first()
        )
        self._kube_service.request_temp_namespace_deletion(
            temp_namespace_id=temp_kube_namespace.id, workspace=workspace
        )
        self._dataplane.request_cluster_replica_creation(
            workspace.dataplane_component.dataplane_cluster
        )

        return workspace

    def check_grafana_url_available(self, workspace, retry=0):
        try:
            self._grafana.poll_grafana_active(workspace)
        except ConnectionError:
            time.sleep(RETRY_SLEEP)
            if retry < 5:
                retry += 1
                return self.check_grafana_url_available(workspace, retry)
            else:
                self.logger.error(f"Grafana URL is not available: {workspace.dns.fqdn}")
                raise ConnectionError("Grafana URL is not available")

    def request_workspace_deletion(
        self, workspace_id: uuid.UUID, account_id: uuid.UUID, is_failed: bool = False
    ) -> Workspace:
        try:
            workspace = self.repositories.workspace.get_by_id(workspace_id)
        except Exception:
            raise WorkspaceNotFoundError()
        if workspace.owner_account_id != account_id:
            if str(account_id) not in ADMIN_ACCOUNTS:
                raise NotOwnerError(account_id, "workspace", workspace_id)
        self.logger.info(
            f"Requesting {logname(workspace)} deletion due to"
            f" {'Dependent service error' if is_failed else 'user request'}"
        )
        self.logger.debug(f"Requesting {logname(workspace)} deletion...")
        if workspace.status is Status.DELETED:
            self.logger.debug(f"{logname(workspace)} already deleted")
            return workspace
        if not is_failed:
            workspace = self._update_workspace_with_and_return(
                workspace, status=Status.DELETION_REQUESTED
            )

        self.workflow_executor.async_exec_core_function(
            service="workspace",
            function="delete_workspace",
            kwargs={"workspace_id": workspace.id, "is_failed": is_failed},
        )
        return workspace

    def delete_workspace(self, workspace_id: uuid.UUID, is_failed: bool = False) -> Workspace:
        workspace = self.repositories.workspace.get_by_id(workspace_id)
        workspace = self._refresh_workspace(workspace.id)

        self.logger.info(f"Starting {logname(workspace)} deletion")

        if workspace.status is Status.CREATING:
            self.logger.error(f"Cannot delete {logname(workspace)} while it's creating")
            raise WorkspaceDeletionFailedError(workspace.id)

        if workspace.status in [
            Status.ACTIVE,
            Status.INACTIVE,  # <- ADD THIS
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
            # <- also handle FAILED without is_failed flag
            self.logger.warning(f"Deleting {logname(workspace)} from FAILED state")
            return self._delete_workspace(workspace)

        self.logger.error(f"Unknown status for {logname(workspace)}: '{workspace.status}'")
        raise WorkspaceDeletionFailedError(workspace.id)

    def _delete_workspace(
        self,
        workspace: Workspace,
        is_failed: bool = False,
    ) -> Workspace:

        if not is_failed:
            self.logger.info(f"Deleting {logname(workspace)}...")
            workspace = self._update_workspace_with_and_return(
                workspace,
                status=Status.DELETING,
            )

        try:
            if workspace.sg_connect is not None:
                self._sg_connect_service.remove_redirect_url(
                    workspace.sg_connect,
                    workspace.dns.fqdn,
                )
            else:
                self.logger.warning(
                    f"{logname(workspace)}: No sg_connect found - "
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
                    f"{logname(workspace)}: No kube stack vault secret found. "
                    f"Skipping secret deletion."
                )

            self._dns.request_dns_deletion(
                dns_id=workspace.dns.id
            )

            self._kube_service.request_namespace_deletion(
                namespace_id=workspace.kube_stack.kube_namespace.id,
                stack_id=workspace.kube_stack.id,
            )

        except Exception:
            self.logger.exception(
                f"{logname(workspace)}: '{workspace.name}' deletion failed."
            )
            workspace = self._update_workspace_with_and_return(
                workspace,
                status=Status.DELETE_FAILED,
            )
            return workspace

        pending = [Status.DELETING]
        target = [Status.DELETED]

        try:
            poll_resource_status(
                pending=pending,
                target=target,
                refresh=lambda: self._refresh_workspace(
                    workspace_id=workspace.id
                ).status,
                timeout=POOLING_TIMEOUT,
            )
        except Exception:
            self.logger.exception(
                f"There was an error while waiting for "
                f"{logname(workspace)} to reach "
                f"'{[s.value for s in target]}'"
            )

        return self._refresh_workspace(workspace_id=workspace.id)

    def _poll_deletion(self, workspace: Workspace) -> Workspace:
        return self._poll_status(
            workspace=workspace, pending=[Status.DELETING], target=[Status.DELETED]
        )

    @staticmethod
    def to_dict(workspace: Workspace) -> Dict[str, Any]:
        return {
            "id": str(workspace.id),
            "name": workspace.name,
            "status": workspace.status.value,
            "fqdn": f"https://{workspace.dns.fqdn}" if workspace.dns else "",
            "tags": workspace.tags,
            "imageId": (str(workspace.grafana_image.id) if workspace.grafana_image else ""),
            "version": (
                workspace.grafana_image.grafana_version.version if workspace.grafana_image else ""
            ),
            "description": workspace.description,
            "insertionDate": format_date(workspace.insertion_date),
            "updateDate": format_date(workspace.update_date),
            "creationDate": format_date(workspace.creation_date),
            "deletionDate": format_date(workspace.deletion_date),
        }

    def status_from_dependencies(self, current_status: Status, deps_status: Set[Status]) -> Status:
        creating = {Status.CREATING, Status.CREATION_REQUESTED}
        updating = {Status.UPDATE_REQUESTED, Status.UPDATING}
        deleting = {Status.DELETING, Status.DELETION_REQUESTED, Status.DELETED}
        crea_or_act = {Status.ACTIVE} | creating
        not_del = crea_or_act | updating | {Status.FAILED}

        #: At least 1 FAILED -> FAILED
        if Status.FAILED in deps_status:
            return Status.FAILED
        #: All the same -> same
        if len(deps_status) == 1:
            return next(iter(deps_status))
        #: Mix of deleting and not deleting -> FAILED
        if deps_status & creating and deps_status & deleting:
            return Status.FAILED
        if deps_status & {Status.ACTIVE} and deps_status & deleting:
            return Status.DELETING
        #: Some deps are being created -> CREATING
        if current_status in ({Status.FAILED} | crea_or_act) and deps_status & creating:
            return Status.CREATING
        #: Some updating (the rest ACTIVE) -> UPDATING
        if current_status in not_del and deps_status & updating:
            return Status.UPDATING
        #: Workspace not creating nor updating and deps deleting -> DELETING
        if current_status in ({Status.ACTIVE, Status.FAILED} | deleting) and deps_status < deleting:
            return Status.DELETING
        #: Any other -> FAILED
        return Status.FAILED

    def _get_workspace_count_per_account_id(self, owner_account_id: str) -> int:
        """
        Function used to get the total count of Active, Creation Requested and
        Creating Workspace per account to restrict the user to create unwanted WS
        """
        filters = [
            FilteringCriterion("owner_account_id", str(owner_account_id)),
            FilteringCriterion(
                "status",
                [
                    Status.CREATION_REQUESTED,
                    Status.ACTIVE,
                    Status.CREATING,
                ],
                op="in",
            ),
        ]
        list_of_workspaces = self.repositories.workspace.list(filters=filters)
        return len(list_of_workspaces)
    
    def _refresh_workspace(self, workspace_id: uuid.UUID) -> Workspace:
        workspace = self.repositories.workspace.get_by_id(workspace_id)
        self.logger.info(f"Refreshing {logname(workspace)}...")

        if workspace.status in [
            Status.CREATION_REQUESTED,
            Status.DELETION_REQUESTED,
            Status.DELETED,
        ]:
            self.logger.info(
                f"{logname(workspace)} status not changed: {workspace.status}"
            )
            return workspace

        if workspace.dns is not None:
            self._dns._refresh_dns(workspace.dns)
        else:
            raise NotFoundError("Workspace", "DNS")

        if workspace.dataplane_component is not None:
            self._dataplane._refresh_component(workspace.dataplane_component)
        else:
            raise NotFoundError("Workspace", "Dataplane")

        # if workspace.kube_stack is not None:
        #     self._kube_service._refresh_stack(workspace.kube_stack)

        flatten_status = set(
            [
                workspace.dns.status,
                workspace.dataplane_component.status,
                # workspace.kube_stack.status,
            ]
        )

        return self._update_workspace_with_and_return(
            workspace,
            status=self.status_from_dependencies(
                Status(workspace.status),
                flatten_status,
            ),  # FIXME: typing issue here
        )
    
    def delete_failed_resources_daily(self):
        filters = [
            FilteringCriterion(
                "status",
                [
                    Status.CREATION_REQUESTED,
                    Status.ACTIVE,
                    Status.CREATING,
                ],
                op="not_in",
            ),
            FilteringCriterion(
                "creation_date",
                datetime.datetime.utcnow() - datetime.timedelta(days=1),
                op="gte",
            ),
        ]
        ws_list = self.repositories.workspace.list(filters=filters)
        for ws in ws_list:
            self._dns.delete_dns(ws.dns.id)
            self._dns_certificate.delete_certificate(ws.dns.certificate.id)
            self._dataplane.delete_component(ws.dataplane_component.id)
            self._kube_service.request_stack_deletion(ws.kube_stack)
            self._update_workspace_with_and_return(ws, status=Status.DELETED)

    def get_backup(self, backup_id: UUID) -> "BackupWorkspace":
        try:
            backup = self.repositories.backup_workspace.get_by_id(backup_id)
        except Exception as e:
            self.logger.error(f"Error while getting backup. Error: {e}")
            raise BackupNotFoundError()
        return backup

    def upgrade_workspace(self, workspace_id: UUID) -> Workspace:
        """
        Upgrades the specified workspace.

        Parameters:
            workspace_id (UUID): The UUID of the workspace to upgrade.

        Returns:
            Workspace: The upgraded workspace.

        Raises:
            None
        """
        workspace = self.get_workspace(workspace_id)
        self.logger.info(f"Upgrading {logname(workspace)}...")
        if workspace.status == Status.UPDATING:
            return workspace
        elif workspace.status == Status.UPDATE_REQUESTED:
            workspace = self._update_workspace_with_and_return(
                workspace=workspace, status=Status.UPDATING
            )
            workspace = self._upgrade_workspace(workspace)
        elif workspace.status != Status.ACTIVE:
            self.logger.error(
                f"Cannot reset {logname(workspace)} while it's in {workspace.status} status"
            )
            return workspace
        return workspace

    def _upgrade_workspace(self, workspace: Workspace) -> Workspace:
        """
        Upgrades the given workspace by upgrading the kube stack and updating the workspace status.

        Args:
            workspace (Workspace): The workspace to be upgraded.

        Returns:
            Workspace: The upgraded workspace.

        Raises:
            None
        """
        self.logger.info(f"Upgrading {logname(workspace)}...")
        try:
            kube_stack = self._kube_service.upgrade_kube_setup(workspace.kube_stack)
            if kube_stack.status == Status.ACTIVE:
                workspace = self._update_workspace_with_and_return(workspace, status=Status.ACTIVE)
            else:
                self.logger.error(f"Upgrade failed for {logname(workspace)}")
                workspace = self._update_workspace_with_and_return(workspace, status=Status.FAILED)
        except Exception as e:
            self.logger.error(f"Upgrade failed for {logname(workspace)}. Error: {e}")
            workspace = self._update_workspace_with_and_return(workspace, status=Status.FAILED)
        return workspace

    def request_workspace_custom_configuration(
        self,
        workspace_id: UUID,
        configuration: Dict,
        owner_account_id: str,
    ):
        """
        Update the custom configuration of a workspace.

        Args:
            workspace_id (UUID): The ID of the workspace.
            configuration (Dict): A dictionary containing the custom configuration to be updated.
            owner_account_id (str): The ID of the owner account.

        Returns:
            Workspace: The updated workspace object.
            Returns:
            Workspace: The updated workspace object.

        Raises:
            NotOwnerError: If the owner account ID does not match the workspace's owner account ID.
            WorkspaceStatusNotActiveError: If the workspace status is not active.
            GrafanaCustomConfigurationInputError: If an invalid configuration key is provided.
        """
        workspace = self.get_workspace(workspace_id)
        if str(workspace.owner_account_id) != owner_account_id:
            raise NotOwnerError(owner_account_id, "workspace", workspace_id)
        if workspace.status != Status.ACTIVE:
            raise WorkspaceStatusNotActiveError(workspace_id)
        is_sg_connect_value = None
        for each_key, each_value in configuration.items():
            try:
                key = GRAFANA_CUSTOM_CONFIGURATION[each_key]
                _each_value = WorkspaceService.parse_config_values(each_value)
                GRAFANA_DEFAULT_CONFIGURATION[key] = _each_value
                if each_key == "isPublicWorkspace":
                    is_sg_connect_value = configuration.get("isPublicWorkspace")
            except KeyError:
                raise GrafanaCustomConfigurationInputError(f"Invalid configuration key: {each_key}")
        update_kwargs = {
            "status": Status.UPDATE_REQUESTED,
            "custom_configurations": GRAFANA_DEFAULT_CONFIGURATION,
        }

        if is_sg_connect_value is not None:
            update_kwargs["is_sg_connect"] = is_sg_connect_value

        workspace = self._update_workspace_with_and_return(workspace, **update_kwargs)

        self.workflow_executor.async_exec_core_function(
            service="workspace",
            function="upgrade_workspace",
            kwargs={"workspace_id": workspace.id},
        )
        return workspace

    @staticmethod
    def parse_config_values(config_val: Union[bool, List[str]]) -> str:
        """
        Converts the given configuration value to a string representation.

        Parameters:
            config_val (Union[bool, List[str]]): The configuration value to be converted.

        Returns:
            str: The string representation of the configuration value.

        """
        if isinstance(config_val, bool):
            return str(config_val).lower()
        return ",".join(config_val)

    @staticmethod
    def process_config_values(value: str) -> Union[bool, List[str]]:
        """
        Converts the given configuration value to its original type.

        Parameters:
            value (str): The configuration value to be converted.

        Returns:
            Union[bool, List[str]]: The original type of the configuration value.

        """
        if value.lower() == "true":
            return True
        elif value.lower() == "false":
            return False
        else:
            return value.split(",") if value else []

    @staticmethod
    def get_workspace_alias_config(workspace_config: Dict) -> Dict[str, Any]:
        """
        Returns the alias configuration for a workspace.

        Args:
            workspace_config (Dict): The configuration of the workspace.

        Returns:
            Dict[str, Any]: The alias configuration for the workspace.

        """
        if workspace_config:
            res = {
                _k: WorkspaceService.process_config_values(workspace_config[_v])
                for _k, _v in GRAFANA_CUSTOM_CONFIGURATION.items()
                if _v in workspace_config
            }
        else:
            res = ALIAS_GRAFANA_DEFAULT_CONFIGURATION
        return res

    @staticmethod
    def workspace_config_to_dict(workspace: Workspace) -> Dict[str, Any]:
        """
        Converts a Workspace object to a dictionary representation.

        Parameters:
            workspace (Workspace): The Workspace object to convert.

        Returns:
            dict: A dictionary representation of the Workspace object with the following keys:
                - "configuration" (dict): The custom configurations of the workspace.
        """
        config = WorkspaceService.get_workspace_alias_config(workspace.custom_configurations)
        return {
            "configuration": config,
        }

    def deactivate_workspaces_by_owner_account_id(self, owner_account_id: uuid.UUID) -> List[Dict]:
        workspace_updates = []
        filters = [
            FilteringCriterion("owner_account_id", owner_account_id),
            FilteringCriterion("status", Status.ACTIVE),
        ]
        workspaces = self.repositories.workspace.list(filters=filters)

        for workspace in workspaces:
            _workspace_updates = {}
            self.logger.info(f"Starting {logname(workspace)} deactivation")

            if workspace.status is not Status.ACTIVE:
                self.logger.error(
                    f"Cannot deactivate {logname(workspace)} while it's {workspace.status}"
                )
                _workspace_updates["workspace_id"] = str(workspace.id)
                _workspace_updates["status"] = (
                    f"account status not in active state. "
                    f"Current status is {workspace.status.value}"
                )
                _workspace_updates["name"] = workspace.name
                workspace_updates.append(_workspace_updates)
                continue

            if workspace.status is Status.ACTIVE:
                try:
                    workspace_res = self._deactivate_workspace(workspace)
                    _workspace_updates["workspace_id"] = str(workspace_res.id)
                    _workspace_updates["status"] = "deactivated"
                    _workspace_updates["name"] = workspace_res.name
                    workspace_updates.append(_workspace_updates)
                except WorkspaceDeActivationFailedError as e:
                    _workspace_updates["workspace_id"] = str(workspace.id)
                    _workspace_updates["status"] = "deactivation failed"
                    _workspace_updates["name"] = workspace.name
                    workspace_updates.append(_workspace_updates)
                    self.logger.error(f"Deactivation failed for {logname(workspace)}. Error: {e}")
                    continue

            if workspace.status is Status.INACTIVE:
                self.logger.error(f"{logname(workspace)} already inactive")
                _workspace_updates["workspace_id"] = str(workspace.id)
                _workspace_updates["status"] = "account status already in inactive state"
                _workspace_updates["name"] = workspace.name
                workspace_updates.append(_workspace_updates)
                continue

        return workspace_updates

    def _deactivate_workspace(self, workspace: Workspace) -> Workspace:
        self.logger.info(f"Deactivating {logname(workspace)}...")

        try:
            self._kube_service.request_stack_deletion(
                workspace.kube_stack,
            )
        except Exception:
            self.logger.exception(f"{logname(workspace)}: '{workspace.name}' kube deletion failed.")
            return self._update_workspace_with_and_return(workspace, status=Status.FAILED)

        return self._update_workspace_with_and_return(workspace, status=Status.INACTIVE)

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
                self.logger.error(f"Reactivation failed for {logname(workspace)}. Error: {e}")
                continue

        self.logger.info(
            f"Workspace reactivation process completed "
            f"and workspace updates collected {workspace_updates}."
        )
        return workspace_updates

    def reactivate_workspace(self, workspace_id: uuid.UUID) -> Workspace:
        workspace = self.repositories.workspace.get_by_id(workspace_id)

        self.logger.info(f"Starting {logname(workspace)} Re-Activation")

        # Trust the DB status — do NOT call _refresh_workspace here
        # because if kube deletion partially failed during deactivation,
        # _refresh_workspace will report ACTIVE from Kubernetes even though
        # DB correctly says INACTIVE — causing activation to be rejected.
        if workspace.status is Status.ACTIVE:
            self.logger.error(
                f"Cannot activate {logname(workspace)} while it's already {workspace.status}"
            )
            raise WorkspaceActivationFailedError(workspace.id)

        if workspace.status is Status.INACTIVE:
            # Trust DB — activate directly
            return self._activate_workspace(workspace)

        # Any other status — fail safely
        self.logger.error(f"Unknown status for {logname(workspace)}: {workspace.status}")
        raise WorkspaceActivationFailedError(workspace.id)

    def _activate_workspace(self, workspace: Workspace) -> Workspace:
        self.logger.info(f"Reactivating {logname(workspace)}...")

        try:
            kube_stack = workspace.kube_stack

            if kube_stack is None:
                self.logger.error(f"{logname(workspace)}: No kube stack found — cannot reactivate")
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
        except Exception:
            self.logger.exception(
                f"{logname(workspace)}: '{workspace.name}' kube reactivation failed."
            )
            return self._update_workspace_with_and_return(workspace, status=Status.FAILED)

        return self._update_workspace_with_and_return(
            workspace,
            status=Status.ACTIVE,
        )

    def delete_workspaces_by_owner_account_id(
        self,
        owner_account_id: uuid.UUID,
    ) -> List[Dict]:
        workspace_updates = []

        # Fetch ALL workspaces for this owner regardless of status
        # so INACTIVE and FAILED workspaces are also deleted
        filters = [
            FilteringCriterion("owner_account_id", owner_account_id),
        ]

        workspaces = self.repositories.workspace.list(filters=filters)

        for workspace in workspaces:
            _workspace_updates = {}
            self.logger.info(f"Starting {logname(workspace)} deletion")

            if workspace.status is Status.DELETED:
                self.logger.info(f"{logname(workspace)} already deleted - skipping")
                _workspace_updates["workspace_id"] = str(workspace.id)
                _workspace_updates["status"] = "already deleted"
                _workspace_updates["name"] = workspace.name
                workspace_updates.append(_workspace_updates)
                continue

            if workspace.status in {
                Status.ACTIVE,
                Status.INACTIVE,
                Status.FAILED,
            }:
                try:
                    workspace_res = self.delete_workspace(workspace.id)

                    _workspace_updates["workspace_id"] = str(workspace_res.id)
                    _workspace_updates["status"] = "deleted"
                    _workspace_updates["name"] = workspace_res.name
                    workspace_updates.append(_workspace_updates)

                except WorkspaceDeletionFailedError as e:
                    # Even if deletion failed, force status to DELETED
                    self.logger.error(
                        f"Deletion failed for {logname(workspace)}. "
                        f"Forcing DELETED status. Error: {e}"
                    )

                    self._update_workspace_with(
                        workspace,
                        status=Status.DELETED,
                    )

                    _workspace_updates["workspace_id"] = str(workspace.id)
                    _workspace_updates["status"] = "deleted"
                    _workspace_updates["name"] = workspace.name
                    workspace_updates.append(_workspace_updates)

        return workspace_updates