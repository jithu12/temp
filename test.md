OK this is very clear now. There are only two kube operations available:

- `request_stack_creation` — creates/recreates a stack
- `request_stack_deletion` — deletes a stack permanently

**There is no suspend/pause operation.** This means the architecture uses creation and deletion for activate/deactivate. The data preservation question depends on whether the database and storage volumes are preserved when the stack is deleted.

Before touching anything, I need to understand the full picture. Run these:

```bash
sed -n '1299,1350p' dataviz_core/services/kube.py
```

```bash
sed -n '129,200p' dataviz_core/services/kube.py
```

```bash
grep -n "def _activate_workspace\|def _deactivate_workspace" dataviz_core/services/workspace.py
```

Then share the `_activate_workspace` function:
```bash
grep -n "def _activate_workspace" dataviz_core/services/workspace.py
```

Then:
```bash
sed -n '<that_line>,<that_line+40>p' dataviz_core/services/workspace.py
```

Share all of those. I need to see:
1. What `request_stack_deletion` actually does — does it delete the DB/volumes or just the ingress/pods?
2. What `request_stack_creation` does — does it recreate from scratch or restore existing data?
3. What `_activate_workspace` passes to `request_stack_creation`

This will tell us whether data can be preserved across deactivate/reactivate cycles, and what the correct fix is. Don't want to guess on this one — if we pass wrong params to kube creation we could make things worse.
