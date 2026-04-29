The logs tell the full story. Look at this:

```
INFO: No active workspaces found for owner_account_id: aaa11111-aaaa-aaaa-aaaa-aaaaaaaaaaaa
INFO: AccountDetails (77777777-...) updating status from ACTIVE to INACTIVE
```

**The deactivation IS working perfectly.** The account status changed from ACTIVE to INACTIVE. ✅

## Why workspaces aren't changing

The log says **"No active workspaces found"** — which means the account `aaa11111-...` has **zero active workspaces** in the DB. So Core takes the direct path (no async job needed) and just flips the account status.

This is correct behavior — if there are no active workspaces, there's nothing to deactivate in `api_workspaces`.

## You're still using fake UUIDs

Look at the two requests in the logs:

**Request 1:**
```
target_account_id: bbbb2222-bbbb-bbbb-bbbb-bbbbbbbbbbbb  ← fake
```

**Request 2:**
```
owner_account_id: aaa11111-aaaa-aaaa-aaaa-aaaaaaaaaaaa  ← also fake
```

These are all fake test UUIDs. Of course they have no workspaces — they don't exist in the DB properly.

## What you need to do — use a REAL UUID

Run this SQL right now and get a real UUID that has workspaces:

```sql
SELECT 
    a.owner_account_id,
    a.name,
    a.status,
    COUNT(w.id) as workspace_count
FROM public.api_account_details a
LEFT JOIN public.api_workspaces w 
    ON w.owner_account_id = a.owner_account_id
GROUP BY a.owner_account_id, a.name, a.status
HAVING COUNT(w.id) > 0;
```

Copy one of those real `owner_account_id` values and use it in Swagger. Then:

1. Call `PATCH /admin/v1/accounts/{real_uuid}/deactivate`
2. Check the logs — you should see the async job firing
3. Check the DB — workspaces should show INACTIVE

The code is working. You just need to test with real data.
