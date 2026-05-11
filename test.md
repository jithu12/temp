Great question — and yes, **delete should remove everything** including the database. That's the difference between deactivate and delete:

- **Deactivate** — suspend, keep all data, can come back
- **Delete** — permanent, remove everything

## What currently happens during delete

Looking at `_delete_workspace` in `workspace.py`, it calls:

```python
self.sg_connect_service.remove_redirect_url(...)
self._dataplane.request_component_deletion(component_id=workspace.dataplane_component.id)
self._dataplane.vault.delete_secret(secret_id=workspace.dataplane_component.vault_secret_id)
self._dns.request_dns_deletion(dns_id=workspace.dns_id)
self._kube_service.request_namespace_deletion(
    namespace_id=workspace.kube_stack.kube_namespace.id,
    stack_id=workspace.kube_stack.id,
)
```

It deletes:
- ✅ SG Connect redirect URL
- ✅ Dataplane component
- ✅ Vault secrets
- ✅ DNS record
- ✅ Kubernetes namespace (which removes ALL kube resources)

But I don't see the **database being explicitly deleted** here. Let me check:

```bash
grep -n "database\|delete_database\|request_database" dataviz_core/services/workspace.py | head -20
```

And:
```bash
grep -n "def request_namespace_deletion" dataviz_core/services/kube.py
sed -n '<that_line>,<that_line+40>p' dataviz_core/services/kube.py
```

Share those outputs. I need to confirm whether:
1. `request_namespace_deletion` also deletes the database
2. Or the database deletion happens via `dataplane.request_component_deletion`
3. Or the database is never deleted (which would be a bug)

This is important to verify before your Lead's delete test — if the database isn't being deleted, data would persist in the DB even after a workspace deletion, which is a storage leak.
