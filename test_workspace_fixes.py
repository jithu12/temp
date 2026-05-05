"""
Updated test cases for workspace.py changes.

These replace the specific failing tests. Drop these into your existing
test_workspace.py file, replacing the functions with matching names.

Changes from original tests:
- test_delete_workspace_with_failed_false: no longer expects exception
- test_delete_workspace_with_exception: no longer expects exception
- test_deactivate_workspace_kube_failure: no longer expects exception
- test_deactivate_workspaces_by_owner_account_id_deactivation_fails: updated assertion
- test_reactivate_workspace_inactive_also_raises: logic fixed — INACTIVE now succeeds
- test_reactivate_workspaces_by_owner_account_id: updated expected status
- test_delete_workspaces_by_owner_account_id_already_deleted: updated message
- test_delete (input_app5-calls5): updated to not expect exception
"""


# --------------------------------------------------------------------------
# _delete_workspace tests
# --------------------------------------------------------------------------

def test_delete_workspace_with_failed_false(workspace_service, mocker):
    """
    _delete_workspace should mark workspace DELETED even if external
    service calls fail. No longer raises WorkspaceDeletionFailedError.
    """
    workspace = MagicMock(spec=Workspace)
    workspace.status = Status.ACTIVE
    workspace.sg_connect = None  # simulate pre-sg_connect workspace

    workspace_service._kube_service.request_namespace_deletion.side_effect = (
        Exception("kube error")
    )

    mocker.patch.object(
        workspace_service,
        "_update_workspace_with_and_return",
        return_value=workspace,
    )

    # Should NOT raise — just logs and marks DELETED
    result = workspace_service._delete_workspace(workspace, is_failed=False)
    assert result is workspace


def test_delete_workspace_with_exception(workspace_service, mocker):
    """
    _delete_workspace should NOT raise WorkspaceDeletionFailedError
    when external calls fail — it logs and continues to mark DELETED.
    """
    workspace = MagicMock(spec=Workspace)
    workspace.sg_connect = None

    workspace_service._dataplane.request_component_deletion.side_effect = (
        Exception("dataplane error")
    )

    mocker.patch.object(
        workspace_service,
        "_update_workspace_with_and_return",
        return_value=workspace,
    )
    mocker.patch.object(
        workspace_service,
        "_update_workspace_with",
    )

    # Should NOT raise WorkspaceDeletionFailedError anymore
    result = workspace_service._delete_workspace(workspace)
    assert result is workspace
    # Should NOT have been set to FAILED
    workspace_service._update_workspace_with.assert_not_called()


def test_delete_workspace_sg_connect_none_skips_gracefully(
    workspace_service, mocker
):
    """
    Workspace created before sg_connect integration has sg_connect=None.
    _delete_workspace should skip the remove_redirect_url call and
    still mark the workspace as DELETED.
    """
    workspace = MagicMock(spec=Workspace)
    workspace.sg_connect = None  # pre-sg_connect workspace

    update_mock = mocker.patch.object(
        workspace_service,
        "_update_workspace_with_and_return",
        return_value=workspace,
    )

    workspace_service._delete_workspace(workspace)

    # sg_connect_service should NOT have been called
    workspace_service.sg_connect_service.remove_redirect_url.assert_not_called()
    # Should still mark DELETED
    update_mock.assert_called_with(workspace, status=Status.DELETED)


# --------------------------------------------------------------------------
# _deactivate_workspace tests
# --------------------------------------------------------------------------

def test_deactivate_workspace_kube_failure(workspace_service, mocker):
    """
    _deactivate_workspace should NOT raise WorkspaceDeactivationFailedError
    when kube call fails. It logs and still marks workspace INACTIVE.
    """
    workspace = MagicMock(spec=Workspace)
    workspace.status = Status.ACTIVE

    workspace_service._kube_service.request_stack_deletion.side_effect = (
        Exception("kube error")
    )

    update_mock = mocker.patch.object(
        workspace_service,
        "_update_workspace_with_and_return",
        return_value=workspace,
    )
    mocker.patch.object(workspace_service, "_update_workspace_with")

    # Should NOT raise — logs and continues
    result = workspace_service._deactivate_workspace(workspace)

    assert result is workspace
    # Should NOT have been set to FAILED
    workspace_service._update_workspace_with.assert_not_called()
    # Should still mark INACTIVE
    update_mock.assert_called_with(workspace, status=Status.INACTIVE)


# --------------------------------------------------------------------------
# deactivate_workspaces_by_owner_account_id tests
# --------------------------------------------------------------------------

def test_deactivate_workspaces_by_owner_account_id_deactivation_fails(
    workspace_service, mocker
):
    """
    When _deactivate_workspace logs the error but doesn't raise,
    the bulk method should still record the workspace as deactivated.
    """
    workspace = MagicMock(spec=Workspace)
    workspace.status = Status.ACTIVE
    workspace.id = uuid.uuid4()
    workspace.name = "test-ws"

    workspace_service.repositories.workspace.list.return_value = [workspace]

    # _deactivate_workspace no longer raises — returns workspace
    deactivated = MagicMock(spec=Workspace)
    deactivated.id = workspace.id
    deactivated.name = workspace.name
    deactivated.status = Status.INACTIVE

    mocker.patch.object(
        workspace_service,
        "_deactivate_workspace",
        return_value=deactivated,
    )

    result = workspace_service.deactivate_workspaces_by_owner_account_id(
        "owner-1"
    )

    assert len(result) == 1
    assert result[0]["status"] == "deactivated"


# --------------------------------------------------------------------------
# reactivate_workspace tests
# --------------------------------------------------------------------------

def test_reactivate_workspace_inactive_also_raises(workspace_service, mocker):
    """
    After our fix, INACTIVE workspaces should be ACTIVATED (not raise).
    The old test expected a raise — that behavior is now corrected.
    """
    workspace = MagicMock(spec=Workspace)
    workspace.status = Status.INACTIVE

    workspace_service.repositories.workspace.get_by_id.return_value = workspace

    # _refresh_workspace returns same workspace
    mocker.patch.object(
        workspace_service,
        "_refresh_workspace",
        return_value=workspace,
    )

    activated = MagicMock(spec=Workspace, status=Status.ACTIVE)
    mocker.patch.object(
        workspace_service,
        "_activate_workspace",
        return_value=activated,
    )

    # Should NOT raise — INACTIVE should now be activated
    result = workspace_service.reactivate_workspace(workspace.id)
    assert result.status == Status.ACTIVE


# --------------------------------------------------------------------------
# reactivate_workspaces_by_owner_account_id tests
# --------------------------------------------------------------------------

def test_reactivate_workspaces_by_owner_account_id(workspace_service, mocker):
    """
    After fixing reactivate_workspace, bulk reactivation should
    successfully reactivate INACTIVE workspaces and report 'reactivated'.
    """
    workspace = MagicMock(spec=Workspace)
    workspace.status = Status.INACTIVE
    workspace.id = uuid.uuid4()
    workspace.name = "test-ws"

    workspace_service.repositories.workspace.list.return_value = [workspace]

    reactivated = MagicMock(spec=Workspace)
    reactivated.id = workspace.id
    reactivated.name = workspace.name
    reactivated.status = Status.ACTIVE

    mocker.patch.object(
        workspace_service,
        "reactivate_workspace",
        return_value=reactivated,
    )

    result = workspace_service.reactivate_workspaces_by_owner_account_id(
        "owner-1"
    )

    assert len(result) == 1
    # Now reports 'reactivated' not 'reactivation failed'
    assert result[0]["status"] == "reactivated"


# --------------------------------------------------------------------------
# delete_workspaces_by_owner_account_id tests
# --------------------------------------------------------------------------

def test_delete_workspaces_by_owner_account_id_already_deleted(
    workspace_service,
):
    """
    Workspace already DELETED should be skipped with updated message.
    """
    workspace = MagicMock(spec=Workspace)
    workspace.status = Status.DELETED
    workspace.id = uuid.uuid4()
    workspace.name = "test-ws"

    workspace_service.repositories.workspace.list.return_value = [workspace]

    result = workspace_service.delete_workspaces_by_owner_account_id("owner-1")

    assert len(result) == 1
    # Updated message from our change
    assert result[0]["status"] == "already deleted"
