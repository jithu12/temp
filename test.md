Yes — applying this fix will make `test_delete_workspace_sg_connect_none_skips_gracefully` pass.

But let me also address `test_delete[input_app4-calls4]` which is still failing. The error shows:

```
Expected: status=DELETED
Actual:   status=DELETING
```

This means the mock runs out of workspace objects after `DELETING` — the test never sees the `DELETED` call because `_poll_deletion` is being called and consuming more items from the side_effect list than expected.

Run this to see what `_poll_deletion` does:

```bash
grep -n "def _poll_deletion" dataviz_core/services/workspace.py
sed -n '<that_line>,<that_line+20>p' dataviz_core/services/workspace.py
```

Share the output. The `FAILED → DELETING` transition works but something after that is consuming extra mock items before `DELETED` can be reached.

But to answer your question directly — **yes, the `SimpleNamespace` fix solves the sg_connect test**. The `input_app4` fix needs the `_poll_deletion` output before I can give you the exact right number of workspace models to put in the side_effect list.
