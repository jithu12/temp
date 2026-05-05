Got it — the issue is in your controller's `account_delete` pre-flight check. Currently it calls `_assert_account_is_active` which rejects INACTIVE accounts. But according to the lifecycle, deletion should work on INACTIVE accounts too (deactivate first, then delete).

## The fix — in `accounts_controller.py`

Find `account_delete` and change `_assert_account_is_active` to a new check that accepts both ACTIVE and INACTIVE:

```python
def account_delete(**kwargs: Any) -> Tuple[Dict[str, Any], int]:
    """
    DELETE /admin/v1/accounts/{target_account_id}

    Deletes the target account and all its associated workspaces.

    Pre-checks:
    - Caller must be in ALLOWED_ACCOUNT_IDS
    - Account must exist
    - Account must be ACTIVE or INACTIVE (not already DELETED)
    """
    core = get_core(current_app)

    try:
        _validate_allowed_account()

        accounts_service = _get_accounts_service(core)
        target_owner_account_id = _get_target_account_id(kwargs)
        caller_account_id = _get_caller_account_id(kwargs)

        # Pre-flight check — only block if already DELETED
        account = _check_account_exists(accounts_service, target_owner_account_id)
        _assert_account_is_deletable(account, target_owner_account_id)

        account = accounts_service.request_account_deletion(
            owner_account_id=target_owner_account_id,
            account_id=caller_account_id,
        )

        return _account_to_response(account), 202

    except ValueError as e:
        return _handle_state_error(e)
    except HTTPException:
        raise
    except Exception as e:
        return _handle_core_exception(e)
```

And add the new `_assert_account_is_deletable` helper alongside your other pre-flight checks:

```python
def _assert_account_is_deletable(account: Any, owner_account_id: UUID) -> None:
    """
    Raise a clear 409 if the account cannot be deleted.
    Account can be deleted from ACTIVE or INACTIVE state.
    Only DELETED accounts are rejected.
    """
    status = _get_account_status(account)

    if status == "DELETED":
        raise ValueError(
            f"ACCOUNT_ALREADY_DELETED:"
            f"Account '{owner_account_id}' has already been deleted. "
            f"No changes were made."
        )

    if status not in ("ACTIVE", "INACTIVE"):
        raise ValueError(
            f"ACCOUNT_INVALID_STATE:"
            f"Account '{owner_account_id}' is in state '{status}' "
            f"and cannot be deleted. Only ACTIVE or INACTIVE accounts "
            f"can be deleted."
        )
```

## Also — Core's `request_account_deletion` check

Remember Core also checks:
```python
if account.status is not Status.ACTIVE:
    raise AccountNotInActiveException(account.id)
```

This means Core will reject INACTIVE accounts too. So even after fixing the controller, Core will block it.

You need to fix Core's `request_account_deletion` as well. Find this in `accounts.py`:

```python
if account.status is not Status.ACTIVE:
    self.logger.info(
        f"Account with id: {owner_account_id} is not active. "
        f"Current status: {account.status}"
    )
    raise AccountNotInActiveException(account.id)
```

Change to:

```python
if account.status == Status.DELETED:
    self.logger.info(
        f"Account with id: {owner_account_id} is already deleted."
    )
    raise AccountNotInActiveException(account.id)

if account.status not in (Status.ACTIVE, Status.INACTIVE):
    self.logger.info(
        f"Account with id: {owner_account_id} cannot be deleted. "
        f"Current status: {account.status}"
    )
    raise AccountNotInActiveException(account.id)
```

This allows both ACTIVE and INACTIVE accounts to be deleted, only rejecting already-DELETED ones.

## Summary of all changes

| File | Change |
|---|---|
| `accounts_controller.py` | Replace `_assert_account_is_active` with new `_assert_account_is_deletable` in `account_delete` |
| `accounts_controller.py` | Add `_assert_account_is_deletable` helper function |
| `dataviz_core/services/accounts.py` | Change `request_account_deletion` status check to allow ACTIVE and INACTIVE |

After these changes the full lifecycle works:
```
ACTIVE → deactivate → INACTIVE → delete → DELETED
# or
ACTIVE → delete directly → DELETED
```

Both paths are valid. Do you want me to regenerate the full `accounts_controller.py` with this fix included?
