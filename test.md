Now I can see exactly what's happening.

Look at `_delete_workspace` lines 443-446:

```python
if not is_failed:
    self.logger.info(f"Deleting {logname(workspace)}...")
    workspace = self._update_workspace_with_and_return(
        workspace, status=Status.DELETING  # ← replaces ws with a new object
    )
```

When `is_failed=False` (default), `_delete_workspace` calls `_update_workspace_with_and_return` which returns a NEW workspace object (the refreshed one from DB). The local variable `workspace` is now reassigned to this new object — NOT the `SimpleNamespace` `ws` you passed in.

So when `sg_connect` is checked on line 447:
```python
if workspace.sg_connect is not None:
```

`workspace` is now the mock returned by `_update_workspace_with_and_return` (which is `updated_ws = Mock(...)`) — and `Mock().sg_connect` is a child Mock, not `None`. So the guard fails and `remove_redirect_url` gets called.

## The fix — update `updated_ws` to also have `sg_connect=None`

In the test, `updated_ws` needs `sg_connect=None` too since that's what gets reassigned to `workspace` after the DELETING update:

Replace the test with this:

```python
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
    from types import SimpleNamespace

    dns_obj = SimpleNamespace(certificate=None, fqdn="test.fqdn")
    kube_obj = SimpleNamespace(
        vault_secret_id=None,
        kube_namespace=SimpleNamespace(id="ns-1"),
        id="stack-1",
    )
    dp_obj = SimpleNamespace(id="comp-1", vault_secret_id="secret-1")

    ws = SimpleNamespace(
        id=uuid.uuid4(),
        name="my-ws",
        status=Status.ACTIVE,
        status_history=[Status.ACTIVE],
        sg_connect=None,
        dataplane_component=dp_obj,
        dns=dns_obj,
        kube_stack=kube_obj,
        dns_id="dns-1",
    )

    # updated_ws is what _update_workspace_with_and_return returns
    # after setting DELETING status — it must ALSO have sg_connect=None
    # because _delete_workspace reassigns workspace = this returned object
    # and then checks workspace.sg_connect
    updated_ws = SimpleNamespace(
        id=ws.id,
        name="my-ws",
        status=Status.DELETING,
        status_history=[Status.ACTIVE, Status.DELETING],
        sg_connect=None,          # ← critical — must be None here too
        dataplane_component=dp_obj,
        dns=dns_obj,
        kube_stack=kube_obj,
        dns_id="dns-1",
    )

    final_ws = SimpleNamespace(
        id=ws.id,
        name="my-ws",
        status=Status.DELETED,
    )

    # First call returns updated_ws (DELETING), second returns final_ws (DELETED)
    mocked_workspace_service._update_workspace_with_and_return = Mock(
        side_effect=[updated_ws, final_ws]
    )

    mocked_workspace_service._delete_workspace(ws)

    # sg_connect_service should NOT have been called
    mocked_workspace_service.sg_connect_service.remove_redirect_url.assert_not_called()
    # Should have been called twice — once for DELETING, once for DELETED
    assert mocked_workspace_service._update_workspace_with_and_return.call_count == 2
```

The key insight: `_delete_workspace` calls `_update_workspace_with_and_return` twice:
1. First to set `DELETING` — returns `updated_ws`
2. Then `workspace` is now `updated_ws` — so `workspace.sg_connect` must be `None` on `updated_ws` too
3. Second call at the end to set `DELETED` — returns `final_ws`

So `_update_workspace_with_and_return` needs `side_effect=[updated_ws, final_ws]` not just `return_value=updated_ws`.
