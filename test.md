Now I can see it. `_poll_deletion` calls `_poll_status` which polls until status reaches `DELETED`. It keeps calling `get_by_id` until it gets `DELETED`. So for the FAILED scenario, the flow is:

```
delete_workspace(FAILED, is_failed=False)
  → our fix: calls _delete_workspace(workspace)
  → _delete_workspace: sets DELETING
  → back in delete_workspace: now status is DELETING
  → hits: if workspace.status is Status.DELETING: return self._poll_deletion(workspace)
  → _poll_deletion keeps calling get_by_id until DELETED
```

So the side_effect list needs **3 items**:
1. `FAILED` — first `get_by_id` call
2. `DELETING` — after `_refresh_workspace`
3. `DELETED` — `_poll_deletion` polls until it gets this

Replace index 4 in the parametrize list with:

```python
# index 4 — FAILED -> _delete_workspace sets DELETING -> _poll_deletion gets DELETED
[
    [
        workspace_model(
            id="workspace_id",
            status=Status.FAILED,
            status_history=[Status.FAILED],
        ),
        workspace_model(
            id="workspace_id",
            status=Status.DELETING,
            status_history=[Status.FAILED, Status.DELETING],
            deletion_date=RUN_NOW,
        ),
        workspace_model(
            id="workspace_id",
            status=Status.DELETED,
            status_history=[Status.FAILED, Status.DELETING, Status.DELETED],
            deletion_date=RUN_NOW,
        ),
    ],
    [
        {
            "status": Status.DELETING,
            "status_history": [Status.FAILED, Status.DELETING],
        },
    ],
],
```

Key points:
- 3 workspace models in side_effect — `FAILED`, `DELETING`, `DELETED`
- Expected calls only has `DELETING` — because `_poll_deletion` doesn't call `update`, it just polls `get_by_id` until `DELETED`
- `DELETED` is reached by polling, not by an explicit update call — so it's NOT in the expected calls list

After applying this, run pytest. Both failures should be gone.
