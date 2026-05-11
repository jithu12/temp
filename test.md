I understand completely. Right now the flow is:

```
Admin clicks deactivate → DB immediately shows INACTIVE → workspaces deactivated in background
```

Your Lead wants:
```
Admin clicks deactivate → DB shows DEACTIVATION_REQUESTED → workspaces deactivating in background → all done → DB shows INACTIVE
Admin clicks activate → DB shows ACTIVATION_REQUESTED → workspaces activating in background → all done → DB shows ACTIVE
```

So the status endpoint would show intermediate states like `DEACTIVATION_REQUESTED` or `ACTIVATION_REQUESTED` while the async job is running, then flip to `INACTIVE` or `ACTIVE` when complete.

## Good news — Core already supports this pattern

Looking at `request_account_deactivation` — it already fires an async job. The issue is we're calling `_update_account_with_and_return(account, status=Status.INACTIVE)` **before** the async job completes. We need to change that to `Status.DEACTIVATION_REQUESTED` (or similar) and only set `INACTIVE` after the async job finishes in `deactivate_account`.

## First — check what status values exist in Core

```bash
grep -rn "DEACTIVATION_REQUESTED\|ACTIVATION_REQUESTED\|DELETING\|DELETION_REQUESTED" dataviz_core/models/shared_enums.py
```

And:
```bash
cat dataviz_core/models/shared_enums.py
```

Share the output. I need to see what Status enum values are already defined — your Lead may have already added `DEACTIVATION_REQUESTED` and `ACTIVATION_REQUESTED`. Once I know what's available I'll give you the exact changes needed in `accounts.py`.
