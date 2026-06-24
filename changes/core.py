from typing import Callable
from dataviz_core.adapters.elastic import Elastic as ElasticAdapter
from dataviz_core.adapters.celery_workflow_executor import (
    celery_workflow_executor_from_config,
)
from dataviz_core.adapters.kube_cp_client import kube_cp_from_config
from dataviz_core.adapters.kube_rp_client import kube_rp_client_from_config
from dataviz_core.adapters.myvault_client import (
    myvault_client_from_config,
)
from dataviz_core.adapters.pki_client import pki_client_from_config
from dataviz_core.adapters.postgres_cp_client import pg_client_from_config
from dataviz_core.adapters.secret_client import secret_client_from_config
from dataviz_core.adapters.docker_image_client import get_default_docker_image_client
from dataviz_core.adapters.github_client import GithubClient
from dataviz_core.config import Config
from dataviz_core.repositories.sqlalchemy import (
    get_default_session_provider,
)
from dataviz_core.adapters.sg_connect_client import sg_connect_from_config
from dataviz_core.adapters.eventbus_adapter import EventBusAdapter
from dataviz_core.services.inventory import InventoryService
from dataviz_core.services.kube import KubeService
from dataviz_core.services.workspace import WorkspacesService
from dataviz_core.services.vault import VaultService
from dataviz_core.services.secret import SecretService
from dataviz_core.services.certificate import CertificateService
from dataviz_core.services.dataplane import DataplanesService
from dataviz_core.services.backup import BackupService
from dataviz_core.services.settings import SettingsService
from dataviz_core.services.dns import DNSService
from dataviz_core.services.ldo import LDOService
from dataviz_core.services.health import HealthService
from dataviz_core.services.eventbus import EventBusService
from dataviz_core.services.customer_requests import CustomerRequestsService
from dataviz_core.services.docker_image import DockerImageService

from dataviz_core.adapters.account_client import account_client_from_config
from dataviz_core.adapters.dns_client import dns_client_from_config
from dataviz_core.adapters.ldo_client import ldo_client
from dataviz_core.services.grafana import GrafanaService
from dataviz_core.services.accounts import AccountService
from dataviz_core.services.account_reconciliation import AccountReconciliationService
from dataviz_core.services.restore import RestoreService
from dataviz_core.adapters.monitoring_client import get_default_monitoring_client
from dataviz_core.utils.logging import get_default_logger, CallableLoggerMixin
from dataviz_core.services.sg_connect import SGConnectService


class DatavizCore(object):
    """A Façade to DatavizCore module"""

    def __init__(
        self,
        session_provider=None,
        config=None,
        logger=None,
        is_retrying_func: Callable[[], bool] = lambda: False,
    ) -> None:
        self.logger = logger if logger else get_default_logger(__name__)
        self.is_retrying_func = is_retrying_func
        self.logger.info("Initializing Dataviz Core...")

        self.logger.info("Setting up necessary Dataviz runtime configuration...")
        if config is None:
            config = {}
        self.config = Config.from_dict(config)

        self.logger.info("Setting up a session provider for the dataplane access...")
        self.session_provider = session_provider or get_default_session_provider(
            self.config.database_uri
        )

        self.logger.info("Setting up a workflow executor for asynchronous execution...")
        # This is component that will send tasks to be executed by Dataviz-Async to the queue broker
        self.workflow_executor = celery_workflow_executor_from_config(self.config)

        # Adapters/Clients setup section
        self.logger.info("Setting up services adapters/clients...")
        self.myvault_client = myvault_client_from_config(self.config)
        self.pki_client = pki_client_from_config(self.config)
        self.pg_client = pg_client_from_config(self.config)
        self.dns_client = dns_client_from_config(self.config)
        self.ldo_client = ldo_client()
        self.workflow_executor = celery_workflow_executor_from_config(self.config)
        self.account_client = account_client_from_config(self.config)
        self.secret_client = secret_client_from_config(self.config)
        self.kube_cp_client = kube_cp_from_config(self.config)
        self.kube_rp_client = kube_rp_client_from_config(self.config)
        self.docker_image_client = get_default_docker_image_client(self.config, self.kube_rp_client)
        self.sg_connect_client = sg_connect_from_config(self.logger)

        # Elastic adapter for Health service
        self._elastic_health = ElasticAdapter(
            self.config.health_elastic_endpoint_conf,
            logs_index=self.config.health_elastic_index,
            logger=self.logger,
        )
        self._git_client = GithubClient(
            api_url="https://sgithub.fr.world.socgen/api/v3",
            app_id=self.config.github_app_id,
            app_private_key_path=self.config.github_app_private_key,
            logger=self.logger,
        )

        # Services setup section
        self.logger.info("Setting up Dataviz Core Services...")
        self.settings_service = SettingsService(session_provider=self.session_provider)
        self.logger.info("Setting up Vault service...")
        self.vault = VaultService(
            engine=self.config.vault_engine,
            namespace=self.config.vault_namespace,
            vault_client=self.myvault_client,
            session_provider=self.session_provider,
            logger=self.logger,
        )

        self.logger.info("Setting up Dataplane service...")
        self.dataplane = DataplanesService(
            vault_service=self.vault,
            postgres_client=self.pg_client,
            workflow_executor=self.workflow_executor,
            session_provider=self.session_provider,
            settings_service=self.settings_service,
            logger=self.logger,
            is_retrying_func=self.is_retrying_func,
        )
        self.logger.info("Setting up SGConnect service...")
        self.sg_connect = SGConnectService(
            vault_service=self.vault,
            sg_connect_client=self.sg_connect_client,
            workflow_executor=self.workflow_executor,
            logger=self.logger,
            session_provider=self.session_provider,
        )

        self.logger.info("Setting up Kube service...")
        self.kube = KubeService(
            email=self.config.email,
            vault=self.vault,
            dataplane_service=self.dataplane,
            workflow_executor=self.workflow_executor,
            kube_cp_client=self.kube_cp_client,
            kube_client=self.kube_rp_client,
            session_provider=self.session_provider,
            kube_ns_network=self.config.kube_ns_network,
            kube_cluster_external_id=self.config.kube_cluster_external_id,
            sg_connect_service=self.sg_connect,
            logger=self.logger,
            is_retrying_func=self.is_retrying_func,
        )
        self.logger.info("Setting up Certificate service...")
        self.certificate = CertificateService(
            email=self.config.email,
            vault=self.vault,
            certificate_client=self.pki_client,
            session_provider=self.session_provider,
            workflow_executor=self.workflow_executor,
            logger=self.logger,
            is_retrying_func=self.is_retrying_func,
            kube_service=self.kube,
        )

        self.logger.info("Setting up DNS service...")
        self.dns = DNSService(
            certificate_service=self.certificate,
            dns_client=self.dns_client,
            myvault_service=self.vault,
            workflow_executor=self.workflow_executor,
            session_provider=self.session_provider,
            logger=self.logger,
            is_retrying_func=self.is_retrying_func,
        )

        self.logger.info("Setting up Docker Image service...")
        self.docker_image = DockerImageService(
            github_client=self._git_client,
            docker_image_client=self.docker_image_client,
            kube_client=self.kube_rp_client,
            workflow_executor=self.workflow_executor,
            session_provider=self.session_provider,
            logger=self.logger,
            is_retrying_func=self.is_retrying_func,
        )

        self.logger.info("Setting up LDO Service")
        self.ldo = LDOService(
            ldo_client=self.ldo_client,
            session_provider=self.session_provider,
            logger=self.logger,
        )

        self.logger.info("Setting up Account Service")
        self.account = AccountService(
            account_client=self.account_client,
            session_provider=self.session_provider,
            logger=self.logger,
            is_retrying_func=self.is_retrying_func,
        )

        self.logger.info("Setting up Secret Service")
        self.secret = SecretService(secret_client=self.secret_client)

        self.logger.info("Setting up Grafana Service")
        self.grafana = GrafanaService(
            account_service=self.account,
            vault_service=self.vault,
            kube_service=self.kube,
            secret_service=self.secret,
            session_provider=self.session_provider,
            workflow_executor=self.workflow_executor,
            logger=self.logger,
        )

        self.logger.info("Setting up Inventory service...")
        self.inventory = InventoryService(
            session_provider=self.session_provider,
            api_fqdn=self.config.fqdn_source,
            logger=self.logger,
        )

        self._eventbus_adapter = EventBusAdapter(
            eventbus_account=self.config.eventbus_credential,
            account_id=self.config.account_id,
            fqdn_source=self.config.fqdn_source,
            logger=self.logger,
        )

        self.eventbus = EventBusService(
            eventbus_adapter=self._eventbus_adapter,
            inventory_service=self.inventory,
            logger=self.logger,
        )

        # Initializing the Dataplane cluster to be used
        self.logger.info("Initializing Dataviz Workspaces backend cluster...")
        # FIXME: The dataplane needs to be autonomous,
        # i.e knows the cluster it creates components into
        cluster = self.dataplane.add_dataplane_cluster(self.config.dataplane_cluster_external_id)

        self.logger.info("Setting up Backup service...")
        self.backup = BackupService(
            postgres_client=self.pg_client,
            workflow_executor=self.workflow_executor,
            session_provider=self.session_provider,
            settings_service=self.settings_service,
            logger=self.logger,
            is_retrying_func=self.is_retrying_func,
        )

        self.logger.info("Setting up Backup Restore service...")
        self.restore_backup = RestoreService(
            postgres_client=self.pg_client,
            backup=self.backup,
            dataplane=self.dataplane,
            kube_service=self.kube,
            workflow_executor=self.workflow_executor,
            session_provider=self.session_provider,
            settings_service=self.settings_service,
            logger=self.logger,
            is_retrying_func=self.is_retrying_func,
        )

        self.logger.info("Setting up Workspace service...")
        # FIXME: The dataplane cluster id should not be given here, It should be in dataplane setup
        self.workspace = WorkspacesService(
            session_provider=self.session_provider,
            dataplane_cluster_id=cluster.id,
            dataplane_service=self.dataplane,
            dns_service=self.dns,
            ldo_service=self.ldo,
            kube_service=self.kube,
            grafana_service=self.grafana,
            eventbus_service=self.eventbus,
            restore_backup=self.restore_backup,
            account_service=self.account,
            sg_connect=self.sg_connect,
            workflow_executor=self.workflow_executor,
            logger=self.logger,
        )

        self.logger.info("Setting up Customer Requests service")
        self.customer_requests = CustomerRequestsService(
            vault_service=self.vault,
            kube_service=self.kube,
            workspace_service=self.workspace,
            grafana_service=self.grafana,
            backup_service=self.backup,
            workflow_executor=self.workflow_executor,
            logger=self.logger,
            session_provider=self.session_provider,
        )

        self.logger.info("Setting up Health service...")
        self.health = HealthService(
            logger=self.logger,
            logger_mixin=CallableLoggerMixin,
            region=str(self.config.region),
            session_provider=self.session_provider,
            elastic_adapter=self._elastic_health,
        )

        # Inject workspace service into account service to avoid circular dependency
        self.account.set_workspace_service(self.workspace)

        self.account.workflow_executor = self.workflow_executor

        # MonitoringClient — sends alert emails when residual workspaces are found
        # after Accounts Team confirms an account has been fully deleted (ResourceDeleted event).
        self.logger.info("Setting up Monitoring client...")
        self.monitoring = get_default_monitoring_client(logger=self.logger)
        self.account.set_monitoring_service(self.monitoring)

        # AccountService also handles lifecycle events consumed from Event Bus
        self.account_lifecycle_consumer = self.account

        # Initialize Account Reconciliation Service (optional, needs API client)
        # Note: Will skip gracefully if Accounts Team API not configured
        self.account_reconciliation = AccountReconciliationService(
            session_provider=self.session_provider,
            repository_context=None,
            accounts_api_client=None,
        )
        self.account_reconciliation.set_lifecycle_consumer(self.account)
        self.account_reconciliation.set_workspace_service(self.workspace)

        self.logger.info("Done initializing Dataviz Core.")