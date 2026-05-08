import itertools
from datetime import datetime
from random import randint
from typing import List, Optional
from unittest.mock import Mock

import pytest
from elasticsearch import Elasticsearch
from elasticsearch_dsl.response import Hit
from pytest_mock import MockerFixture

from dataviz_core.adapters.elastic import Elastic as ElasticAdapter
from dataviz_core.repositories.context import RepositoryContext
from dataviz_core.services.health import (
    CallableLoggerResult,
    CallableResult,
    DepedencyHealthBlock,
    HealthResult,
    HealthService,
    HealthStatus,
    ModuleHealthBlock,
    _build_health_comment,
)


def build_mocked_multisearch_client(mocker: MockerFixture, hits: List[List[Hit]]) -> Elasticsearch:
    client = mocker.MagicMock(spec=Elasticsearch)
    responses = {}
    responses["responses"] = []

    for h in hits:
        res = {"_hits": h}
        responses["responses"].append(res)

    client.msearch.return_value = responses
    return client


def build_hits_from_status(status: HealthStatus, len: int = 12):
    def hit_factory(result):
        return Hit(
            {
                "_index": "my-index-2022.11",
                "_type": "_doc",
                "_id": "87362a38-cd83-4aad-bd41-f86281ef3394",
                "_version": 1,
                "_score": None,
                "_source": {
                    "agent_timestamp": "2022-11-14T11:20:14.662820Z",
                    "message": "MyVaultClient: read_secret has succeeded",
                    "details": {
                        "dependency_name": "MyVaultClient",
                        "result": str(result),
                        "action": "read_secret",
                    },
                    "agent_hostname": "async_name",
                    "agent_offset": "+01:00",
                    "metadata_account_id": "acc_id",
                    "metadata_resource_id": "dep_id",
                    "metadata_resourceset_id": "env_id",
                    "metadata_xaas_key": "ccp",
                    "metadata_product_name": "logs",
                    "metadata_kafka_topic": "my_index",
                    "metadata_product_version": "1.0",
                    "metadata_region": "eu-fr-north",
                },
            }
        )

    if status == HealthStatus.UP:
        return [hit_factory(CallableResult.SUCCESS) for _ in range(len)]
    elif status == HealthStatus.DEGRADED:
        # 50 %
        return [
            *itertools.islice(
                itertools.cycle(
                    [
                        hit_factory(CallableResult.FAILURE),
                        hit_factory(CallableResult.SUCCESS),
                    ]
                ),
                len,
            )
        ]
    else:
        return [hit_factory(CallableResult.FAILURE) for _ in range(len)]


@pytest.mark.unit
def test_status_order():
    assert HealthStatus.DOWN < HealthStatus.DEGRADED < HealthStatus.UP


# =============================================================================
# NEW TESTS: ModuleHealthBlock
# =============================================================================

@pytest.mark.unit
def test_module_health_block_asdict_up():
    """ModuleHealthBlock.asdict() should return correct keys in alphabetical order."""
    block = ModuleHealthBlock(
        name="dataviz:application",
        description="Core application module",
        status=HealthStatus.UP,
    )
    result = block.asdict()
    assert result == {
        "description": "Core application module",
        "name": "dataviz:application",
        "status": "UP",
    }
    # Verify key order is alphabetical
    assert list(result.keys()) == ["description", "name", "status"]


@pytest.mark.unit
def test_module_health_block_asdict_with_rootcause():
    """ModuleHealthBlock.asdict() should include rootcause when module is not UP."""
    block = ModuleHealthBlock(
        name="dataviz:application",
        description="Core application module",
        status=HealthStatus.DOWN,
        rootcause="PostgresClient is DOWN",
    )
    result = block.asdict()
    assert result["rootcause"] == "PostgresClient is DOWN"
    assert result["status"] == "DOWN"


@pytest.mark.unit
def test_module_health_block_asdict_no_null_tags():
    """ModuleHealthBlock.asdict() should not include tags when None."""
    block = ModuleHealthBlock(
        name="dataviz:application",
        description="Core application module",
        status=HealthStatus.UP,
    )
    result = block.asdict()
    assert "tags" not in result


@pytest.mark.unit
def test_module_health_block_asdict_with_tags():
    """ModuleHealthBlock.asdict() should include tags when provided."""
    block = ModuleHealthBlock(
        name="dataviz:application",
        description="Core application module",
        status=HealthStatus.UP,
        tags=["core", "infra"],
    )
    result = block.asdict()
    assert result["tags"] == ["core", "infra"]


# =============================================================================
# NEW TESTS: HealthResult
# =============================================================================

@pytest.mark.unit
def test_health_result_from_modules_all_up():
    """HealthResult.from_modules() should return UP when all modules are UP."""
    modules = [
        ModuleHealthBlock(
            name="dataviz:application",
            description="Core application module",
            status=HealthStatus.UP,
        ).asdict()
    ]
    result = HealthResult.from_modules(modules)
    assert result.status == "UP"
    assert result.comment == "All modules are healthy"
    assert result.description == "Health status of the Dataviz Service"
    assert result.version == "1.0"
    assert len(result.modules) == 1


@pytest.mark.unit
def test_health_result_from_modules_one_degraded():
    """HealthResult.from_modules() should return DEGRADED when any module is DEGRADED."""
    modules = [
        ModuleHealthBlock(
            name="dataviz:application",
            description="Core application module",
            status=HealthStatus.DEGRADED,
            rootcause="CeleryWorkflowExecutor is DEGRADED",
        ).asdict()
    ]
    result = HealthResult.from_modules(modules)
    assert result.status == "DEGRADED"
    assert "degraded" in result.comment.lower()


@pytest.mark.unit
def test_health_result_from_modules_one_down():
    """HealthResult.from_modules() should return DOWN when any module is DOWN."""
    modules = [
        ModuleHealthBlock(
            name="dataviz:application",
            description="Core application module",
            status=HealthStatus.DOWN,
            rootcause="PostgresClient is DOWN",
        ).asdict()
    ]
    result = HealthResult.from_modules(modules)
    assert result.status == "DOWN"
    assert "down" in result.comment.lower()


@pytest.mark.unit
def test_health_result_from_modules_key_order():
    """HealthResult.asdict() should return keys in alphabetical order."""
    modules = [
        ModuleHealthBlock(
            name="dataviz:application",
            description="Core application module",
            status=HealthStatus.UP,
        ).asdict()
    ]
    result = HealthResult.from_modules(modules).asdict()
    assert list(result.keys()) == ["comment", "description", "modules", "status", "time", "version"]


@pytest.mark.unit
def test_health_result_has_time_field():
    """HealthResult.from_modules() should always include a time field."""
    modules = [
        ModuleHealthBlock(
            name="dataviz:application",
            description="Core application module",
            status=HealthStatus.UP,
        ).asdict()
    ]
    result = HealthResult.from_modules(modules)
    assert result.time is not None
    # Should be a valid ISO format datetime
    datetime.fromisoformat(result.time)


@pytest.mark.unit
def test_health_result_no_dependencies_in_response():
    """HealthResult response should NOT contain a 'dependencies' key."""
    modules = [
        ModuleHealthBlock(
            name="dataviz:application",
            description="Core application module",
            status=HealthStatus.UP,
        ).asdict()
    ]
    result = HealthResult.from_modules(modules).asdict()
    assert "dependencies" not in result


# =============================================================================
# NEW TESTS: _build_health_comment
# =============================================================================

@pytest.mark.unit
def test_build_health_comment_all_up():
    modules = [{"name": "dataviz:application", "status": "UP"}]
    comment = _build_health_comment(HealthStatus.UP, modules)
    assert comment == "All modules are healthy"


@pytest.mark.unit
def test_build_health_comment_degraded():
    modules = [{"name": "dataviz:application", "status": "DEGRADED"}]
    comment = _build_health_comment(HealthStatus.DEGRADED, modules)
    assert "degraded" in comment.lower()
    assert "dataviz:application" in comment


@pytest.mark.unit
def test_build_health_comment_down():
    modules = [{"name": "dataviz:application", "status": "DOWN"}]
    comment = _build_health_comment(HealthStatus.DOWN, modules)
    assert "down" in comment.lower()
    assert "dataviz:application" in comment


# =============================================================================
# EXISTING TESTS — preserved and updated for new standard
# =============================================================================

@pytest.mark.component
class TestHealthService:
    class FakeLoggerMixin:
        pass

    class FakeLoggedAdapter(FakeLoggerMixin):
        pass

    class AnotherFakeLoggedAdapter(FakeLoggerMixin):
        pass

    @pytest.mark.parametrize("action", [None, pytest.param("read_secret", id="notnone")])
    @pytest.mark.parametrize("environment_id", [None, pytest.param(str("uuid4()"), id="notnone")])
    @pytest.mark.parametrize("deployment_id", [None, pytest.param(str("uuid4()"), id="notnone")])
    @pytest.mark.parametrize("start_date", [None, pytest.param(datetime.now(), id="notnone")])
    @pytest.mark.parametrize("end_date", [None, pytest.param(datetime.now(), id="notnone")])
    @pytest.mark.parametrize("limit", [0, pytest.param(randint(0, 2500), id="notzero")])
    @pytest.mark.parametrize("offset", [0, pytest.param(randint(0, 2500), id="notzero")])
    def test_build_logger_search_parameters(
        self,
        action: Optional[str],
        environment_id: Optional[str],
        deployment_id: Optional[str],
        start_date: Optional[datetime],
        end_date: Optional[datetime],
        limit: Optional[int],
        offset: Optional[int],
        mocker: MockerFixture,
    ):
        adapter = ElasticAdapter({})
        client = mocker.MagicMock(spec=Elasticsearch)
        adapter.clients["test"] = client

        dep_name = "MyVaultClient"
        ctx = mocker.MagicMock(spec=RepositoryContext)

        service = HealthService(Mock(), self.FakeLoggerMixin, adapter, "test", ctx)

        search = service.build_logger_search(
            dep_name,
            action=action,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
        )

        expected_search_query = adapter.basic_search_builder(
            "test", start_date, end_date, limit, offset
        )
        expected_search_query = expected_search_query.filter(
            "match_phrase", details__dependency_name=dep_name
        )
        if action:
            expected_search_query = expected_search_query.filter(
                "match_phrase", details__action=action
            )

        assert search == expected_search_query

    def test_get_subclasses(self, mocker: MockerFixture):
        adapter = ElasticAdapter({})
        client = mocker.MagicMock(spec=Elasticsearch)
        adapter.clients["test"] = client
        ctx = mocker.MagicMock(spec=RepositoryContext)
        service = HealthService(Mock(), self.FakeLoggerMixin, adapter, "test", ctx)

        assert all(adapter in service.deps for adapter in self.FakeLoggerMixin.__subclasses__())

    def test_get_all_dependencies_blocks_no_logs_return_up(self, mocker: MockerFixture):
        adapter = ElasticAdapter({})
        deps = self.FakeLoggerMixin.__subclasses__()
        client = build_mocked_multisearch_client(mocker, [[] for _ in deps])
        adapter.clients["test"] = client

        ctx = mocker.MagicMock(spec=RepositoryContext)
        service = HealthService(Mock(), self.FakeLoggerMixin, adapter, "test", ctx)
        expected_blocks = [
            DepedencyHealthBlock(None, None, None, dep.__name__, HealthStatus.UP).asdict()
            for dep in deps
        ]
        blocks = service.get_all_dependencies_blocks()
        assert expected_blocks == blocks

    @pytest.mark.parametrize(
        "expected_status", [HealthStatus.DOWN, HealthStatus.DEGRADED, HealthStatus.UP]
    )
    def test_get_all_dependencies_blocks(
        self, expected_status: HealthStatus, mocker: MockerFixture
    ):
        adapter = ElasticAdapter({})
        deps = self.FakeLoggerMixin.__subclasses__()
        hits = [build_hits_from_status(expected_status) for _ in deps]
        client = build_mocked_multisearch_client(mocker, hits)
        print([[CallableResult[h.details.result] for h in hs] for hs in hits])
        adapter.clients["test"] = client

        ctx = mocker.MagicMock(spec=RepositoryContext)
        service = HealthService(Mock(), self.FakeLoggerMixin, adapter, "test", ctx)
        expected_blocks = [
            DepedencyHealthBlock(None, None, None, dep.__name__, expected_status).asdict()
            for dep in deps
        ]
        blocks = service.get_all_dependencies_blocks()
        assert expected_blocks == blocks

    @pytest.mark.parametrize(
        ("result", "expected_status"),
        [
            ("FAILURE", HealthStatus.DOWN),
            ("CallableResult.FAILURE", HealthStatus.DOWN),
            ("SUCCESS", HealthStatus.UP),
            ("CallableResult.SUCCESS", HealthStatus.UP),
        ],
    )
    def test_get_dep_status_unique(
        self, result: str, expected_status: HealthStatus, mocker: MockerFixture
    ):
        raw_hits = [
            {
                "_index": "my-index-2022.11",
                "_type": "_doc",
                "_id": "87362a38-cd83-4aad-bd41-f86281ef3394",
                "_version": 1,
                "_score": None,
                "_source": {
                    "agent_timestamp": "2022-11-14T11:20:14.662820Z",
                    "message": "MyVaultClient: read_secret has succeeded",
                    "details": {
                        "dependency_name": "MyVaultClient",
                        "result": result,
                        "action": "read_secret",
                    },
                    "agent_hostname": "async_name",
                    "agent_offset": "+01:00",
                    "metadata_account_id": "acc_id",
                    "metadata_resource_id": "dep_id",
                    "metadata_resourceset_id": "env_id",
                    "metadata_xaas_key": "ccp",
                    "metadata_product_name": "logs",
                    "metadata_kafka_topic": "my_index",
                    "metadata_product_version": "1.0",
                    "metadata_region": "eu-fr-north",
                },
            }
        ]
        hits = [[Hit(h)] for h in raw_hits]

        adapter = ElasticAdapter({})
        adapter.clients["test"] = build_mocked_multisearch_client(mocker, hits)

        ctx = mocker.MagicMock(spec=RepositoryContext)
        service = HealthService(Mock(), self.FakeLoggerMixin, adapter, "test", ctx)
        # The argument doesn't really matter, we already test the passed argument in other tests
        status = service.get_dependencies_statuses([self.FakeLoggedAdapter])
        assert status == [expected_status]

    @pytest.mark.parametrize(
        ("expected_status", "results"),
        [
            (
                HealthStatus.UP,
                # All successes
                [
                    CallableLoggerResult(
                        None,
                        None,
                        "dep1",
                        CallableResult.SUCCESS,
                        None,
                        None,
                    ),
                    CallableLoggerResult(
                        None,
                        None,
                        "dep1",
                        CallableResult.SUCCESS,
                        None,
                        None,
                    ),
                ],
            ),
            (
                HealthStatus.DEGRADED,
                # 50% (3/6)
                [
                    CallableLoggerResult(
                        None,
                        None,
                        "dep1",
                        CallableResult.SUCCESS,
                        None,
                        None,
                    ),
                    CallableLoggerResult(
                        None,
                        None,
                        "dep1",
                        CallableResult.FAILURE,
                        None,
                        None,
                    ),
                    CallableLoggerResult(
                        None,
                        None,
                        "dep1",
                        CallableResult.FAILURE,
                        None,
                        None,
                    ),
                    CallableLoggerResult(
                        None,
                        None,
                        "dep1",
                        CallableResult.SUCCESS,
                        None,
                        None,
                    ),
                    CallableLoggerResult(
                        None,
                        None,
                        "dep1",
                        CallableResult.SUCCESS,
                        None,
                        None,
                    ),
                    CallableLoggerResult(
                        None,
                        None,
                        "dep1",
                        CallableResult.FAILURE,
                        None,
                        None,
                    ),
                ],
            ),
            (
                HealthStatus.DOWN,
                # 22% (2/9)
                [
                    CallableLoggerResult(
                        None,
                        None,
                        "dep1",
                        CallableResult.SUCCESS,
                        None,
                        None,
                    ),
                    CallableLoggerResult(
                        None,
                        None,
                        "dep1",
                        CallableResult.FAILURE,
                        None,
                        None,
                    ),
                    CallableLoggerResult(
                        None,
                        None,
                        "dep1",
                        CallableResult.FAILURE,
                        None,
                        None,
                    ),
                    CallableLoggerResult(
                        None,
                        None,
                        "dep1",
                        CallableResult.SUCCESS,
                        None,
                        None,
                    ),
                    CallableLoggerResult(
                        None,
                        None,
                        "dep1",
                        CallableResult.SUCCESS,
                        None,
                        None,
                    ),
                    CallableLoggerResult(
                        None,
                        None,
                        "dep1",
                        CallableResult.FAILURE,
                        None,
                        None,
                    ),
                    *itertools.repeat(
                        CallableLoggerResult(
                            None,
                            None,
                            "dep1",
                            CallableResult.FAILURE,
                            None,
                            None,
                        ),
                        5,
                    ),
                ],
            ),
            (
                HealthStatus.DOWN,
                # All failures
                [
                    CallableLoggerResult(
                        None,
                        None,
                        "dep1",
                        CallableResult.FAILURE,
                        None,
                        None,
                    ),
                    CallableLoggerResult(
                        None,
                        None,
                        "dep1",
                        CallableResult.FAILURE,
                        None,
                        None,
                    ),
                ],
            ),
        ],
    )
    def test_compute_status(self, expected_status, results):
        assert HealthService.compute_status(list(results), len(results)) == expected_status

    @pytest.mark.unit
    def test_check_health(self, mocker: MockerFixture):
        adapter = ElasticAdapter({})
        client = mocker.MagicMock(spec=Elasticsearch)
        adapter.clients["test"] = client
        ctx = mocker.MagicMock(spec=RepositoryContext)
        service = HealthService(Mock(), self.FakeLoggerMixin, adapter, "test", ctx)
        saved_health = Mock()
        ctx.health.get_last.return_value = Mock(content=saved_health)
        res = service.check_health()
        assert res == saved_health

    @pytest.mark.unit
    def test_check_health_with_data(self, mocker: MockerFixture):
        """When no cached result, check_health should return new standard format."""
        adapter = ElasticAdapter({})
        client = mocker.MagicMock(spec=Elasticsearch)
        adapter.clients["test"] = client
        ctx = mocker.MagicMock(spec=RepositoryContext)
        service = HealthService(Mock(), self.FakeLoggerMixin, adapter, "test", ctx)
        ctx.health.get_last.return_value = None
        res = service.check_health()
        # Verify new standard fields are present
        assert len(res.keys()) > 0
        assert "status" in res
        assert "modules" in res
        assert "description" in res
        assert "time" in res
        assert "comment" in res
        assert "version" in res
        # Verify no dependencies in response
        assert "dependencies" not in res

    @pytest.mark.unit
    def test_check_health_modules_use_new_naming(self, mocker: MockerFixture):
        """Modules returned by check_health should use dataviz:<module> naming format."""
        adapter = ElasticAdapter({})
        client = mocker.MagicMock(spec=Elasticsearch)
        adapter.clients["test"] = client
        ctx = mocker.MagicMock(spec=RepositoryContext)
        service = HealthService(Mock(), self.FakeLoggerMixin, adapter, "test", ctx)
        ctx.health.get_last.return_value = None
        res = service.check_health()
        for module in res["modules"]:
            assert module["name"].startswith("dataviz:")

    @pytest.mark.unit
    def test_update_health_cache(self, mocker: MockerFixture):
        adapter = ElasticAdapter({})
        client = mocker.MagicMock(spec=Elasticsearch)
        adapter.clients["test"] = client
        ctx = mocker.MagicMock(spec=RepositoryContext)
        mocker.patch(
            "dataviz_core.services.health.HealthService.get_all_dependencies_blocks",
            return_value={},
        )
        mocker.patch(
            "dataviz_core.services.health.HealthService.get_modules_blocks",
            return_value={},
        )
        service = HealthService(Mock(), self.FakeLoggerMixin, adapter, "test", ctx)
        service.update_health_cache()
        ctx.health.insert.assert_called_once()

    @pytest.mark.unit
    def test_get_modules_blocks_returns_new_format(self, mocker: MockerFixture):
        """get_modules_blocks() should return modules in new Platform Health Standard format."""
        adapter = ElasticAdapter({})
        deps = self.FakeLoggerMixin.__subclasses__()
        client = build_mocked_multisearch_client(mocker, [[] for _ in deps])
        adapter.clients["test"] = client
        ctx = mocker.MagicMock(spec=RepositoryContext)
        service = HealthService(Mock(), self.FakeLoggerMixin, adapter, "test", ctx)

        modules = service.get_modules_blocks()

        for module in modules:
            # name must follow dataviz:<module> format
            assert ":" in module["name"]
            assert module["name"].startswith("dataviz:")
            # description must be present
            assert "description" in module
            # status must be valid
            assert module["status"] in ["UP", "DEGRADED", "DOWN"]

    @pytest.mark.unit
    def test_get_modules_blocks_no_logs_returns_up(self, mocker: MockerFixture):
        """get_modules_blocks() should return UP when no ES logs found."""
        adapter = ElasticAdapter({})
        deps = self.FakeLoggerMixin.__subclasses__()
        client = build_mocked_multisearch_client(mocker, [[] for _ in deps])
        adapter.clients["test"] = client
        ctx = mocker.MagicMock(spec=RepositoryContext)
        service = HealthService(Mock(), self.FakeLoggerMixin, adapter, "test", ctx)

        modules = service.get_modules_blocks()

        for module in modules:
            assert module["status"] == "UP"
            assert module.get("rootcause") is None
