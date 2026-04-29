Sure. Let me find all the information I need first.

From everything we've seen across this conversation, here's what I know:

**Fix 1** — `workspace.py` line 933: `request_stack_deletion(stack_id=...)` wrong param name.
**Fix 2** — Response showing ACTIVE on async path — controller side fix.
**Fix 3** — `service="accounts"` → `service="account"` already done.

Let me produce all fixes cleanly.

## Fix 1 — `dataviz_core/services/workspace.py`

Find line 933 area:
```bash
grep -n "request_stack_deletion" dataviz_core/services/workspace.py
```

It currently says:
```python
self._kube_service.request_stack_deletion(
    stack_id=workspace.kube_stack_id,
)
```

Check the correct param name:
```bash
grep -n "def request_stack_deletion" dataviz_core/adapters/kube_client.py
```

Based on the pattern I saw in `_activate_workspace` earlier (which used `ws_name`, `name`, `dns_id`, `database_id`, `kube_namespace_id`), the deletion probably just needs the stack id directly. Change to:

```python
self._kube_service.request_stack_deletion(
    workspace.kube_stack_id,  # positional, no keyword
)
```

Or if it has a different keyword name, whatever `def request_stack_deletion` shows. Share the output of that grep and I'll confirm.

## Fix 2 — `dataviz_core/services/accounts.py`

The response shows ACTIVE because Core returns the account object **before** the async job runs. Fix this by returning the account with updated status immediately after firing the async job.

In `request_account_deactivation`, change:

```python
self.workflow_executor.async_exec_core_function(
    service="account",
    function="deactivate_account",
    kwargs={"owner_account_id": owner_account_id},
)

return account  # ← returns stale ACTIVE status
```

To:

```python
self.workflow_executor.async_exec_core_function(
    service="account",
    function="deactivate_account",
    kwargs={"owner_account_id": owner_account_id},
)

# Return account with updated status so response reflects
# what was requested, not the stale pre-async status
return self._update_account_with_and_return(
    account, status=Status.INACTIVE
)
```

Same fix for `request_account_reactivation`:
```python
# Change:
return account

# To:
return self._update_account_with_and_return(
    account, status=Status.ACTIVE
)
```

And `request_account_deletion`:
```python
# Change:
return account

# To:
return self._update_account_with_and_return(
    account, status=Status.DELETED
)
```

## Fix 3 — `accounts_controller.py` (already in your file)

No change needed here — the controller correctly returns `_account_to_response(account)` which will now show the updated status since Fix 2 updates it before returning.

## Fix 4 — Confirm `service="account"` change

In `dataviz_core/services/accounts.py`, confirm all three places say `service="account"` (singular):

```python
# request_account_deactivation
self.workflow_executor.async_exec_core_function(
    service="account",           # ← singular
    function="deactivate_account",
    kwargs={"owner_account_id": owner_account_id},
)

# request_account_reactivation
self.workflow_executor.async_exec_core_function(
    service="account",           # ← singular
    function="reactivate_account",
    kwargs={"owner_account_id": owner_account_id},
)

# request_account_deletion
self.workflow_executor.async_exec_core_function(
    service="account",           # ← singular
    function="delete_account",
    kwargs={"owner_account_id": owner_account_id},
)
```

## One thing I need from you before confirming Fix 1

Run this and share the output:
```bash
grep -n "def request_stack_deletion" dataviz_core/adapters/kube_client.py
# or if that doesn't find it:
grep -rn "def request_stack_deletion" dataviz_core/
```

That'll show me the exact method signature so I can give you the precise parameter name for Fix 1. Everything else above is ready to apply.
