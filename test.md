This is very helpful. I can see two important things:

**`_refresh_stack`** returns early without checking Kubernetes if status is `CREATION_REQUESTED` or `DELETED` — which means after we reset to `CREATION_REQUESTED`, `create_stack` will call `_refresh_stack` which returns the stack as-is, then proceeds to `_create_stack`. ✅

**`create_stack`** raises `KubeStackCreationError` at the end if status is anything other than `CREATION_REQUESTED`, `CREATING`, `RETRYING`, or `ACTIVE`. So our fix of resetting to `CREATION_REQUESTED` is exactly right.

Now check `_delete_deployment` to confirm data is preserved:

```bash
grep -n "def _delete_deployment" dataviz_core/services/kube.py
```

Then share the function — this is critical to confirm before your Lead's data test.

Also verify `_update_stack_with` signature matches how we're calling it:

```bash
sed -n '703,720p' dataviz_core/services/kube.py
```

Share both and I'll confirm the fix is safe to apply and give you the final code.
