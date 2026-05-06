No — that function is already correct. What I need you to do first is add **temporary debug logs** to find out exactly where the status update is failing.

Add these 2 lines temporarily in your server's `accounts.py`, inside `request_account_deactivation`, right before the final return:

Find this at the bottom of the function:
```python
self.workflow_executor.async_exec_core_function(
    service="account",
    function="deactivate_account",
    kwargs={"owner_account_id": owner_account_id},
)

return self._update_account_with_and_return(
    account,
    status=Status.INACTIVE
)
```

Change to:
```python
self.workflow_executor.async_exec_core_function(
    service="account",
    function="deactivate_account",
    kwargs={"owner_account_id": owner_account_id},
)

self.logger.error(f"DEBUG BEFORE UPDATE: account.status = {account.status!r}")
result = self._update_account_with_and_return(account, status=Status.INACTIVE)
self.logger.error(f"DEBUG AFTER UPDATE: result.status = {result.status!r}")
return result
```

Also add the same two lines in the `if len(workspaces) == 0` block:
```python
if len(workspaces) == 0:
    self.logger.info(
        f"No active workspaces found for owner_account_id: {owner_account_id}"
    )
    self.logger.error(f"DEBUG BEFORE UPDATE: account.status = {account.status!r}")
    result = self._update_account_with_and_return(account, status=Status.INACTIVE)
    self.logger.error(f"DEBUG AFTER UPDATE: result.status = {result.status!r}")
    return result
```

Then:
1. Restart API server
2. Call deactivate in Swagger with a real UUID
3. Check the API server logs — find the two `DEBUG` lines
4. Also run this SQL immediately after:

```sql
SELECT owner_account_id, status 
FROM public.api_account_details
WHERE owner_account_id = 'your-real-uuid';
```

Share:
- The two DEBUG log lines
- The SQL result

That will tell me exactly where it's breaking — whether `_update_account_with_and_return` is failing silently, or whether the DB is updating but the response is wrong, or something else. Then I'll give you the precise fix.
