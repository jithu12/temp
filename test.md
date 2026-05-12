Got it! Crystal clear now.

**The rule is:**
- Deactivate → remove kube resources → **leave vault password alone** ✅
- Activate → recreate kube resources → **leave vault password alone** ✅
- Password only changes during **initial workspace creation** — never during activate/deactivate

So the fix is: during reactivation, call `create_stack` BUT skip `_reset_grafana_admin_password`.

Since we can't modify existing kube functions, we add ONE new function to `kube.py` — `reactivate_stack` — which is identical to `_create_stack` but without the password reset line.

First confirm what's available:

```bash
grep -n "def _create_" dataviz_core/services/kube.py | head -30
```

Share that and I'll write:
1. New `reactivate_stack` function in `kube.py` — copies `_create_stack` exactly, removes only `_reset_grafana_admin_password` line
2. Updated `_activate_workspace` in `workspace.py` — calls `reactivate_stack` instead of `create_stack`

This way:
- ✅ Existing `create_stack` untouched — initial workspace creation works exactly as before
- ✅ Vault password preserved during reactivation
- ✅ Reset-admin pod not touched during activate/deactivate
- ✅ Grafana comes back with same password after reactivation
- ✅ Login works
