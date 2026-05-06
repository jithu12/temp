"""
Two fixes to apply in tests/services/test_workspace.py
"""

# ============================================================
# FIX 1: Replace index 4 in the @pytest.mark.parametrize list
# for test_delete.
#
# The actual flow for FAILED workspace goes:
# FAILED -> _delete_workspace -> sets DELETING first -> then DELETED
# So the expected calls must include the DELETING intermediate step.
# ============================================================

# Replace this entire parametrize block for test_delete
# (only index 4 changes, rest stays the same):

@pytest.mark.parametrize(
    ["input_app", "calls"],
    [
        # index 0 — CREATION_REQUESTED -> DELETING -> DELETED
        [
            [
                workspace_model(
                    id="workspace_id",
                    status=Status.CREATION_REQUESTED,
                    status_history=[Status.CREATION_REQUESTED],
                ),
                workspace_model(
                    id="workspace_id",
                    status=Status.DELETING,
                    status_history=[Status.CREATION_REQUESTED, Status.DELETING],
                    deletion_date=RUN_NOW,
                ),
                workspace_model(
                    id="workspace_id",
                    status=Status.DELETED,
                    status_history=[
                        Status.CREATION_REQUESTED,
                        Status.DELETING,
                        Status.DELETED,
                    ],
                    deletion_date=RUN_NOW,
                ),
            ],
            [
                {
                    "status": Status.DELETING,
                    "status_history": [Status.CREATION_REQUESTED, Status.DELETING],
                },
                {
                    "status": Status.DELETED,
                    "status_history": [
                        Status.CREATION_REQUESTED,
                        Status.DELETING,
                        Status.DELETED,
                    ],
                },
            ],
        ],
        # index 1 — CREATING -> raises
        [
            [workspace_model(status=Status.CREATING)] * 2,
            {
                "expected_exception": WorkspaceDeletionFailedError,
                "match": "Cannot delete WorkspaceAlias with id 'workspace_id'",
            },
        ],
        # index 2 — ACTIVE -> DELETING -> DELETED
        [
            [
                workspace_model(
                    id="workspace_id",
                    status=Status.ACTIVE,
                    status_history=[Status.DELETION_REQUESTED],
                )
            ] * 2
            + [
                workspace_model(
                    id="workspace_id",
                    status=Status.DELETING,
                    status_history=[Status.DELETION_REQUESTED, Status.DELETING],
                ),
                workspace_model(
                    id="workspace_id",
                    status=Status.DELETED,
                    status_history=[Status.DELETION_REQUESTED, Status.DELETING],
                ),
            ],
            [
                {
                    "status": Status.DELETING,
                    "status_history": [
                        Status.DELETION_REQUESTED,
                        Status.DELETING,
                    ],
                }
            ],
        ],
        # index 3 — already DELETED -> no calls
        [
            [workspace_model(id="workspace_id", status=Status.DELETED)],
            [],
        ],
        # index 4 — FAILED -> now deletable, goes DELETING then DELETED
        [
            [
                workspace_model(
                    id="workspace_id",
                    status=Status.FAILED,
                    status_history=[Status.FAILED],
                ),
                workspace_model(
                    id="workspace_id",
                    status=Status.DELETING,
                    status_history=[Status.FAILED, Status.DELETING],
                    deletion_date=RUN_NOW,
                ),
                workspace_model(
                    id="workspace_id",
                    status=Status.DELETED,
                    status_history=[Status.FAILED, Status.DELETING, Status.DELETED],
                    deletion_date=RUN_NOW,
                ),
            ],
            [
                {
                    "status": Status.DELETING,
                    "status_history": [Status.FAILED, Status.DELETING],
                },
                {
                    "status": Status.DELETED,
                    "status_history": [
                        Status.FAILED,
                        Status.DELETING,
                        Status.DELETED,
                    ],
                },
            ],
        ],
        # index 5 — unknown string status "toto" -> raises
        [
            [workspace_model(status="toto")] * 2,
            {
                "expected_exception": WorkspaceDeletionFailedError,
                "match": "Cannot delete WorkspaceAlias with id 'workspace_id'",
            },
        ],
    ],
)
@pytest.mark.unit
@pytest.mark.component
def test_delete(
    mock_repository,
    mocked_workspace_service,
    grafana_service_with_mock,
    input_app,
    calls,
    mocker_post,
    mocker,
):
    mocker.patch(
        "dataviz_core.adapters.requests_http_client.RequestsHTTPClient.post",
        return_value=mocker_post,
    )
    mock_repository.workspace.get_by_id.side_effect = input_app
    if isinstance(input_app[0].status, str):
        mocked_workspace_service._refresh_workspace = Mock(side_effect=input_app)
    else:
        mocked_workspace_service._refresh_workspace = Mock(side_effect=input_app)
    mocker.patch("dataviz_core.models.utils.utcnow", return_value=RUN_NOW)
    if "expected_exception" in calls:
        with pytest.raises(**calls):
            mocked_workspace_service.delete_workspace("workspace_id")
        return
    mocked_workspace_service.delete_workspace("workspace_id")
    if input_app:
        mock_repository.workspace.update.assert_has_calls(
            [call(id="workspace_id", **kwargs) for kwargs in calls]
        )


# ============================================================
# FIX 2: Replace test_delete_workspace_sg_connect_none_skips_gracefully
#
# The issue: Mock() creates child mocks for ANY attribute access,
# so ws.sg_connect returns a child Mock, not None.
# Fix: use a simple class or configure Mock to return None for sg_connect.
# ============================================================

@pytest.mark.unit
@pytest.mark.component
def test_delete_workspace_sg_connect_none_skips_gracefully(
    mock_repository,
    mocked_workspace_service,
    mocker,
):
    """
    Workspace created before sg_connect integration has sg_connect=None.
    _delete_workspace should skip the remove_redirect_url call and
    still mark the workspace as DELETED.
    """
    # Use a simple namespace so attributes are exactly what we set
    # and nothing auto-creates child mocks
    from types import SimpleNamespace

    dns_obj = SimpleNamespace(certificate=None, fqdn="test.fqdn")
    kube_obj = SimpleNamespace(vault_secret_id=None)
    dp_obj = SimpleNamespace(id="comp-1", vault_secret_id="secret-1")

    ws = SimpleNamespace(
        id=uuid.uuid4(),
        name="my-ws",
        status=Status.ACTIVE,
        status_history=[Status.ACTIVE],
        sg_connect=None,          # truly None — SimpleNamespace doesn't auto-mock
        dataplane_component=dp_obj,
        dns=dns_obj,
        kube_stack=kube_obj,
    )

    updated_ws = Mock(id=ws.id, name="my-ws", status=Status.DELETED)
    mocked_workspace_service._update_workspace_with_and_return = Mock(
        return_value=updated_ws
    )

    mocked_workspace_service._delete_workspace(ws)

    # sg_connect_service should NOT have been called
    mocked_workspace_service.sg_connect_service.remove_redirect_url.assert_not_called()
    # Should still mark DELETED
    mocked_workspace_service._update_workspace_with_and_return.assert_called_with(
        ws, status=Status.DELETED
    )
