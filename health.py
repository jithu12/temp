from __future__ import annotations

import functools
from dataclasses import asdict as dataclass_asdict
from dataclasses import dataclass
from dataclasses import fields as dataclass_fields
from datetime import datetime, timezone
from enum import Enum
from math import ceil
from typing import TYPE_CHECKING

from dateutil.parser import parse as date_parse

from elasticsearch_dsl.response import Hit as DSLHit
from elasticsearch_dsl.search import MultiSearch
from elasticsearch_dsl.search import Search as DSLSearch

from dataviz_core.adapters.elastic import Elastic as ElasticAdapter
from dataviz_core.adapters.pki_client import PKIClient
from dataviz_core.adapters.kube_cp_client import KubeCPClient
from dataviz_core.adapters.dns_client import DNSClient
from dataviz_core.adapters.postgres_cp_client import PostgresClient
from dataviz_core.adapters.myvault_client import MyVaultClient
from dataviz_core.adapters.celery_workflow_executor import CeleryWorkflowExecutor
from dataviz_core.models.health import HealthCheckResult
from dataviz_core.services.session import SessionManagerMixin, SessionProvider
from dataviz_core.repositories.context import RepositoryContext
from dataviz_core.utils.logging import get_default_logger, CallableResult

if TYPE_CHECKING:
    from typing import Any, Optional, Type

    from dataviz_core.utils.logging import LoggerType


# =============================================================================
# Constants for the new Platform Health Standard
# =============================================================================
APP_NAME = "dataviz"
APP_DESCRIPTION = "Health status of the Dataviz Service"
APP_VERSION = "1.0"


# =============================================================================
# CallableLoggerResult — unchanged from original
# =============================================================================
@dataclass(frozen=True)
class CallableLoggerResult:
    """Result from an Elastic search hit for callable log."""

    account_id: Optional[str]
    action: str
    dependency_name: str
    result: CallableResult
    date: datetime
    container_name: Optional[str]

    @staticmethod
    def from_search_hit(search: DSLHit) -> CallableLoggerResult:
        # Circumvent mislabeled results bug (#484)
        CLASS_PREFIX = "CallableResult."
        enum_value = (
            search.details.result[len(CLASS_PREFIX) :]
            if search.details.result.startswith(CLASS_PREFIX)
            else search.details.result
        )
        result = CallableResult[enum_value]

        return CallableLoggerResult(
            account_id=getattr(search, "metadata_account_id", None),
            action=search.details.action,
            dependency_name=search.details.dependency_name,
            result=result,
            date=date_parse(search.agent_timestamp).replace(tzinfo=timezone.utc),
            container_name=getattr(search, "agent_hostname", None),
        )


# =============================================================================
# DepedencyHealthBlock — kept for internal use (not exposed in response)
# =============================================================================
@dataclass(frozen=True)
class DepedencyHealthBlock:
    """Dependency health block as returned by the API."""

    code: Optional[int]
    comment: Optional[str]
    environment: Optional[str]
    name: str
    status: HealthStatus

    def asdict(self) -> dict[str, str]:
        return {
            field.name: str(getattr(self, field.name))
            for field in dataclass_fields(self)
            if getattr(self, field.name) is not None
        }


# =============================================================================
# ModuleHealthBlock — UPDATED for new Platform Health Standard
# =============================================================================
@dataclass(frozen=True)
class ModuleHealthBlock:
    """Module health block conforming to the new Platform Health Standard.

    Fields:
        name: Name in the format <app-name>:<module-name>
        description: Human-readable description of the module
        status: Health status (UP, DEGRADED, DOWN)
        tags: Optional list of tags for categorization
        rootcause: Optional root cause message when module is not UP
    """

    name: str
    description: str
    status: HealthStatus
    tags: Optional[list[str]] = None
    rootcause: Optional[str] = None

    def asdict(self) -> dict[str, Any]:
        result = {
            "name": self.name,
            "description": self.description,
            "status": str(self.status),
        }
        if self.tags is not None:
            result["tags"] = self.tags
        if self.rootcause is not None:
            result["rootcause"] = self.rootcause
        return result


# =============================================================================
# HealthStatus enum — unchanged from original
# =============================================================================
@functools.total_ordering
class HealthStatus(Enum):
    DOWN = "DOWN"
    DEGRADED = "DEGRADED"
    UP = "UP"

    # The enum should keep this ordering.
    @classmethod
    def _order(cls) -> list[HealthStatus]:
        return [cls.DOWN, cls.DEGRADED, cls.UP]

    def __lt__(self, other: Any) -> bool:
        if isinstance(other, self.__class__):
            order = self._order()
            return order.index(self) < order.index(other)
        return NotImplemented

    def __str__(self) -> str:
        return self.value


# =============================================================================
# HealthResult — UPDATED for new Platform Health Standard
# =============================================================================
@dataclass(frozen=True)
class HealthResult:
    """Result for a health check, conforming to the new Platform Health Standard.

    Fields:
        description: Description of the service health
        version: Product version
        status: Global status of the service (UP, DEGRADED, DOWN)
        time: Timestamp when this health status was computed
        comment: Human-readable explanation of the current status
        modules: List of module health blocks
    """

    description: str
    version: str
    status: str
    time: str
    comment: str
    modules: list[dict[str, Any]]

    @staticmethod
    def from_modules(
        modules: list[dict[str, Any]],
        description: str = APP_DESCRIPTION,
        version: str = APP_VERSION,
    ) -> HealthResult:
        """Build a HealthResult from module health blocks.

        Computes the global application status from module statuses
        following the new Platform Health Standard evaluation rules:
        - If any module is DOWN -> app is DOWN
        - If any module is DEGRADED (and none DOWN) -> app is DEGRADED
        - If all modules are UP -> app is UP
        - If all modules are DOWN -> app is DOWN
        """
        if not modules:
            app_status = HealthStatus.UP
        else:
            module_statuses = [
                HealthStatus(m.get("status", "UP")) for m in modules
            ]
            # Rule: any module DOWN -> app DOWN
            if any(s == HealthStatus.DOWN for s in module_statuses):
                app_status = HealthStatus.DOWN
            # Rule: any module DEGRADED -> app DEGRADED
            elif any(s == HealthStatus.DEGRADED for s in module_statuses):
                app_status = HealthStatus.DEGRADED
            # Rule: all modules DOWN -> app DOWN
            elif all(s == HealthStatus.DOWN for s in module_statuses):
                app_status = HealthStatus.DOWN
            else:
                app_status = HealthStatus.UP

        # Dynamically compute comment
        comment = _build_health_comment(app_status, modules)

        return HealthResult(
            description=description,
            version=version,
            status=str(app_status),
            time=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f"),
            comment=comment,
            modules=modules,
        )

    def asdict(self) -> dict[str, Any]:
        return dataclass_asdict(self)


def _build_health_comment(
    app_status: HealthStatus, modules: list[dict[str, Any]]
) -> str:
    """Build a human-readable comment describing the current health state."""
    if app_status == HealthStatus.UP:
        return "All modules are healthy"

    # Collect modules that are not UP
    unhealthy = []
    for m in modules:
        m_status = m.get("status", "UP")
        if m_status != "UP":
            name = m.get("name", "unknown")
            unhealthy.append(f"{name} is {m_status}")

    if app_status == HealthStatus.DOWN:
        return f"Service is down: {', '.join(unhealthy)}"
    elif app_status == HealthStatus.DEGRADED:
        return f"Service is degraded: {', '.join(unhealthy)}"

    return "Health status unknown"


# =============================================================================
# Module descriptions for Dataviz service
# =============================================================================
MODULE_DESCRIPTIONS = {
    "application": "Core application module covering all Dataviz dependencies",
}


# =============================================================================
# HealthService — UPDATED for new Platform Health Standard
# =============================================================================
class HealthService(SessionManagerMixin):
    """Service which returns the health of the dependencies and modules,
    based on a sliding window mean composed of the most recent dependencies calls.
    Dependencies are inferred from the logger mixin subclasses,
    and modules are defined statically."""

    deps: list[Type]
    modules_deps: dict[str, list[Type]]
    region: str
    elastic: ElasticAdapter
    _deps_pretty_names: list[str]

    def __init__(
        self,
        session_provider: SessionProvider,
        logger_mixin: Type,
        # TODO: Enable once adapter part starting
        elastic_adapter: ElasticAdapter,
        region: str,
        repository_context: RepositoryContext = None,
        logger: Optional[LoggerType] = None,
        pretty_names: Optional[dict[Type, str]] = None,
    ):
        self.region = region
        self.logger = logger or get_default_logger(self.__class__.__name__)
        self.deps = [cls for cls in logger_mixin.__subclasses__()]
        super().__init__(session_provider, repository_context)
        pretty_names = pretty_names or {
            KubeCPClient: "Orchestrated Containers (Kube)",
            MyVaultClient: "MyVault",
            PostgresClient: "PCP (PostgreSQL)",
            CeleryWorkflowExecutor: "Messages (RMQ)",
            DNSClient: "DNS",
            PKIClient: "Certificate",
        }
        self._deps_pretty_names = [pretty_names.get(dep, dep.__name__) for dep in self.deps]

        self.modules_deps = {
            "application": [
                KubeCPClient,
                MyVaultClient,
                PostgresClient,
                CeleryWorkflowExecutor,
                DNSClient,
                PKIClient,
            ]
        }
        self.elastic = elastic_adapter

    def check_health(self) -> dict[str, Any]:
        """Return the cached health result, or a default UP response.

        Returns the health in the new Platform Health Standard format.
        """
        result = self.get_last_result()
        return (
            result
            or HealthResult.from_modules(
                [
                    ModuleHealthBlock(
                        name=f"{APP_NAME}:{mod}",
                        description=MODULE_DESCRIPTIONS.get(mod, f"Module {mod}"),
                        status=HealthStatus.UP,
                    ).asdict()
                    for mod in self.modules_deps
                ],
            ).asdict()
        )

    def get_last_result(self) -> Optional[dict[str, Any]]:
        """Get last cached result."""
        return getattr(self.repositories.health.get_last(), "content", None)

    def update_health_cache(self) -> None:
        """Update the cached health check result."""
        health_result = self.compute_health_result()
        health = HealthCheckResult(raw_content=health_result)
        with self.autocommit():
            self.repositories.health.insert(health)

    def compute_health_result(self) -> dict[str, Any]:
        """Compute the health result in the new Platform Health Standard format.

        This method:
        1. Gets dependency statuses for each module (internal, not exposed)
        2. Aggregates them into module-level health blocks
        3. Computes global app status from modules
        4. Returns the new standard format
        """
        modules = self.get_modules_blocks()

        return HealthResult.from_modules(modules).asdict()

    def get_all_dependencies_blocks(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, str]]:
        """Get the statuses of all dependencies inferred from the logger mixin subclasses
        and return them as a list of HealthBlock dicts."""
        result = []
        for dep_name, status in zip(
            self._deps_pretty_names,
            self.get_dependencies_statuses(
                self.deps,
                start_date,
                end_date,
                limit,
                offset,
            ),
        ):
            result.append(DepedencyHealthBlock(None, None, None, dep_name, status).asdict())

        return result

    def get_dependencies_statuses(
        self,
        deps: list[Type],
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[HealthStatus]:
        """Return the statuses of the provided dependencies,
        defaulting to UP if no log result has been found.
        Returned list is ordered according to the dependencies' original ordering."""
        ms = MultiSearch(
            # TODO: Take it form the args?
            index=self.elastic._logs_index,
            using=self.elastic.clients[self.region],
        )

        for dep in deps:
            search = self.build_logger_search(
                dep.__name__,
                None,
                start_date,
                end_date,
                limit,
                offset,
            )
            ms = ms.add(search)

        responses = ms.execute()
        result = []
        for res, dep in zip(responses, deps):
            logger_results = [CallableLoggerResult.from_search_hit(hit) for hit in res.hits]
            if not logger_results:
                self.logger.debug(f"No results for dependency {dep.__name__}")
            else:
                self.logger.debug(f"Got {len(logger_results)} hits for {dep.__name__}")
            result.append(
                self.compute_status(logger_results, limit) if logger_results else HealthStatus.UP
            )

        return result

    def build_logger_search(
        self,
        dep_name: str,
        action: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> DSLSearch:
        """Build a search for callable logger results."""
        search: DSLSearch = self.elastic.basic_search_builder(
            self.region,
            start_date,
            end_date,
            limit,
            offset,
        )
        search = search.filter("match_phrase", details__dependency_name=dep_name)
        if action:
            search = search.filter("match_phrase", details__action=action)

        return search

    def get_modules_blocks(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return module health blocks in the new Platform Health Standard format.

        Each module block includes name (in dataviz:<module> format),
        description, status, tags, and rootcause (if not UP).
        """
        result: list[dict[str, Any]] = []
        for service in self.modules_deps:
            module_name = f"{APP_NAME}:{service}"
            module_description = MODULE_DESCRIPTIONS.get(
                service, f"Module {service}"
            )

            # Get the worst dependency status for this module
            dep_statuses = self.get_dependencies_statuses(
                self.modules_deps[service],
                start_date,
                end_date,
                limit,
                offset,
            )
            module_status = min(dep_statuses)

            # Build rootcause if module is not UP
            rootcause = None
            if module_status != HealthStatus.UP:
                # Identify which dependencies are failing
                failing_deps = []
                dep_names = [dep.__name__ for dep in self.modules_deps[service]]
                for dep_name, dep_status in zip(dep_names, dep_statuses):
                    if dep_status != HealthStatus.UP:
                        failing_deps.append(f"{dep_name} is {dep_status}")
                rootcause = ", ".join(failing_deps) if failing_deps else None

            result.append(
                ModuleHealthBlock(
                    name=module_name,
                    description=module_description,
                    status=module_status,
                    tags=None,
                    rootcause=rootcause,
                ).asdict()
            )

        return result

    @staticmethod
    def compute_status(results: list[CallableLoggerResult], window_limit: int) -> HealthStatus:
        """Compute status from a list of callable log results limited to the first `limit` elements.
        Raise an exception if the provided list is empty or the limit is set to 0."""

        window_limit = min(window_limit, len(results))

        if window_limit == 0:
            raise ValueError("Window limit should be greater than 0")

        computed_window = slice(0, ceil(len(results) / 4))
        results_slice = results[computed_window]

        while computed_window.stop <= window_limit:
            success_percentage = sum(
                1 for log_entry in results_slice if log_entry.result == CallableResult.SUCCESS
            ) / len(results_slice)
            failure_percentage = sum(
                1 for log_entry in results_slice if log_entry.result == CallableResult.FAILURE
            ) / len(results_slice)

            if success_percentage >= 0.98:
                return HealthStatus.UP
            if failure_percentage >= 0.98:
                return HealthStatus.DOWN

            computed_window = slice(computed_window.stop, computed_window.stop * 2)
            results_slice = results[computed_window]

        success_percentage = sum(
            1 for log_entry in results if log_entry.result == CallableResult.SUCCESS
        ) / len(results)

        if 0.30 < success_percentage:
            return HealthStatus.DEGRADED

        return HealthStatus.DOWN
