Sure. Here are all 7 functions you need to manually replace in your test files. Each one is a complete drop-in replacement.

## File 1: `tests/services/test_accounts.py`

### Function 1 — Replace `test_request_account_deactivation_with_active_workspaces_fires_async`

```python
@pytest.mark.unit
@pytest.mark.component
def test_request_account_deactivation_with_active_workspaces_fires_async(
    account_service, patched_admin, mocker
):
    mock_account = MagicMock(spec=AccountDetails, status=Status.ACTIVE)
    account_service.repositories.account_details.list.return_value = [mock_account]
    account_service.repositories.workspace.list.return_value = [
        MagicMock(spec=Workspace)
    ]
    account_service.workflow_executor = MagicMock()
    mocker.patch.object(
        account_service,
        "_update_account_with_and_return",
        return_value=mock_account,
    )

    result = account_service.request_account_deactivation(
        owner_account_id="owner-1", account_id=ADMIN_ID
    )

    assert result is mock_account
    account_service.workflow_executor.async_exec_core_function.assert_called_once_with(
        service="account",
        function="deactivate_account",
        kwargs={"owner_account_id": "owner-1"},
    )
```

### Function 2 — Replace `test_request_account_reactivation_with_inactive_workspaces_fires_async`

```python
@pytest.mark.unit
@pytest.mark.component
def test_request_account_reactivation_with_inactive_workspaces_fires_async(
    account_service, patched_admin, mocker
):
    mock_account = MagicMock(spec=AccountDetails, status=Status.INACTIVE)
    account_service.repositories.account_details.list.return_value = [mock_account]
    account_service.repositories.workspace.list.return_value = [
        MagicMock(spec=Workspace)
    ]
    account_service.workflow_executor = MagicMock()
    mocker.patch.object(
        account_service,
        "_update_account_with_and_return",
        return_value=mock_account,
    )

    result = account_service.request_account_reactivation(
        owner_account_id="owner-1", account_id=ADMIN_ID
    )

    assert result is mock_account
    account_service.workflow_executor.async_exec_core_function.assert_called_once_with(
        service="account",
        function="reactivate_account",
        kwargs={"owner_account_id": "owner-1"},
    )
```

### Function 3 — Replace `test_request_account_deletion_account_not_active`

```python
@pytest.mark.unit
@pytest.mark.component
def test_request_account_deletion_account_not_active(
    account_service, patched_admin
):
    # Core raises AccountNotInActiveException for DELETED accounts
    mock_account = MagicMock(
        spec=AccountDetails, status=Status.DELETED, id="acct-1"
    )
    account_service.repositories.account_details.list.return_value = [mock_account]

    with pytest.raises(AccountNotInActiveException):
        account_service.request_account_deletion(
            owner_account_id="owner-1", account_id=ADMIN_ID
        )
```

### Function 4 — Replace `test_request_account_deletion_with_active_workspaces_fires_async`

```python
@pytest.mark.unit
@pytest.mark.component
def test_request_account_deletion_with_active_workspaces_fires_async(
    account_service, patched_admin, mocker
):
    mock_account = MagicMock(spec=AccountDetails, status=Status.ACTIVE)
    account_service.repositories.account_details.list.return_value = [mock_account]
    account_service.repositories.workspace.list.return_value = [
        MagicMock(spec=Workspace)
    ]
    account_service.workflow_executor = MagicMock()
    mocker.patch.object(
        account_service,
        "_update_account_with_and_return",
        return_value=mock_account,
    )

    result = account_service.request_account_deletion(
        owner_account_id="owner-1", account_id=ADMIN_ID
    )

    assert result is mock_account
    account_service.workflow_executor.async_exec_core_function.assert_called_once_with(
        service="account",
        function="delete_account",
        kwargs={"owner_account_id": "owner-1"},
    )
```

## File 2: `tests/services/test_workspace.py`

### Function 5 — Replace `test_delete_workspace_sg_connect_none_skips_gracefully`

```python
@pytest.mark.unit
@pytest.mark.component
def test_delete_workspace_sg_connect_none_skips_gracefully(
    mock_repository, mocked_workspace_service, mocker
):
    """
    Workspace created before sg_connect integration has sg_connect=None.
    _delete_workspace should skip the remove_redirect_url call and
    still mark the workspace as DELETED.
    """
    ws = Mock()
    ws.id = uuid.uuid4()
    ws.name = "my-ws"
    ws.status = Status.ACTIVE
    ws.status_history = [Status.ACTIVE]
    ws.sg_connect = None  # set AFTER creation so it's truly None
    ws.dataplane_component = Mock(id="comp-1", vault_secret_id="secret-1")
    ws.dns = Mock(certificate=None, fqdn="test.fqdn")
    ws.kube_stack = Mock(vault_secret_id=None)

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
```

### Function 6 — Replace `test_delete_workspace_with_exception`

```python
@pytest.mark.unit
@pytest.mark.component
def test_delete_workspace_with_exception(
    mocked_workspace_service_exception, mock_repository
):
    """
    After our fix _delete_workspace should NOT raise
    WorkspaceDeletionFailedError when external calls fail.
    It logs and still marks workspace DELETED.
    """
    ws = Mock()
    ws.id = "workspace_id"
    ws.name = "workspace"  # must be a real string for logname()
    ws.status = Status.ACTIVE
    ws.status_history = [Status.ACTIVE]
    ws.sg_connect = None
    ws.dataplane_component = Mock(id=None, vault_secret_id="secret-1")
    ws.dns = Mock(certificate=None, fqdn="test.fqdn")
    ws.kube_stack = Mock(vault_secret_id=None)

    updated_ws = Mock(id=ws.id, name="workspace", status=Status.DELETED)
    mocked_workspace_service_exception._update_workspace_with_and_return = Mock(
        return_value=updated_ws
    )
    mocked_workspace_service_exception._update_workspace_with = Mock()

    # Should NOT raise
    result = mocked_workspace_service_exception._delete_workspace(ws, False)
    assert result.status == Status.DELETED
    # Should NOT have been set to FAILED
    mocked_workspace_service_exception._update_workspace_with.assert_not_called()
```

### Function 7 — Replace `test_reactivate_workspaces_by_owner_account_id`

```python
@pytest.mark.unit
@pytest.mark.component
def test_reactivate_workspaces_by_owner_account_id(
    mock_repository, mocked_workspace_service
):
    owner_id = uuid.uuid4()
    ws = Mock()
    ws.id = uuid.uuid4()
    ws.name = "test-workspace"  # must be a real string for logname()
    ws.status = Status.INACTIVE
    ws.status_history = []
    ws.owner_account_id = str(owner_id)
    mock_repository.workspace.list.return_value = [ws]

    # After our fix reactivate_workspace succeeds for INACTIVE workspaces
    reactivated_ws = Mock(
        id=ws.id,
        name="test-workspace",
        status=Status.ACTIVE,
    )
    mocked_workspace_service.reactivate_workspace = Mock(
        return_value=reactivated_ws
    )

    result = mocked_workspace_service.reactivate_workspaces_by_owner_account_id(
        owner_id
    )
    assert result is not None
    assert len(result) == 1
    assert result[0]["status"] == "reactivated"
```

## For the last failure `test_delete[input_app5-calls5]`

I still need to see the parametrized test. Run on your server:

```bash
sed -n '573,800p' tests/services/test_workspace.py
```

That'll show the parametrize block at line 573 and the function at line 686. Paste the output and I'll give you the exact fix for the failing parameter set.

## How to apply

1. Open each test file in your editor (nano, VS Code, etc.)
2. Use Ctrl+F or `/` to search for the function name (e.g. `test_request_account_deactivation_with_active_workspaces_fires_async`)
3. Select the entire function from `@pytest.mark.unit` down to the closing `)` of the last assertion
4. Delete it and paste the new version above
5. Save

After replacing all 7, run pytest again and the only remaining failure should be the `test_delete[input_app5-calls5]` one.
