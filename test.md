Yes! We can handle this entirely in `workspace.py` in the `_activate_workspace` function we already wrote.

The fix is simple — instead of calling `create_stack` which recreates EVERYTHING including the reset-admin pod, we call a more targeted function that only recreates the Grafana deployment/ingress/services.

But first let me check what's available:

```bash
grep -n "def request_stack\|def create_stack\|def _create_stack\|def request_namespace" dataviz_core/services/kube.py | head -20
```

And:
```bash
grep -n "reset_admin\|reset-admin" dataviz_core/services/kube.py | head -10
```

Share those outputs. Based on what's available I'll tell you the exact change in `workspace.py` only.

But thinking about it logically — if `create_stack` internally calls `_reset_grafana_admin_password` which creates the reset-admin pod, we have two options in `workspace.py`:

**Option A — Skip `create_stack` entirely and call individual kube functions:**

Not possible without touching kube.py.

**Option B — Add a flag to skip reset-admin:**

Not possible without touching kube.py.

**Option C — Delete the reset-admin pod BEFORE calling `create_stack`:**

This is in `workspace.py` — no kube.py touch needed. When reactivating, delete the old reset-admin pod first so `create_stack` can recreate it cleanly without 409.

But you said your Lead doesn't want to touch that pod at all during activate/deactivate...

So the real question is: **does `request_stack_deletion` (called during deactivate) delete the reset-admin pod or not?**

```bash
grep -n "reset_admin\|reset-admin" dataviz_core/services/kube.py
```

Share that one output — it'll tell us everything. If the reset-admin pod is NOT being deleted during deactivation, then during reactivation `create_stack` tries to create it again → 409. That's the root cause and we need to know if it's being deleted or not before deciding the fix.
