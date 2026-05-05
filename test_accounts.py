"""
Comprehensive tests for dataviz_core/services/accounts.py.
"""

import uuid
from contextlib import contextmanager
from typing import List, Optional
from unittest.mock import MagicMock, patch

import pytest

from dataviz_core.models.shared_enums import Status
from dataviz_core.errors.exceptions import (
    AccountNotFoundException,
    AccountNotInActiveException,
    NotOwnerError,
)
from dataviz_core.adapters.account_client import Account
from dataviz_core.config.const import WORKSPACE_LIMIT_PER_ACCOUNT
from dataviz_core.models.account_details import AccountDetails
from dataviz_core.models.workspace import Workspace
from dataviz_core.services.accounts import AccountService


# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
ADMIN_ID = "admin-uuid-aaaa-bbbb-cccc-dddddddddddd"
NON_ADMIN_ID = "not-an-admin-uuid"


# --------------------------------------------------------------------------
# Mock helpers
# --------------------------------------------------------------------------
class MockRepo:
    """In-memory account_details repository stub."""

    def __init__(self, mock_objects: List[AccountDetails] = None):
        self.mock_dicts = {
            str(item.owner_account_id): item
            for item in (mock_objects or [])
        }

    def get_by_owner_account_id(
        self, owner_account_id: str
    ) -> Optional[AccountDetails]:
        if self.mock_dicts.get(owner_account_id) is None:
            raise Exception("Failed to get Account details")
        return self.mock_dicts.get(owner_account_id)

    def update(self, owner_account_id: str, **kwargs):
        item = self.get_by_owner_account_id(owner_account_id)
        if item:
            for key, value in kwargs.items():
                setattr(item, key, value)

    def insert(self, item: AccountDetails):
        item.owner_account_id = item.owner_account_id or str(uuid.uuid4())
        self.mock_dicts[str(item.owner_account_id)] = item
        return item

    def list(self, limit=None, offset=None, filters=None):
        return list(self.mock_dicts.values())


def _make_autocommit_mock():
    """
    Return a MagicMock that works both as a callable returning a
    context manager AND as a context manager itself.

    AccountService uses it as:
        with self.autocommit() as session: ...
    or via update_resource_with(ctx_manager=self.autocommit(), ...)
    """
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=cm)
    cm.__exit__ = MagicMock(return_value=False)
    autocommit = MagicMock(return_value=cm)
    return autocommit


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
@pytest.fixture
def account_service():
    """Default AccountService with MagicMock'd dependencies."""
    account_client_mock = MagicMock()
    session_provider_mock = MagicMock()
    repository_context_mock = MagicMock()

    service = AccountService(
        account_client=account_client_mock,
        session_provider=session_provider_mock,
        repository_context=repository_context_mock,
    )
    service.logger = MagicMock()
    # Use a proper context manager mock — not just MagicMock(return_value=True)
    service.autocommit = _make_autocommit_mock()
    return service


@pytest.fixture
def patched_admin(mocker):
    """Patch ADMIN_ACCOUNTS so ADMIN_ID is recognised as admin."""
    mocker.patch(
        "dataviz_core.services.accounts.ADMIN_ACCOUNTS",
        f'"{ADMIN_ID}"',
    )


# --------------------------------------------------------------------------
# set_workspace_service
# --------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.component
def test_set_workspace_service(account_service):
    ws_service = MagicMock()
    account_service.set_workspace_service(ws_service)
    assert account_service.workspace_service is ws_service


# --------------------------------------------------------------------------
# _update_account_with_and_return
# --------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.component
def test_update_account_with_and_return_when_changes_applied(
    account_service, mocker
):
    account = MagicMock(spec=AccountDetails, id="acct-1")
    refreshed = MagicMock(spec=AccountDetails, id="acct-1")

    mocker.patch.object(
        account_service,
        "_update_account_with",
        return_value={"status": "INACTIVE"},
    )
    account_service.repositories.account_details.get_by_id.return_value = refreshed

    result = account_service._update_account_with_and_return(
        account, status=Status.INACTIVE
    )

    assert result is refreshed
    account_service.repositories.account_details.get_by_id.assert_called_once_with(
        "acct-1"
    )


@pytest.mark.unit
@pytest.mark.component
def test_update_account_with_and_return_when_no_changes(account_service, mocker):
    account = MagicMock(spec=AccountDetails, id="acct-1")
    mocker.patch.object(account_service, "_update_account_with", return_value={})

    result = account_service._update_account_with_and_return(
        account, status=Status.ACTIVE
    )

    assert result is account
    account_service.repositories.account_details.get_by_id.assert_not_called()


# --------------------------------------------------------------------------
# _update_account_with
# --------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.component
def test_update_account_with_invokes_resource_helper(account_service, mocker):
    fake_changes = {"status": "INACTIVE"}
    update_helper = mocker.patch(
        "dataviz_core.services.accounts.update_resource_with",
        return_value=fake_changes,
    )

    account = MagicMock(spec=AccountDetails)
    result = account_service._update_account_with(account, status=Status.INACTIVE)

    assert result == fake_changes
    update_helper.assert_called_once()


# --------------------------------------------------------------------------
# get_account_details_by_id
# --------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.component
def test_get_account_details_by_id_returns_client_result(account_service):
    mock_account = MagicMock(spec=Account)
    account_service._account_client.get_account_by_id.return_value = mock_account

    workspace = Workspace(owner_account_id="12345")
    result = account_service.get_account_details_by_id(workspace)

    assert result is mock_account
    account_service._account_client.get_account_by_id.assert_called_once_with(
        "12345"
    )


# --------------------------------------------------------------------------
# get_by_owner_id
# --------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.component
def test_get_by_owner_id_success(account_service):
    mock_account = MagicMock(spec=AccountDetails)
    account_service.repositories.account_details.list.return_value = [mock_account]

    result = account_service.get_by_owner_id("owner-123")
    assert result is mock_account


@pytest.mark.unit
@pytest.mark.component
def test_get_by_owner_id_raises_account_not_found_when_empty(account_service):
    account_service.repositories.account_details.list.return_value = []

    with pytest.raises(AccountNotFoundException):
        account_service.get_by_owner_id("owner-missing")


# --------------------------------------------------------------------------
# get_soft_limit
# --------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.component
def test_get_soft_limit_with_existing_account_details(account_service):
    owner_account_id = "12345"
    mock_account_details = AccountDetails(
        owner_account_id=owner_account_id, name="Test", soft_limit=100
    )
    mock_repo = MockRepo(mock_objects=[mock_account_details])

    account_service.repositories.account_details.get_by_owner_account_id.side_effect = (
        lambda oid: mock_repo.get_by_owner_account_id(oid)
    )

    soft_limit = account_service.get_soft_limit(owner_account_id)
    assert soft_limit == 100


@pytest.mark.unit
@pytest.mark.component
def test_get_soft_limit_with_non_existing_account_details(account_service):
    mock_repo = MockRepo()
    account_service.repositories.account_details.get_by_owner_account_id.side_effect = (
        lambda oid: mock_repo.get_by_owner_account_id(oid)
    )
    account_service.repositories.account_details.insert.side_effect = (
        mock_repo.insert
    )

    owner_account_id = "12345"
    mock_account = MagicMock(
        spec=AccountDetails, id=owner_account_id, name="Test Account"
    )
    account_service._account_client.get_account_by_id.return_value = mock_account

    soft_limit = account_service.get_soft_limit(owner_account_id)
    assert soft_limit == WORKSPACE_LIMIT_PER_ACCOUNT


@pytest.mark.unit
@pytest.mark.component
def test_get_soft_limit_account_details_not_found_from_client(account_service):
    mock_repo = MockRepo()
    account_service.repositories.account_details.get_by_owner_account_id.side_effect = (
        lambda oid: mock_repo.get_by_owner_account_id(oid)
    )
    account_service._account_client.get_account_by_id.return_value = None

    with pytest.raises(Exception, match="Error while getting Account details"):
        account_service.get_soft_limit("12345")


@pytest.mark.unit
@pytest.mark.component
def test_get_soft_limit_with_non_existing_account_and_client_failure(
    account_service,
):
    mock_repo = MockRepo()
    account_service.repositories.account_details.get_by_owner_account_id.side_effect = (
        lambda oid: mock_repo.get_by_owner_account_id(oid)
    )
    account_service._account_client.get_account_by_id.side_effect = Exception(
        "Client API error"
    )

    with pytest.raises(Exception, match="Client API error"):
        account_service.get_soft_limit("12345")


# --------------------------------------------------------------------------
# request_account_deactivation
# --------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.component
def test_request_account_deactivation_not_admin(account_service, patched_admin):
    with pytest.raises(NotOwnerError):
        account_service.request_account_deactivation(
            owner_account_id="owner-1", account_id=NON_ADMIN_ID
        )


@pytest.mark.unit
@pytest.mark.component
def test_request_account_deactivation_account_not_found(
    account_service, patched_admin
):
    account_service.repositories.account_details.list.return_value = []

    with pytest.raises(AccountNotFoundException):
        account_service.request_account_deactivation(
            owner_account_id="owner-1", account_id=ADMIN_ID
        )


@pytest.mark.unit
@pytest.mark.component
def test_request_account_deactivation_account_not_active(
    account_service, patched_admin
):
    mock_account = MagicMock(
        spec=AccountDetails, status=Status.INACTIVE, id="acct-1"
    )
    account_service.repositories.account_details.list.return_value = [mock_account]

    with pytest.raises(AccountNotInActiveException):
        account_service.request_account_deactivation(
            owner_account_id="owner-1", account_id=ADMIN_ID
        )


@pytest.mark.unit
@pytest.mark.component
def test_request_account_deactivation_no_active_workspaces(
    account_service, patched_admin, mocker
):
    mock_account = MagicMock(spec=AccountDetails, status=Status.ACTIVE)
    account_service.repositories.account_details.list.return_value = [mock_account]
    account_service.repositories.workspace.list.return_value = []

    updated = MagicMock(status=Status.INACTIVE)
    mocker.patch.object(
        account_service,
        "_update_account_with_and_return",
        return_value=updated,
    )

    result = account_service.request_account_deactivation(
        owner_account_id="owner-1", account_id=ADMIN_ID
    )
    assert result.status == Status.INACTIVE


@pytest.mark.unit
@pytest.mark.component
def test_request_account_deactivation_with_active_workspaces_fires_async(
    account_service, patched_admin
):
    mock_account = MagicMock(spec=AccountDetails, status=Status.ACTIVE)
    account_service.repositories.account_details.list.return_value = [mock_account]
    account_service.repositories.workspace.list.return_value = [
        MagicMock(spec=Workspace)
    ]
    account_service.workflow_executor = MagicMock()

    result = account_service.request_account_deactivation(
        owner_account_id="owner-1", account_id=ADMIN_ID
    )

    assert result is mock_account
    account_service.workflow_executor.async_exec_core_function.assert_called_once_with(
        service="account",
        function="deactivate_account",
        kwargs={"owner_account_id": "owner-1"},
    )


# --------------------------------------------------------------------------
# deactivate_account (async target)
# --------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.component
def test_deactivate_account_success(account_service, mocker):
    mock_account = MagicMock(spec=AccountDetails, id="acct-1")
    account_service.repositories.account_details.list.return_value = [mock_account]

    workspace_service_mock = MagicMock()
    workspace_service_mock.deactivate_workspaces_by_owner_account_id.return_value = [
        {"workspace_id": "ws-1", "status": "deactivated"}
    ]
    account_service.set_workspace_service(workspace_service_mock)

    updated = MagicMock(status=Status.INACTIVE)
    mocker.patch.object(
        account_service,
        "_update_account_with_and_return",
        return_value=updated,
    )

    result = account_service.deactivate_account("owner-1")

    assert result.status == Status.INACTIVE
    workspace_service_mock.deactivate_workspaces_by_owner_account_id.assert_called_once_with(
        "owner-1"
    )


@pytest.mark.unit
@pytest.mark.component
def test_deactivate_account_when_account_not_found(account_service):
    account_service.repositories.account_details.list.return_value = []

    with pytest.raises(AccountNotFoundException):
        account_service.deactivate_account("owner-missing")


# --------------------------------------------------------------------------
# request_account_reactivation
# --------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.component
def test_request_account_reactivation_not_admin(account_service, patched_admin):
    with pytest.raises(NotOwnerError):
        account_service.request_account_reactivation(
            owner_account_id="owner-1", account_id=NON_ADMIN_ID
        )


@pytest.mark.unit
@pytest.mark.component
def test_request_account_reactivation_account_not_found(
    account_service, patched_admin
):
    account_service.repositories.account_details.list.return_value = []

    with pytest.raises(AccountNotFoundException):
        account_service.request_account_reactivation(
            owner_account_id="owner-1", account_id=ADMIN_ID
        )


@pytest.mark.unit
@pytest.mark.component
def test_request_account_reactivation_account_not_inactive(
    account_service, patched_admin
):
    mock_account = MagicMock(
        spec=AccountDetails, status=Status.ACTIVE, id="acct-1"
    )
    account_service.repositories.account_details.list.return_value = [mock_account]

    with pytest.raises(AccountNotInActiveException):
        account_service.request_account_reactivation(
            owner_account_id="owner-1", account_id=ADMIN_ID
        )


@pytest.mark.unit
@pytest.mark.component
def test_request_account_reactivation_no_inactive_workspaces(
    account_service, patched_admin, mocker
):
    mock_account = MagicMock(spec=AccountDetails, status=Status.INACTIVE)
    account_service.repositories.account_details.list.return_value = [mock_account]
    account_service.repositories.workspace.list.return_value = []

    updated = MagicMock(status=Status.ACTIVE)
    mocker.patch.object(
        account_service,
        "_update_account_with_and_return",
        return_value=updated,
    )

    result = account_service.request_account_reactivation(
        owner_account_id="owner-1", account_id=ADMIN_ID
    )
    assert result.status == Status.ACTIVE


@pytest.mark.unit
@pytest.mark.component
def test_request_account_reactivation_with_inactive_workspaces_fires_async(
    account_service, patched_admin
):
    mock_account = MagicMock(spec=AccountDetails, status=Status.INACTIVE)
    account_service.repositories.account_details.list.return_value = [mock_account]
    account_service.repositories.workspace.list.return_value = [
        MagicMock(spec=Workspace)
    ]
    account_service.workflow_executor = MagicMock()

    result = account_service.request_account_reactivation(
        owner_account_id="owner-1", account_id=ADMIN_ID
    )

    assert result is mock_account
    account_service.workflow_executor.async_exec_core_function.assert_called_once_with(
        service="account",
        function="reactivate_account",
        kwargs={"owner_account_id": "owner-1"},
    )


# --------------------------------------------------------------------------
# reactivate_account (async target)
# --------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.component
def test_reactivate_account_success(account_service, mocker):
    mock_account = MagicMock(spec=AccountDetails, id="acct-1")
    account_service.repositories.account_details.list.return_value = [mock_account]

    workspace_service_mock = MagicMock()
    workspace_service_mock.reactivate_workspaces_by_owner_account_id.return_value = [
        {"workspace_id": "ws-1", "status": "reactivated"}
    ]
    account_service.set_workspace_service(workspace_service_mock)

    updated = MagicMock(status=Status.ACTIVE)
    mocker.patch.object(
        account_service,
        "_update_account_with_and_return",
        return_value=updated,
    )

    result = account_service.reactivate_account("owner-1")

    assert result.status == Status.ACTIVE
    workspace_service_mock.reactivate_workspaces_by_owner_account_id.assert_called_once_with(
        "owner-1"
    )


@pytest.mark.unit
@pytest.mark.component
def test_reactivate_account_when_account_not_found(account_service):
    account_service.repositories.account_details.list.return_value = []

    with pytest.raises(AccountNotFoundException):
        account_service.reactivate_account("owner-missing")


# --------------------------------------------------------------------------
# request_account_deletion
# --------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.component
def test_request_account_deletion_not_admin(account_service, patched_admin):
    with pytest.raises(NotOwnerError):
        account_service.request_account_deletion(
            owner_account_id="owner-1", account_id=NON_ADMIN_ID
        )


@pytest.mark.unit
@pytest.mark.component
def test_request_account_deletion_account_not_found(account_service, patched_admin):
    account_service.repositories.account_details.list.return_value = []

    with pytest.raises(AccountNotFoundException):
        account_service.request_account_deletion(
            owner_account_id="owner-1", account_id=ADMIN_ID
        )


@pytest.mark.unit
@pytest.mark.component
def test_request_account_deletion_account_not_active(
    account_service, patched_admin
):
    # Core requires ACTIVE for deletion — INACTIVE should raise
    mock_account = MagicMock(
        spec=AccountDetails, status=Status.INACTIVE, id="acct-1"
    )
    account_service.repositories.account_details.list.return_value = [mock_account]

    with pytest.raises(AccountNotInActiveException):
        account_service.request_account_deletion(
            owner_account_id="owner-1", account_id=ADMIN_ID
        )


@pytest.mark.unit
@pytest.mark.component
def test_request_account_deletion_no_active_workspaces(
    account_service, patched_admin, mocker
):
    mock_account = MagicMock(spec=AccountDetails, status=Status.ACTIVE)
    account_service.repositories.account_details.list.return_value = [mock_account]
    account_service.repositories.workspace.list.return_value = []

    updated = MagicMock(status=Status.DELETED)
    mocker.patch.object(
        account_service,
        "_update_account_with_and_return",
        return_value=updated,
    )

    result = account_service.request_account_deletion(
        owner_account_id="owner-1", account_id=ADMIN_ID
    )
    assert result.status == Status.DELETED


@pytest.mark.unit
@pytest.mark.component
def test_request_account_deletion_with_active_workspaces_fires_async(
    account_service, patched_admin
):
    mock_account = MagicMock(spec=AccountDetails, status=Status.ACTIVE)
    account_service.repositories.account_details.list.return_value = [mock_account]
    account_service.repositories.workspace.list.return_value = [
        MagicMock(spec=Workspace)
    ]
    account_service.workflow_executor = MagicMock()

    result = account_service.request_account_deletion(
        owner_account_id="owner-1", account_id=ADMIN_ID
    )

    assert result is mock_account
    account_service.workflow_executor.async_exec_core_function.assert_called_once_with(
        service="account",
        function="delete_account",
        kwargs={"owner_account_id": "owner-1"},
    )


# --------------------------------------------------------------------------
# delete_account (async target)
# --------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.component
def test_delete_account_success(account_service, mocker):
    mock_account = MagicMock(spec=AccountDetails, id="acct-1")
    account_service.repositories.account_details.list.return_value = [mock_account]

    workspace_service_mock = MagicMock()
    workspace_service_mock.delete_workspaces_by_owner_account_id.return_value = [
        {"workspace_id": "ws-1", "status": "deleted"}
    ]
    account_service.set_workspace_service(workspace_service_mock)

    updated = MagicMock(status=Status.DELETED)
    mocker.patch.object(
        account_service,
        "_update_account_with_and_return",
        return_value=updated,
    )

    result = account_service.delete_account("owner-1")

    assert result.status == Status.DELETED
    workspace_service_mock.delete_workspaces_by_owner_account_id.assert_called_once_with(
        "owner-1"
    )


@pytest.mark.unit
@pytest.mark.component
def test_delete_account_when_account_not_found(account_service):
    account_service.repositories.account_details.list.return_value = []

    with pytest.raises(AccountNotFoundException):
        account_service.delete_account("owner-missing")
