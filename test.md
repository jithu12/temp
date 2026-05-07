Now I have the full picture. Let me explain clearly what's happening and what the fix is.

## What's actually happening

**Deactivation calls `request_stack_deletion` which:**
- Deletes HPA, Ingress, Config, Service, Secrets, Deployment
- The **database is NOT deleted** — only Kubernetes resources
- So Grafana data (stored in the database) is preserved ✅
- But the ingress/deployment/secrets are gone → 404 error ✅

**Reactivation calls `request_stack_creation` which:**
- Creates a **brand new** KubeStack DB record
- Fires async job to create new Kubernetes resources
- But `workspace.kube_stack` still points to the **old deleted stack**
- The new stack has no connection to the workspace

## The core problem — `_activate_workspace` is wrong

```python
def _activate_workspace(self, workspace: Workspace) -> Workspace:
    self._kube_service.request_stack_creation(
        ws_name=workspace.name,
        name=workspace.kube_stack.name,        # ← old deleted stack name
        dns_id=workspace.dns.id,
        database_id=workspace.database.id,
        kube_namespace_id=workspace.kube_stack.kube_namespace.id,
    )
```

This creates a NEW kube stack using the old stack's name and namespace. But the new stack record gets inserted into DB with a new ID — and the workspace's `kube_stack_id` still points to the old deleted stack. So nothing reconnects.

Also `request_stack_creation` fires an async job `create_stack` which checks:
```python
if kube_stack.status in [Status.CREATION_REQUESTED, Status.CREATING, Status.RETRYING]:
    return self._create_stack(kube_stack, temp_ns_check)
```

But the old stack has status `DELETED` — so `create_stack` does nothing.

## The fix

Instead of creating a new stack, **recreate using the existing kube stack record** — update its status back to `CREATION_REQUESTED` and fire the async job with its existing ID.

Find the existing `_activate_workspace` in `workspace.py` and replace it:

```python
def _activate_workspace(self, workspace: Workspace) -> Workspace:
    self.logger.info(f"Reactivating {logname(workspace)}...")
    try:
        # Get the existing kube stack — don't create a new one
        kube_stack = workspace.kube_stack

        if kube_stack is None:
            self.logger.error(
                f"{logname(workspace)}: No kube stack found — cannot reactivate"
            )
            raise WorkspaceActivationFailedError(workspace.id)

        # Reset the existing stack status back to CREATION_REQUESTED
        # so create_stack async job will process it
        self._kube_service._update_stack_with(
            kube_stack,
            status=Status.CREATION_REQUESTED,
        )

        # Fire async job to recreate Kubernetes resources
        # using the EXISTING stack id — not a new one
        self._kube_service.workflow_executor.async_exec_core_function(
            service="kube",
            function="create_stack",
            kwargs={
                "stack_id": kube_stack.id,
                "temp_ns_check": False,
            },
        )

    except WorkspaceActivationFailedError:
        raise
    except Exception as e:
        self.logger.exception(
            f"{logname(workspace)}: '{workspace.name}' kube reactivation failed. "
            f"Error: {e}"
        )

    return self._update_workspace_with_and_return(
        workspace,
        status=Status.ACTIVE,
    )
```

## Why this works

- Uses the **existing** kube stack record (same ID, same namespace, same database connection)
- Resets its status to `CREATION_REQUESTED`
- `create_stack` async job sees `CREATION_REQUESTED` and calls `_create_stack`
- `_create_stack` recreates HPA, Ingress, Config, Service, Secrets, Deployment
- Because the **same database** is used, all Grafana data is preserved
- The ingress comes back → Grafana login works again ✅

## Before applying — verify `_update_stack_with` exists

```bash
grep -n "def _update_stack_with\b" dataviz_core/services/kube.py
```

And verify `create_stack` handles `CREATION_REQUESTED`:
```bash
sed -n '192,220p' dataviz_core/services/kube.py
```

Share those outputs. If `_update_stack_with` exists and `create_stack` handles `CREATION_REQUESTED` (which we already saw it does), the fix above is correct and safe to apply.

Also — one important question: **does `_delete_deployment` in `request_stack_deletion` also delete the Grafana database/persistent volume?** Run:

```bash
grep -n "def _delete_deployment" dataviz_core/services/kube.py
sed -n '<that_line>,<that_line+30>p' dataviz_core/services/kube.py
```

If the deployment deletion also removes persistent volumes, the data won't be there after reactivation regardless of our fix. This is critical to confirm before your Lead's test.
