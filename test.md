OK I can see the test failures and the test file. Let me work through what's broken.

## The 3 failures

```
FAILED tests/services/test_accounts.py::test_get_by_owner_id_success
  AssertionError: assert <MagicMock ...list().__getitem__()...> is <MagicMock ...list().first()...>

FAILED tests/services/test_accounts.py::test_get_by_owner_id_raises_account_not_found
  Exception: db err

FAILED tests/services/test_accounts.py::test_request_account_deactivation_no_active_workspaces
  AccountNotInActiveException: <MagicMock ...list().__getitem__().id...>
```

All three failures are caused by **the same root issue**: the tests were written for the **old** `get_by_owner_id` (the buggy one that called `.first()`), but the **new** code uses `results[0]` (which calls `__getitem__`). The mocks aren't aligned with the new implementation.

Let me walk through each:

## Failure 1: `test_get_by_owner_id_success`

Looking at line 150 in your test:
```python
account_service.repositories.account_details.list.return_value.first.return_value = mock_account
```

This sets up `list().first()` to return `mock_account`. But the new code does `results[0]`, which on a MagicMock triggers `__getitem__(0)` — a different mock attribute. So `results[0]` returns a fresh MagicMock, not your `mock_account`. The assertion `assert result is mock_account` fails because `result` is `list().__getitem__()` while `mock_account` is `list().first()`.

**Fix:** Update the mock setup to align with the new code path:
```python
account_service.repositories.account_details.list.return_value = [mock_account]
```

That makes `list(filters=...)` return a real Python list `[mock_account]`, and `results[0]` correctly returns `mock_account`.

## Failure 2: `test_get_by_owner_id_raises_account_not_found`

Line 158:
```python
account_service.repositories.account_details.list.side_effect = Exception("db err")
```

This made `list()` itself throw `Exception("db err")`. The old code wrapped `list().first()` in `try/except Exception`, so any error became `AccountNotFoundException`. **You removed that try/except in your fix.** So now the raw `Exception("db err")` propagates up, and pytest sees it as an unhandled exception instead of the expected `AccountNotFoundException`.

**Fix:** The new code raises `AccountNotFoundException` only when `results` is empty (not on exceptions). So the test should reflect the new contract: not-found means empty results, not an exception.

```python
account_service.repositories.account_details.list.return_value = []
with pytest.raises(AccountNotFoundException):
    account_service.get_by_owner_id("owner-123")
```

This tests the actual new behavior: when no rows match, raise `AccountNotFoundException`.

## Failure 3: `test_request_account_deactivation_no_active_workspaces`

Line 179:
```python
account_service.repositories.account_details.list.return_value.first.return_value = mock_account
```

Same problem as Failure 1 — the test is setting up `.first()` but the code calls `[0]`. So when `request_account_deactivation` internally calls `get_by_owner_id`, `results[0]` returns a MagicMock (not `mock_account`). That MagicMock has a `.status` that's also a MagicMock, which is `is not Status.ACTIVE`, so Core raises `AccountNotInActiveException` — exactly what we see in the error.

**Fix:** Same pattern:
```python
mock_account = MagicMock(spec=AccountDetails, status=Status.ACTIVE)
account_service.repositories.account_details.list.return_value = [mock_account]
account_service.repositories.workspace.list.return_value = []
```

## Why the test file uses two different patterns

I notice the test file actually has TWO different mocking patterns coexisting:

- Lines 75–88 use a custom `MockRepo` class that returns real lists from `.list()` (returns `list(self.mock_dicts.values())`).
- Lines 147–189 use `MagicMock` directly with `list.return_value.first.return_value = ...`.

The `MockRepo` pattern was already designed for the new code (returns real lists, supports `[0]` indexing). The `MagicMock` pattern was written for the old `.first()` code. Whoever wrote those three failing tests didn't use `MockRepo` — they used MagicMock the way the original buggy code expected.

## The fixes — three test functions to update

Here's what each should look like:

**`test_get_by_owner_id_success`** (around line 147):
```python
@pytest.mark.unit
@pytest.mark.component
def test_get_by_owner_id_success(account_service):
    mock_account = MagicMock(spec=AccountDetails)
    account_service.repositories.account_details.list.return_value = [mock_account]
    
    result = account_service.get_by_owner_id("owner-123")
    assert result is mock_account
```

**`test_get_by_owner_id_raises_account_not_found`** (around line 157):
```python
@pytest.mark.unit
@pytest.mark.component
def test_get_by_owner_id_raises_account_not_found(account_service):
    account_service.repositories.account_details.list.return_value = []
    with pytest.raises(AccountNotFoundException):
        account_service.get_by_owner_id("owner-123")
```

**`test_request_account_deactivation_no_active_workspaces`** (around line 174):
```python
@pytest.mark.unit
@pytest.mark.component
def test_request_account_deactivation_no_active_workspaces(account_service, mocker):
    admin_id = "admin"
    mocker.patch("dataviz_core.services.accounts.ADMIN_ACCOUNTS", f'"{admin_id}"')
    
    mock_account = MagicMock(spec=AccountDetails, status=Status.ACTIVE)
    account_service.repositories.account_details.list.return_value = [mock_account]
    account_service.repositories.workspace.list.return_value = []
    
    updated = MagicMock(status=Status.INACTIVE)
    mocker.patch.object(
        account_service, "_update_account_with_and_return", return_value=updated
    )
    
    result = account_service.request_account_deactivation("owner-1", admin_id)
    assert result.status == Status.INACTIVE
```

The key change in all three: `.list.return_value = [mock_account]` instead of `.list.return_value.first.return_value = mock_account`. And dropping the `side_effect = Exception(...)` pattern in the not-found test, since not-found is now signaled by an empty list, not by an exception.

## Also worth adding

While you're updating tests, add a couple more to cover the bug we hit in production:

```python
@pytest.mark.unit
@pytest.mark.component
def test_get_by_owner_id_returns_correct_account_when_filtered(account_service):
    """Regression test: get_by_owner_id must return the account matching
    the filter, not just the first row in the table."""
    target = MagicMock(spec=AccountDetails)
    # Repo returns just the filtered result (one row)
    account_service.repositories.account_details.list.return_value = [target]
    
    result = account_service.get_by_owner_id("owner-target")
    assert result is target

@pytest.mark.unit
@pytest.mark.component
def test_get_by_owner_id_passes_correct_filter(account_service):
    """get_by_owner_id should filter by owner_account_id."""
    account_service.repositories.account_details.list.return_value = [MagicMock(spec=AccountDetails)]
    
    account_service.get_by_owner_id("owner-xyz")
    
    # Verify the filter was constructed with the right value
    call_kwargs = account_service.repositories.account_details.list.call_args.kwargs
    filters = call_kwargs.get("filters", [])
    assert any(
        getattr(f, "value", None) == "owner-xyz" or "owner-xyz" in str(f)
        for f in filters
    )
```

These would have caught the original `.first()` bug in CI before it ever shipped.

Want me to write out all three fixed tests in full? Or shall I generate a consolidated patch you can drop in?
