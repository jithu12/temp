I can see `DELETION_REQUESTED` and `DELETING` exist but **`DEACTIVATION_REQUESTED` and `ACTIVATION_REQUESTED` don't exist yet**. We need to add them to the enum first, then update `accounts.py`.

## Step 1 — Add new statuses to `shared_enums.py`

Open `dataviz_core/models/shared_enums.py` and add two new values to the `Status` enum:

```python
class Status(Enum):
    CREATION_REQUESTED = "CREATION REQUESTED"
    CREATING = "CREATING"
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    ACTIVATION_REQUESTED = "ACTIVATION REQUESTED"      # ← ADD THIS
    DEACTIVATION_REQUESTED = "DEACTIVATION REQUESTED"  # ← ADD THIS
    UPDATE_REQUESTED = "UPDATE REQUESTED"
    UPDATING = "UPDATING"
    DELETION_REQUESTED = "DELETION REQUESTED"
    DELETING = "DELETING"
    DELETED = "DELETED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    DEPRECATED = "DEPRECATED"
```

## Step 2 — Update `accounts.py`

Now update the three request methods so they set the intermediate status instead of the final one:

**`request_account_deactivation`** — change final return from `INACTIVE` to `DEACTIVATION_REQUESTED`:

```python
# When no workspaces — still set DEACTIVATION_REQUESTED first
# deactivate_account async target will set INACTIVE when done
if len(workspaces) == 0:
    self.logger.info(
        f"No active workspaces found for owner_account_id: {owner_account_id}"
    )
    return self._update_account_with_and_return(
        account,
        status=Status.DEACTIVATION_REQUESTED  # ← was INACTIVE
    )

self.workflow_executor.async_exec_core_function(
    service="account",
    function="deactivate_account",
    kwargs={"owner_account_id": owner_account_id},
)

return self._update_account_with_and_return(
    account,
    status=Status.DEACTIVATION_REQUESTED  # ← was INACTIVE
)
```

Wait — but if no workspaces exist, the async job doesn't fire. So we need to handle that case differently. If no workspaces, we can go directly to `INACTIVE` since there's nothing async to wait for:

```python
if len(workspaces) == 0:
    # No workspaces — nothing async, go straight to INACTIVE
    return self._update_account_with_and_return(
        account,
        status=Status.INACTIVE
    )

# Has workspaces — set DEACTIVATION_REQUESTED, async job will set INACTIVE
self.workflow_executor.async_exec_core_function(
    service="account",
    function="deactivate_account",
    kwargs={"owner_account_id": owner_account_id},
)

return self._update_account_with_and_return(
    account,
    status=Status.DEACTIVATION_REQUESTED  # ← intermediate status
)
```

**`deactivate_account`** (async target) — set `INACTIVE` only when fully done:

```python
def deactivate_account(self, owner_account_id: uuid.UUID) -> AccountDetails:
    account = self.get_by_owner_id(owner_account_id)

    self.logger.info(
        f"All workspaces deactivation requested for {owner_account_id}"
    )

    workspace_details = (
        self.workspace_service.deactivate_workspaces_by_owner_account_id(
            owner_account_id
        )
    )

    self.logger.info(
        f"Deactivation completed for {owner_account_id} | {workspace_details}"
    )

    # Only set INACTIVE after ALL workspaces are done
    return self._update_account_with_and_return(
        account,
        status=Status.INACTIVE  # ← final status, set here not in request method
    )
```

Same pattern for reactivation:

**`request_account_reactivation`**:

```python
if len(workspaces) == 0:
    # No workspaces — go straight to ACTIVE
    return self._update_account_with_and_return(
        account,
        status=Status.ACTIVE
    )

self.workflow_executor.async_exec_core_function(
    service="account",
    function="reactivate_account",
    kwargs={"owner_account_id": owner_account_id},
)

return self._update_account_with_and_return(
    account,
    status=Status.ACTIVATION_REQUESTED  # ← intermediate status
)
```

**`reactivate_account`** (async target) — already sets `ACTIVE` at the end ✅

**`request_account_deletion`** — your Lead said delete should go directly, so no change needed there.

## Step 3 — Update the controller pre-flight checks

The `_assert_account_is_active` check in the controller needs to also allow `DEACTIVATION_REQUESTED` and `ACTIVATION_REQUESTED` to pass through — otherwise if a previous request is stuck mid-way, a retry will be rejected.

Actually — the checks are correct as-is. If an account is in `DEACTIVATION_REQUESTED` state, it should NOT be deactivated again. Leave the pre-flight checks unchanged.

But we need to update `_assert_account_is_inactive` to accept `ACTIVATION_REQUESTED` accounts — wait no, same logic. If it's already `ACTIVATION_REQUESTED`, don't request again.

## Step 4 — Update `shared_enums.py` DB migration

Since we're adding new enum values, check if there's a DB migration needed:

```bash
grep -rn "INACTIVE\|ACTIVE\|Status" dataviz_core/migrations/ | head -5
# or
ls dataviz_core/migrations/versions/ | tail -5
```

If the `status` column in `api_account_details` is a VARCHAR (string), no migration needed — it'll just store the new string values. If it's a DB-level ENUM type, you'll need a migration to add the new values.

Check:
```bash
grep -rn "api_account_details\|AccountDetails" dataviz_core/models/ | grep -i "status\|column" | head -10
```

Share that output and I'll tell you if a migration is needed. Then I'll generate the complete updated `accounts.py` with all these changes applied.
