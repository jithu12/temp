Good — `_update_stack_with` signature matches perfectly. Now check `_delete_deployment`:

```bash
sed -n '1418,1450p' dataviz_core/services/kube.py
```

This is the critical check — if `_delete_deployment` deletes persistent volumes, your Lead's data test will fail regardless of our fix. Share that output and I'll give you the final confirmed fix.
