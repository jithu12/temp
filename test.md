Good progress — down to 6 failures. Let me fix them all.

## Issue 1 — `_account_client` vs `account_client`

The test uses `account_service._account_client` but the actual attribute is `account_service.account_client` (no underscore). Simple fix in the test.

## Issue 2 — `patch` not defined in `test_delete_workspace_sg_connect_none_skips_gracefully`

The test uses `patch` but it's not imported at the top of the test file. Need to use `mocker` instead since the fixture already has it.

## Issue 3 — `test_delete[input_app5-calls5]` still need to see it

Still need that parametrized test. But let me fix the 5 I can fix now.

Open `tests/services/test_accounts.py` and make these replacements:

### Fix 1 — `test_get_account_details_by_id_returns_client_result`

```python
@pytest.mark.unit
@pytest.mark.component
def test_get_account_details_by_id_returns_client_result(account_service):
    mock_account = MagicMock(spec=Account)
    account_service.account_client.get_account_by_id.return_value = mock_account

    workspace = Workspace(owner_account_id="12345")
    result = account_service.get_account_details_by_id(workspace)

    assert result is mock_account
    account_service.account_client.get_account_by_id.assert_called_once_with("12345")
    account_service.logger.info.assert_called_once()
```

### Fix 2 — `test_get_soft_limit_with_non_existing_account_details`

```python
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
    account_service.account_client.get_account_by_id.return_value = mock_account

    soft_limit = account_service.get_soft_limit(owner_account_id)
    assert soft_limit == WORKSPACE_LIMIT_PER_ACCOUNT
```

### Fix 3 — `test_get_soft_limit_account_details_not_found_from_client`

```python
@pytest.mark.unit
@pytest.mark.component
def test_get_soft_limit_account_details_not_found_from_client(account_service):
    mock_repo = MockRepo()
    account_service.repositories.account_details.get_by_owner_account_id.side_effect = (
        lambda oid: mock_repo.get_by_owner_account_id(oid)
    )
    account_service.account_client.get_account_by_id.return_value = None

    with pytest.raises(Exception, match="Error while getting Account details"):
        account_service.get_soft_limit("12345")
```

### Fix 4 — `test_get_soft_limit_with_non_existing_account_and_client_failure`

```python
@pytest.mark.unit
@pytest.mark.component
def test_get_soft_limit_with_non_existing_account_and_client_failure(
    account_service,
):
    mock_repo = MockRepo()
    account_service.repositories.account_details.get_by_owner_account_id.side_effect = (
        lambda oid: mock_repo.get_by_owner_account_id(oid)
    )
    account_service.account_client.get_account_by_id.side_effect = Exception(
        "Client API error"
    )

    with pytest.raises(Exception, match="Client API error"):
        account_service.get_soft_limit("12345")
```

### Fix 5 — `test_delete_workspace_sg_connect_none_skips_gracefully`

Replace the entire function in `tests/services/test_workspace.py`:

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
    ws.sg_connect = None
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

Note — removed `patch` from the signature since it wasn't imported. The test only needs `mocker` which is already available from pytest-mock.

## For `test_delete[input_app5-calls5]` — still need to see it

Run this and share:
```bash
sed -n '573,800p' tests/services/test_workspace.py
```

That's the last one. Once you share it I'll fix it immediately.
