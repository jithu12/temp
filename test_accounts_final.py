"""
Comprehensive tests for dataviz_core/services/accounts.py

Written from a TESTER perspective — covers every scenario a Lead
would try including edge cases, invalid inputs, concurrent operations,
failure recovery, and full lifecycle flows.

Test categories:
1. Admin gate tests
2. Account not found tests
3. Invalid state transition tests
4. Happy path — no workspaces (direct status change)
5. Happy path — with workspaces (UPDATE_REQUESTED + async)
6. Concurrent operation protection (UPDATE_REQUESTED blocks new requests)
7. FAILED account recovery (can retry from FAILED)
8. Async target success tests
9. Async target failure tests (sets FAILED)
10. Full lifecycle integration tests
11. Edge cases (FAILED → deactivate, FAILED → delete, etc.)
"""

import uuid
from typing import List, Optional
from unittest.mock import MagicMock

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
# Helpers
# --------------------------------------------------------------------------
class MockRepo:
    def __init__(self, mock_objects: List[AccountDetails] = None):
        self.mock_dicts = {
            str(item.owner_account_id): item
            for item in (mock_objects or [])
        }

    def get_by_owner_account_id(self, owner_account_id):
        if self.mock_dicts.get(owner_account_id) is None:
            raise Exception("Failed to get Account details")
        return self.mock_dicts.get(owner_account_id)

    def insert(self, item):
        item.owner_account_id = item.owner_account_id or str(uuid.uuid4())
        self.mock_dicts[str(item.owner_account_id)] = item
        return item

    def list(self, **kwargs):
        return list(self.mock_dicts.values())


def _make_autocommit_mock():
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=cm)
    cm.__exit__ = MagicMock(return_value=False)
    return MagicMock(return_value=cm)


def _make_account(status=Status.ACTIVE, account_id=None):
    """Helper to create a mock AccountDetails with given status."""
    return MagicMock(
        spec=AccountDetails,
        id=account_id or str(uuid.uuid4()),
        status=status,
        owner_account_id=str(uuid.uuid4()),
        name="test-account",
    )


def _make_workspace(status=Status.ACTIVE):
    return MagicMock(spec=Workspace, status=status)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
@pytest.fixture
def svc():
    """AccountService with mocked dependencies."""
    service = AccountService(
        account_client=MagicMock(),
        session_provider=MagicMock(),
        repository_context=MagicMock(),
    )
    service.logger = MagicMock()
    service.autocommit = _make_autocommit_mock()
    return service


@pytest.fixture
def admin(mocker):
    """Patch ADMIN_ACCOUNTS to recognise ADMIN_ID."""
    mocker.patch(
        "dataviz_core.services.accounts.ADMIN_ACCOUNTS",
        f'"{ADMIN_ID}"',
    )


# ==========================================================================
# 1. ADMIN GATE TESTS
# ==========================================================================

@pytest.mark.unit
def test_deactivate_rejects_non_admin(svc, admin):
    with pytest.raises(NotOwnerError):
        svc.request_account_deactivation("owner", NON_ADMIN_ID)


@pytest.mark.unit
def test_activate_rejects_non_admin(svc, admin):
    with pytest.raises(NotOwnerError):
        svc.request_account_reactivation("owner", NON_ADMIN_ID)


@pytest.mark.unit
def test_delete_rejects_non_admin(svc, admin):
    with pytest.raises(NotOwnerError):
        svc.request_account_deletion("owner", NON_ADMIN_ID)


# ==========================================================================
# 2. ACCOUNT NOT FOUND TESTS
# ==========================================================================

@pytest.mark.unit
def test_deactivate_raises_when_account_not_found(svc, admin):
    svc.repositories.account_details.list.return_value = []
    with pytest.raises(AccountNotFoundException):
        svc.request_account_deactivation("missing", ADMIN_ID)


@pytest.mark.unit
def test_activate_raises_when_account_not_found(svc, admin):
    svc.repositories.account_details.list.return_value = []
    with pytest.raises(AccountNotFoundException):
        svc.request_account_reactivation("missing", ADMIN_ID)


@pytest.mark.unit
def test_delete_raises_when_account_not_found(svc, admin):
    svc.repositories.account_details.list.return_value = []
    with pytest.raises(AccountNotFoundException):
        svc.request_account_deletion("missing", ADMIN_ID)


@pytest.mark.unit
def test_deactivate_async_raises_when_account_not_found(svc):
    svc.repositories.account_details.list.return_value = []
    with pytest.raises(AccountNotFoundException):
        svc.deactivate_account("missing")


@pytest.mark.unit
def test_activate_async_raises_when_account_not_found(svc):
    svc.repositories.account_details.list.return_value = []
    with pytest.raises(AccountNotFoundException):
        svc.reactivate_account("missing")


@pytest.mark.unit
def test_delete_async_raises_when_account_not_found(svc):
    svc.repositories.account_details.list.return_value = []
    with pytest.raises(AccountNotFoundException):
        svc.delete_account("missing")


# ==========================================================================
# 3. INVALID STATE TRANSITION TESTS
# What a tester would try: wrong operations on wrong states
# ==========================================================================

@pytest.mark.unit
@pytest.mark.parametrize("status", [
    Status.INACTIVE,
    Status.DELETED,
    Status.UPDATE_REQUESTED,
    Status.UPDATING,
])
def test_deactivate_rejects_invalid_states(svc, admin, status):
    """Cannot deactivate if already INACTIVE, DELETED, or in progress."""
    svc.repositories.account_details.list.return_value = [
        _make_account(status=status)
    ]
    with pytest.raises(AccountNotInActiveException):
        svc.request_account_deactivation("owner", ADMIN_ID)


@pytest.mark.unit
@pytest.mark.parametrize("status", [
    Status.ACTIVE,
    Status.DELETED,
    Status.UPDATE_REQUESTED,
    Status.UPDATING,
])
def test_activate_rejects_invalid_states(svc, admin, status):
    """Cannot activate if already ACTIVE, DELETED, or in progress."""
    svc.repositories.account_details.list.return_value = [
        _make_account(status=status)
    ]
    with pytest.raises(AccountNotInActiveException):
        svc.request_account_reactivation("owner", ADMIN_ID)


@pytest.mark.unit
@pytest.mark.parametrize("status", [
    Status.DELETED,
    Status.UPDATE_REQUESTED,
    Status.UPDATING,
])
def test_delete_rejects_invalid_states(svc, admin, status):
    """Cannot delete if already DELETED or in progress."""
    svc.repositories.account_details.list.return_value = [
        _make_account(status=status)
    ]
    with pytest.raises(AccountNotInActiveException):
        svc.request_account_deletion("owner", ADMIN_ID)


# ==========================================================================
# 4. HAPPY PATH — NO WORKSPACES (direct status change, no async)
# ==========================================================================

@pytest.mark.unit
def test_deactivate_goes_straight_to_inactive_when_no_workspaces(
    svc, admin, mocker
):
    svc.repositories.account_details.list.return_value = [
        _make_account(status=Status.ACTIVE)
    ]
    svc.repositories.workspace.list.return_value = []
    updated = _make_account(status=Status.INACTIVE)
    mocker.patch.object(svc, "_update_account_with_and_return", return_value=updated)

    result = svc.request_account_deactivation("owner", ADMIN_ID)
    assert result.status == Status.INACTIVE


@pytest.mark.unit
def test_activate_goes_straight_to_active_when_no_workspaces(
    svc, admin, mocker
):
    svc.repositories.account_details.list.return_value = [
        _make_account(status=Status.INACTIVE)
    ]
    svc.repositories.workspace.list.return_value = []
    updated = _make_account(status=Status.ACTIVE)
    mocker.patch.object(svc, "_update_account_with_and_return", return_value=updated)

    result = svc.request_account_reactivation("owner", ADMIN_ID)
    assert result.status == Status.ACTIVE


@pytest.mark.unit
def test_delete_goes_straight_to_deleted_when_no_workspaces(
    svc, admin, mocker
):
    svc.repositories.account_details.list.return_value = [
        _make_account(status=Status.ACTIVE)
    ]
    svc.repositories.workspace.list.return_value = []
    updated = _make_account(status=Status.DELETED)
    mocker.patch.object(svc, "_update_account_with_and_return", return_value=updated)

    result = svc.request_account_deletion("owner", ADMIN_ID)
    assert result.status == Status.DELETED


@pytest.mark.unit
def test_delete_inactive_account_with_no_workspaces(svc, admin, mocker):
    """Lead will try: deactivate first, then delete from INACTIVE state."""
    svc.repositories.account_details.list.return_value = [
        _make_account(status=Status.INACTIVE)
    ]
    svc.repositories.workspace.list.return_value = []
    updated = _make_account(status=Status.DELETED)
    mocker.patch.object(svc, "_update_account_with_and_return", return_value=updated)

    result = svc.request_account_deletion("owner", ADMIN_ID)
    assert result.status == Status.DELETED


@pytest.mark.unit
def test_delete_skips_already_deleted_workspaces(svc, admin, mocker):
    """
    If some workspaces are already DELETED (e.g. from previous partial attempt),
    they should be skipped. Account still becomes DELETED.
    """
    svc.repositories.account_details.list.return_value = [
        _make_account(status=Status.ACTIVE)
    ]
    # All workspaces already deleted
    svc.repositories.workspace.list.return_value = [
        _make_workspace(status=Status.DELETED),
        _make_workspace(status=Status.DELETED),
    ]
    updated = _make_account(status=Status.DELETED)
    mocker.patch.object(svc, "_update_account_with_and_return", return_value=updated)

    result = svc.request_account_deletion("owner", ADMIN_ID)
    assert result.status == Status.DELETED


# ==========================================================================
# 5. HAPPY PATH — WITH WORKSPACES (UPDATE_REQUESTED + async)
# ==========================================================================

@pytest.mark.unit
def test_deactivate_sets_update_requested_and_fires_async(svc, admin, mocker):
    svc.repositories.account_details.list.return_value = [
        _make_account(status=Status.ACTIVE)
    ]
    svc.repositories.workspace.list.return_value = [_make_workspace()]
    svc.workflow_executor = MagicMock()
    update_req = _make_account(status=Status.UPDATE_REQUESTED)
    mocker.patch.object(svc, "_update_account_with_and_return", return_value=update_req)

    result = svc.request_account_deactivation("owner-1", ADMIN_ID)

    assert result.status == Status.UPDATE_REQUESTED
    svc.workflow_executor.async_exec_core_function.assert_called_once_with(
        service="account",
        function="deactivate_account",
        kwargs={"owner_account_id": "owner-1"},
    )


@pytest.mark.unit
def test_activate_sets_update_requested_and_fires_async(svc, admin, mocker):
    svc.repositories.account_details.list.return_value = [
        _make_account(status=Status.INACTIVE)
    ]
    svc.repositories.workspace.list.return_value = [
        _make_workspace(status=Status.INACTIVE)
    ]
    svc.workflow_executor = MagicMock()
    update_req = _make_account(status=Status.UPDATE_REQUESTED)
    mocker.patch.object(svc, "_update_account_with_and_return", return_value=update_req)

    result = svc.request_account_reactivation("owner-1", ADMIN_ID)

    assert result.status == Status.UPDATE_REQUESTED
    svc.workflow_executor.async_exec_core_function.assert_called_once_with(
        service="account",
        function="reactivate_account",
        kwargs={"owner_account_id": "owner-1"},
    )


@pytest.mark.unit
def test_delete_fires_async_and_sets_deleted_when_workspaces_exist(
    svc, admin, mocker
):
    svc.repositories.account_details.list.return_value = [
        _make_account(status=Status.ACTIVE)
    ]
    svc.repositories.workspace.list.return_value = [_make_workspace()]
    svc.workflow_executor = MagicMock()
    deleted = _make_account(status=Status.DELETED)
    mocker.patch.object(svc, "_update_account_with_and_return", return_value=deleted)

    result = svc.request_account_deletion("owner-1", ADMIN_ID)

    assert result.status == Status.DELETED
    svc.workflow_executor.async_exec_core_function.assert_called_once_with(
        service="account",
        function="delete_account",
        kwargs={"owner_account_id": "owner-1"},
    )


# ==========================================================================
# 6. CONCURRENT OPERATION PROTECTION
# Lead will try: click deactivate twice quickly
# ==========================================================================

@pytest.mark.unit
def test_cannot_deactivate_while_update_already_in_progress(svc, admin):
    """Clicking deactivate twice — second call should be blocked."""
    svc.repositories.account_details.list.return_value = [
        _make_account(status=Status.UPDATE_REQUESTED)
    ]
    with pytest.raises(AccountNotInActiveException):
        svc.request_account_deactivation("owner", ADMIN_ID)


@pytest.mark.unit
def test_cannot_activate_while_update_already_in_progress(svc, admin):
    """Clicking activate twice — second call should be blocked."""
    svc.repositories.account_details.list.return_value = [
        _make_account(status=Status.UPDATE_REQUESTED)
    ]
    with pytest.raises(AccountNotInActiveException):
        svc.request_account_reactivation("owner", ADMIN_ID)


@pytest.mark.unit
def test_cannot_delete_while_update_already_in_progress(svc, admin):
    """Cannot delete while deactivation/activation is running."""
    svc.repositories.account_details.list.return_value = [
        _make_account(status=Status.UPDATE_REQUESTED)
    ]
    with pytest.raises(AccountNotInActiveException):
        svc.request_account_deletion("owner", ADMIN_ID)


@pytest.mark.unit
def test_cannot_deactivate_while_updating(svc, admin):
    """UPDATING status also blocks new requests."""
    svc.repositories.account_details.list.return_value = [
        _make_account(status=Status.UPDATING)
    ]
    with pytest.raises(AccountNotInActiveException):
        svc.request_account_deactivation("owner", ADMIN_ID)


# ==========================================================================
# 7. FAILED ACCOUNT RECOVERY
# Lead will ask: "What happens if deactivation fails? Can we retry?"
# ==========================================================================

@pytest.mark.unit
def test_can_retry_deactivation_from_failed_state(svc, admin, mocker):
    """
    If deactivation failed before, admin should be able to retry.
    FAILED → deactivate → UPDATE_REQUESTED → INACTIVE
    """
    svc.repositories.account_details.list.return_value = [
        _make_account(status=Status.FAILED)
    ]
    svc.repositories.workspace.list.return_value = [_make_workspace()]
    svc.workflow_executor = MagicMock()
    update_req = _make_account(status=Status.UPDATE_REQUESTED)
    mocker.patch.object(svc, "_update_account_with_and_return", return_value=update_req)

    result = svc.request_account_deactivation("owner", ADMIN_ID)
    assert result.status == Status.UPDATE_REQUESTED


@pytest.mark.unit
def test_can_retry_activation_from_failed_state(svc, admin, mocker):
    """
    If reactivation failed before, admin should be able to retry.
    FAILED → activate → UPDATE_REQUESTED → ACTIVE
    """
    svc.repositories.account_details.list.return_value = [
        _make_account(status=Status.FAILED)
    ]
    svc.repositories.workspace.list.return_value = [
        _make_workspace(status=Status.INACTIVE)
    ]
    svc.workflow_executor = MagicMock()
    update_req = _make_account(status=Status.UPDATE_REQUESTED)
    mocker.patch.object(svc, "_update_account_with_and_return", return_value=update_req)

    result = svc.request_account_reactivation("owner", ADMIN_ID)
    assert result.status == Status.UPDATE_REQUESTED


@pytest.mark.unit
def test_can_delete_failed_account(svc, admin, mocker):
    """
    If account is FAILED, admin should be able to delete it.
    FAILED → delete → DELETED
    """
    svc.repositories.account_details.list.return_value = [
        _make_account(status=Status.FAILED)
    ]
    svc.repositories.workspace.list.return_value = []
    updated = _make_account(status=Status.DELETED)
    mocker.patch.object(svc, "_update_account_with_and_return", return_value=updated)

    result = svc.request_account_deletion("owner", ADMIN_ID)
    assert result.status == Status.DELETED


# ==========================================================================
# 8. ASYNC TARGET SUCCESS TESTS
# ==========================================================================

@pytest.mark.unit
def test_deactivate_account_async_sets_inactive_on_success(svc, mocker):
    svc.repositories.account_details.list.return_value = [_make_account()]
    ws = MagicMock()
    ws.deactivate_workspaces_by_owner_account_id.return_value = []
    svc.set_workspace_service(ws)
    updated = _make_account(status=Status.INACTIVE)
    mocker.patch.object(svc, "_update_account_with_and_return", return_value=updated)

    result = svc.deactivate_account("owner")
    assert result.status == Status.INACTIVE
    ws.deactivate_workspaces_by_owner_account_id.assert_called_once_with("owner")


@pytest.mark.unit
def test_reactivate_account_async_sets_active_on_success(svc, mocker):
    svc.repositories.account_details.list.return_value = [_make_account()]
    ws = MagicMock()
    ws.reactivate_workspaces_by_owner_account_id.return_value = []
    svc.set_workspace_service(ws)
    updated = _make_account(status=Status.ACTIVE)
    mocker.patch.object(svc, "_update_account_with_and_return", return_value=updated)

    result = svc.reactivate_account("owner")
    assert result.status == Status.ACTIVE
    ws.reactivate_workspaces_by_owner_account_id.assert_called_once_with("owner")


@pytest.mark.unit
def test_delete_account_async_sets_deleted_on_success(svc, mocker):
    svc.repositories.account_details.list.return_value = [_make_account()]
    ws = MagicMock()
    ws.delete_workspaces_by_owner_account_id.return_value = []
    svc.set_workspace_service(ws)
    updated = _make_account(status=Status.DELETED)
    mocker.patch.object(svc, "_update_account_with_and_return", return_value=updated)

    result = svc.delete_account("owner")
    assert result.status == Status.DELETED
    ws.delete_workspaces_by_owner_account_id.assert_called_once_with("owner")


# ==========================================================================
# 9. ASYNC TARGET FAILURE TESTS
# Must set FAILED — must NOT stay stuck in UPDATE_REQUESTED
# ==========================================================================

@pytest.mark.unit
def test_deactivate_account_async_sets_failed_on_workspace_error(svc):
    mock_account = _make_account()
    svc.repositories.account_details.list.return_value = [mock_account]
    ws = MagicMock()
    ws.deactivate_workspaces_by_owner_account_id.side_effect = Exception("kube down")
    svc.set_workspace_service(ws)
    svc._update_account_with = MagicMock()

    with pytest.raises(Exception, match="kube down"):
        svc.deactivate_account("owner")

    svc._update_account_with.assert_called_once_with(
        mock_account, status=Status.FAILED
    )


@pytest.mark.unit
def test_reactivate_account_async_sets_failed_on_workspace_error(svc):
    mock_account = _make_account()
    svc.repositories.account_details.list.return_value = [mock_account]
    ws = MagicMock()
    ws.reactivate_workspaces_by_owner_account_id.side_effect = Exception("kube down")
    svc.set_workspace_service(ws)
    svc._update_account_with = MagicMock()

    with pytest.raises(Exception, match="kube down"):
        svc.reactivate_account("owner")

    svc._update_account_with.assert_called_once_with(
        mock_account, status=Status.FAILED
    )


@pytest.mark.unit
def test_delete_account_async_sets_failed_on_workspace_error(svc):
    mock_account = _make_account()
    svc.repositories.account_details.list.return_value = [mock_account]
    ws = MagicMock()
    ws.delete_workspaces_by_owner_account_id.side_effect = Exception("deletion error")
    svc.set_workspace_service(ws)
    svc._update_account_with = MagicMock()

    with pytest.raises(Exception, match="deletion error"):
        svc.delete_account("owner")

    svc._update_account_with.assert_called_once_with(
        mock_account, status=Status.FAILED
    )


# ==========================================================================
# 10. FULL LIFECYCLE INTEGRATION TESTS
# These simulate exactly what the Lead will do tomorrow
# ==========================================================================

@pytest.mark.unit
def test_full_lifecycle_active_deactivate_activate(svc, admin, mocker):
    """
    Lead's main test: ACTIVE → deactivate → INACTIVE → activate → ACTIVE
    """
    # Step 1: deactivate
    svc.repositories.account_details.list.return_value = [
        _make_account(status=Status.ACTIVE)
    ]
    svc.repositories.workspace.list.return_value = [_make_workspace()]
    svc.workflow_executor = MagicMock()
    mocker.patch.object(
        svc, "_update_account_with_and_return",
        return_value=_make_account(status=Status.UPDATE_REQUESTED)
    )
    result = svc.request_account_deactivation("owner", ADMIN_ID)
    assert result.status == Status.UPDATE_REQUESTED

    # Step 2: async completes → INACTIVE
    mocker.patch.object(
        svc, "_update_account_with_and_return",
        return_value=_make_account(status=Status.INACTIVE)
    )
    ws = MagicMock()
    ws.deactivate_workspaces_by_owner_account_id.return_value = []
    svc.set_workspace_service(ws)
    final = svc.deactivate_account("owner")
    assert final.status == Status.INACTIVE

    # Step 3: activate
    svc.repositories.account_details.list.return_value = [
        _make_account(status=Status.INACTIVE)
    ]
    svc.repositories.workspace.list.return_value = [
        _make_workspace(status=Status.INACTIVE)
    ]
    mocker.patch.object(
        svc, "_update_account_with_and_return",
        return_value=_make_account(status=Status.UPDATE_REQUESTED)
    )
    result = svc.request_account_reactivation("owner", ADMIN_ID)
    assert result.status == Status.UPDATE_REQUESTED

    # Step 4: async completes → ACTIVE
    mocker.patch.object(
        svc, "_update_account_with_and_return",
        return_value=_make_account(status=Status.ACTIVE)
    )
    ws2 = MagicMock()
    ws2.reactivate_workspaces_by_owner_account_id.return_value = []
    svc.set_workspace_service(ws2)
    final = svc.reactivate_account("owner")
    assert final.status == Status.ACTIVE


@pytest.mark.unit
def test_full_lifecycle_deactivate_then_delete(svc, admin, mocker):
    """
    Lead's delete test: ACTIVE → deactivate → INACTIVE → delete → DELETED
    """
    # Step 1: deactivate
    svc.repositories.account_details.list.return_value = [
        _make_account(status=Status.ACTIVE)
    ]
    svc.repositories.workspace.list.return_value = [_make_workspace()]
    svc.workflow_executor = MagicMock()
    mocker.patch.object(
        svc, "_update_account_with_and_return",
        return_value=_make_account(status=Status.UPDATE_REQUESTED)
    )
    result = svc.request_account_deactivation("owner", ADMIN_ID)
    assert result.status == Status.UPDATE_REQUESTED

    # Step 2: async completes → INACTIVE
    mocker.patch.object(
        svc, "_update_account_with_and_return",
        return_value=_make_account(status=Status.INACTIVE)
    )
    ws = MagicMock()
    ws.deactivate_workspaces_by_owner_account_id.return_value = []
    svc.set_workspace_service(ws)
    svc.deactivate_account("owner")

    # Step 3: delete from INACTIVE
    svc.repositories.account_details.list.return_value = [
        _make_account(status=Status.INACTIVE)
    ]
    svc.repositories.workspace.list.return_value = [
        _make_workspace(status=Status.INACTIVE)
    ]
    mocker.patch.object(
        svc, "_update_account_with_and_return",
        return_value=_make_account(status=Status.DELETED)
    )
    result = svc.request_account_deletion("owner", ADMIN_ID)
    assert result.status == Status.DELETED


@pytest.mark.unit
def test_full_lifecycle_direct_delete_from_active(svc, admin, mocker):
    """
    ACTIVE → delete directly (no deactivate step)
    """
    svc.repositories.account_details.list.return_value = [
        _make_account(status=Status.ACTIVE)
    ]
    svc.repositories.workspace.list.return_value = [_make_workspace()]
    svc.workflow_executor = MagicMock()
    mocker.patch.object(
        svc, "_update_account_with_and_return",
        return_value=_make_account(status=Status.DELETED)
    )
    result = svc.request_account_deletion("owner", ADMIN_ID)
    assert result.status == Status.DELETED


# ==========================================================================
# 11. EDGE CASES
# Things a thorough tester would definitely try
# ==========================================================================

@pytest.mark.unit
def test_cannot_deactivate_already_deleted_account(svc, admin):
    """Lead might try to deactivate a deleted account."""
    svc.repositories.account_details.list.return_value = [
        _make_account(status=Status.DELETED)
    ]
    with pytest.raises(AccountNotInActiveException):
        svc.request_account_deactivation("owner", ADMIN_ID)


@pytest.mark.unit
def test_cannot_activate_already_deleted_account(svc, admin):
    """Lead might try to activate a deleted account."""
    svc.repositories.account_details.list.return_value = [
        _make_account(status=Status.DELETED)
    ]
    with pytest.raises(AccountNotInActiveException):
        svc.request_account_reactivation("owner", ADMIN_ID)


@pytest.mark.unit
def test_cannot_delete_already_deleted_account(svc, admin):
    """Lead might try to delete a deleted account twice."""
    svc.repositories.account_details.list.return_value = [
        _make_account(status=Status.DELETED)
    ]
    with pytest.raises(AccountNotInActiveException):
        svc.request_account_deletion("owner", ADMIN_ID)


@pytest.mark.unit
def test_cannot_deactivate_already_inactive_account(svc, admin):
    """Lead might try to deactivate an already inactive account."""
    svc.repositories.account_details.list.return_value = [
        _make_account(status=Status.INACTIVE)
    ]
    with pytest.raises(AccountNotInActiveException):
        svc.request_account_deactivation("owner", ADMIN_ID)


@pytest.mark.unit
def test_cannot_activate_already_active_account(svc, admin):
    """Lead might try to activate an already active account."""
    svc.repositories.account_details.list.return_value = [
        _make_account(status=Status.ACTIVE)
    ]
    with pytest.raises(AccountNotInActiveException):
        svc.request_account_reactivation("owner", ADMIN_ID)


@pytest.mark.unit
def test_deactivation_with_multiple_workspaces_fires_single_async_job(
    svc, admin, mocker
):
    """
    Even with 10 workspaces, only ONE async job should be fired.
    The async job handles all of them internally.
    """
    svc.repositories.account_details.list.return_value = [
        _make_account(status=Status.ACTIVE)
    ]
    svc.repositories.workspace.list.return_value = [
        _make_workspace() for _ in range(10)
    ]
    svc.workflow_executor = MagicMock()
    mocker.patch.object(
        svc, "_update_account_with_and_return",
        return_value=_make_account(status=Status.UPDATE_REQUESTED)
    )

    svc.request_account_deactivation("owner", ADMIN_ID)

    # Exactly ONE async call — not one per workspace
    assert svc.workflow_executor.async_exec_core_function.call_count == 1


@pytest.mark.unit
def test_delete_with_mix_of_active_and_inactive_workspaces(
    svc, admin, mocker
):
    """
    Account has both ACTIVE and INACTIVE workspaces (partial deactivation).
    Delete should handle all non-DELETED workspaces.
    """
    svc.repositories.account_details.list.return_value = [
        _make_account(status=Status.ACTIVE)
    ]
    svc.repositories.workspace.list.return_value = [
        _make_workspace(status=Status.ACTIVE),
        _make_workspace(status=Status.INACTIVE),
        _make_workspace(status=Status.FAILED),
        _make_workspace(status=Status.DELETED),  # this one should be skipped
    ]
    svc.workflow_executor = MagicMock()
    mocker.patch.object(
        svc, "_update_account_with_and_return",
        return_value=_make_account(status=Status.DELETED)
    )

    result = svc.request_account_deletion("owner", ADMIN_ID)

    # Async job fired because there are 3 non-deleted workspaces
    svc.workflow_executor.async_exec_core_function.assert_called_once()
    assert result.status == Status.DELETED


@pytest.mark.unit
def test_update_account_with_and_return_refetches_on_changes(svc, mocker):
    """
    When changes are applied, should refetch from DB to get latest state.
    """
    account = _make_account()
    refreshed = _make_account(status=Status.INACTIVE)
    mocker.patch.object(svc, "_update_account_with", return_value={"status": "INACTIVE"})
    svc.repositories.account_details.get_by_id.return_value = refreshed

    result = svc._update_account_with_and_return(account, status=Status.INACTIVE)
    assert result is refreshed
    svc.repositories.account_details.get_by_id.assert_called_once_with(account.id)


@pytest.mark.unit
def test_update_account_with_and_return_returns_original_when_no_changes(
    svc, mocker
):
    """
    When no changes detected, should return the original account without DB hit.
    """
    account = _make_account()
    mocker.patch.object(svc, "_update_account_with", return_value={})

    result = svc._update_account_with_and_return(account, status=Status.ACTIVE)
    assert result is account
    svc.repositories.account_details.get_by_id.assert_not_called()


# ==========================================================================
# 12. get_soft_limit tests
# ==========================================================================

@pytest.mark.unit
def test_get_soft_limit_returns_stored_limit(svc):
    repo = MockRepo([AccountDetails(owner_account_id="1", name="T", soft_limit=50)])
    svc.repositories.account_details.get_by_owner_account_id.side_effect = (
        lambda o: repo.get_by_owner_account_id(o)
    )
    assert svc.get_soft_limit("1") == 50


@pytest.mark.unit
def test_get_soft_limit_creates_record_when_not_found(svc):
    repo = MockRepo()
    svc.repositories.account_details.get_by_owner_account_id.side_effect = (
        lambda o: repo.get_by_owner_account_id(o)
    )
    svc.repositories.account_details.insert.side_effect = repo.insert
    svc.account_client.get_account_by_id.return_value = MagicMock(id="1", name="T")
    assert svc.get_soft_limit("1") == WORKSPACE_LIMIT_PER_ACCOUNT


@pytest.mark.unit
def test_get_soft_limit_raises_when_client_returns_none(svc):
    repo = MockRepo()
    svc.repositories.account_details.get_by_owner_account_id.side_effect = (
        lambda o: repo.get_by_owner_account_id(o)
    )
    svc.account_client.get_account_by_id.return_value = None
    with pytest.raises(Exception, match="Error while getting Account details"):
        svc.get_soft_limit("1")


@pytest.mark.unit
def test_get_soft_limit_propagates_client_exception(svc):
    repo = MockRepo()
    svc.repositories.account_details.get_by_owner_account_id.side_effect = (
        lambda o: repo.get_by_owner_account_id(o)
    )
    svc.account_client.get_account_by_id.side_effect = Exception("network error")
    with pytest.raises(Exception, match="network error"):
        svc.get_soft_limit("1")
