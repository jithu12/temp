Yes absolutely possible! During **deactivation** and **reactivation** we only need to remove/recreate the Grafana deployment, ingress, services etc. The reset-admin pod is only needed during **initial stack creation** — not during deactivate/reactivate cycles.

## What should happen during deactivate

Remove everything EXCEPT the reset-admin pod:
- ✅ Delete ingress
- ✅ Delete deployment
- ✅ Delete HPA
- ✅ Delete service
- ✅ Delete configs
- ❌ Do NOT touch reset-admin pod

## What should happen during reactivate

Recreate everything EXCEPT the reset-admin pod:
- ✅ Create ingress
- ✅ Create deployment
- ✅ Create HPA
- ✅ Create service
- ✅ Create configs
- ❌ Do NOT create/touch reset-admin pod

## Where to make the change

This is in `_deactivate_workspace` and `_activate_workspace` in `workspace.py` — NOT in `kube.py`. So we're not touching kube code.

First let me see what `request_stack_deletion` deletes and what `_create_stack` creates. Show me:

```bash
grep -n "reset_admin\|reset-admin" dataviz_core/services/kube.py
```

And:
```bash
grep -n "reset_admin\|reset-admin" dataviz_core/services/workspace.py
```

Share those outputs. Based on what I see, the fix will be in one of two places:

**Option A — If `request_stack_deletion` deletes the reset-admin pod:**

We need to tell it to skip that pod. But this is in `kube.py` which your Lead said not to touch. So we'd need another way.

**Option B — The reset-admin pod is NOT part of `request_stack_deletion`:**

Then it's already being ignored during deactivation — and the issue is only during reactivation where `create_stack` tries to recreate it and hits 409.

Share the grep outputs and I'll tell you exactly which case you're in and what to fix in `workspace.py` only.
