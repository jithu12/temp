The guard IS in `_delete_workspace` (line 447) — that's correct. But the test is calling `_delete_workspace` which is at line 440, not `_deactivate_workspace` at line 941. Let me check what's actually at line 440:

```bash
sed -n '440,490p' dataviz_core/services/workspace.py
```

Share that output — I need to see the full `_delete_workspace` function body to confirm the guard is there.

Also run:
```bash
sed -n '760,800p' tests/services/test_workspace.py
```

To see exactly what the test looks like now after the SimpleNamespace fix was applied.
