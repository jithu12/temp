Crystal clear. The error is:

```
TypeError: update_resource_with() missing 1 required positional argument: 'repository'
```

My `_update_account_with` is calling `update_resource_with` with wrong argument names. The original code used `repositories=` but the actual function signature requires `repository=` (singular, no `s`).

Check the exact signature right now:

```bash
grep -n "def update_resource_with" dataviz_core/utils/__init__.py
# or
grep -rn "def update_resource_with" dataviz_core/
```

Share what comes back. But based on the error, the fix is one of these two things in `_update_account_with`:

**Option A — wrong keyword name (`repositories` vs `repository`):**
```python
# Current (wrong)
actual_changes = update_resource_with(
    ctx_manager=self.autocommit(),
    repositories=self.repositories.account_details,  # ← wrong keyword
    resource=account,
    logger=self.logger,
    **changes,
)

# Fix
actual_changes = update_resource_with(
    ctx_manager=self.autocommit(),
    repository=self.repositories.account_details,    # ← singular
    resource=account,
    logger=self.logger,
    **changes,
)
```

**Option B — positional argument (no keyword at all):**
```python
actual_changes = update_resource_with(
    self.autocommit(),
    self.repositories.account_details,
    account,
    self.logger,
    **changes,
)
```

## How to find the correct signature

Run:
```bash
grep -rn "def update_resource_with" dataviz_core/
```

Then share the function signature line. OR look at how other services call it:
```bash
grep -rn "update_resource_with(" dataviz_core/services/workspace.py | head -5
```

That'll show you the exact argument names workspace.py uses — since workspace works correctly, match that exact pattern.

## Also — still using fake UUID

```
target_account_id: bbbb2222-bbbb-bbbb-bbbb-bbbbbbbbbbbb
```

You're still testing with a fake UUID. Once we fix the `update_resource_with` error, please test with a real `owner_account_id` from your DB — otherwise you'll keep hitting `AccountNotFoundException` and related issues.

Share the `grep` output and I'll give you the one-line fix.
