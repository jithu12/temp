Great progress! Two separate issues here. Let me address them one by one.

## Issue 1 — `request_stack_deletion() got unexpected keyword argument 'stack_id'`

This is in Core's `workspace.py` line 933:
```python
self._kube_service.request_stack_deletion(
    stack_id=workspace.kube_stack_id,  # ← wrong parameter name
)
```

The `_kube_service.request_stack_deletion` method doesn't accept `stack_id` as a keyword argument. Check what parameter name it expects:

```bash
grep -n "def request_stack_deletion" dataviz_core/adapters/
# or
grep -rn "def request_stack_deletion" dataviz_core/
```

It probably expects something like `kube_stack_id` or just a positional argument. Once you find the right name, fix line 933 in `workspace.py`.

**This is your Lead's code** — raise it:
> "`_deactivate_workspace` calls `self._kube_service.request_stack_deletion(stack_id=...)` but `request_stack_deletion` doesn't accept `stack_id` as a keyword argument. What's the correct parameter name?"

## Issue 2 — Response shows ACTIVE even after deactivation succeeds

This is the expected behavior I mentioned earlier. Look at Core:

```python
def request_account_deactivation(self, owner_account_id, account_id):
    ...
    # Has workspaces → fires async job
    self.workflow_executor.async_exec_core_function(...)

    return account  # ← returns account AS-IS before async job completes
```

When workspaces exist, Core fires the async job and **immediately returns the account object** — which still has `status=ACTIVE` because the async job hasn't run yet. The actual status change to INACTIVE happens in the background after the async job completes.

This is correct async behavior — the response tells you "request accepted" (202), not "already done." The status in the response reflects the state **at the time of the request**.

To fix the response to be less confusing, update `_account_to_response` in your controller to return a more descriptive status when the account has workspaces — OR simply accept that 202 means "in progress."

Actually the simplest fix is in Core — instead of returning `account` (which has old status), return a version that indicates the request is in progress. But that's a Core change.

For now the cleanest API-side fix is to **not return the account status at all** when the async path is taken, and instead return a clear message:

```python
return {
    "id": str(getattr(account, "id", "")),
    "owner_account_id": str(getattr(account, "owner_account_id", "")),
    "status": "DEACTIVATION_REQUESTED",  # ← descriptive
    "name": getattr(account, "name", None),
}, 202
```

But wait — looking at this more carefully, Core's `request_account_deactivation` always returns `account` on the async path. Your `_account_to_response` just serializes whatever Core returns. The fix should be in how you interpret the 202 response.

## Summary of what's actually working vs broken

| Item | Status |
|---|---|
| `api_account_details` status updating | ✅ Working |
| Response status showing ACTIVE on async path | ⚠️ Expected — async hasn't completed yet |
| `api_workspace` status updating | ❌ Blocked by `request_stack_deletion` param bug |

## What to do right now

**Step 1 — Find the correct parameter name:**
```bash
grep -rn "def request_stack_deletion" dataviz_core/
```

**Step 2 — Fix the call in `workspace.py` line 933:**
```python
# Change from:
self._kube_service.request_stack_deletion(
    stack_id=workspace.kube_stack_id,
)

# To whatever the correct param name is, e.g.:
self._kube_service.request_stack_deletion(
    kube_stack_id=workspace.kube_stack_id,
)
```

**Step 3 — Raise the response-status issue to your Lead** — since it's Core returning the stale status on the async path, not your API code.

Share what `grep -rn "def request_stack_deletion"` returns and I'll tell you the exact fix.
