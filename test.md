Perfect — now I have everything I need. Here's the plan:

Add ONE new function `reactivate_stack` to `kube.py` — identical to `_create_stack` but without `_reset_grafana_admin_password`. Then update `_activate_workspace` in `workspace.py` to call it.

## Change 1 — Add to `kube.py`

Find `_create_stack` in `kube.py` and add this new function RIGHT AFTER it:

```python
def reactivate_stack(
    self,
    stack_id: uuid.UUID,
    temp_ns_check: bool = False,
) -> KubeStack:
    """
    Recreates all Kubernetes resources for an existing stack
    WITHOUT resetting the Grafana admin password.

    Used during account reactivation to preserve the vault password
    and avoid the reset-admin pod 409 conflict on retries.

    Identical to _create_stack except _reset_grafana_admin_password
    is deliberately skipped.
    """
    kube_stack = self.repositories.kube_stack.get_by_id(stack_id)
    kube_stack = self._refresh_stack(kube_stack)

    if kube_stack.status not in [
        Status.CREATION_REQUESTED,
        Status.CREATING,
        Status.RETRYING,
    ]:
        if kube_stack.status == Status.ACTIVE:
            self.logger.info(
                f"{logname(kube_stack)}: Kube stack already active!"
            )
            return kube_stack
        self.logger.info(
            f"{logname(kube_stack)}: Kube Stack has status "
            f"'{kube_stack.status}', can't reactivate!"
        )
        raise KubeStackCreationError(kube_stack)

    self._poll_res_created(
        self.repositories.kube_namespace, kube_stack.kube_namespace_id
    )
    kube_stack = self._update_stack_with_and_return(
        kube_stack, status=Status.CREATING
    )
    self.logger.info(f"{logname(kube_stack)}: Start reactivation")

    try:
        self._wait_for_dependencies(kube_stack, temp_ns_check)
        self._poll_res_created(
            self.repositories.sg_connect,
            kube_stack.workspace.sg_connect_id
        )
        self._create_tls_secret(kube_stack)
        self._create_nginx_conf(kube_stack, temp_ns_check)
        self._create_grafana_secret(kube_stack)
        self._create_ldap_conf(kube_stack)
        self._create_fluent_config(kube_stack)
        self._create_metrology_log_secret(kube_stack)
        self._create_metric_fluentd_conf(kube_stack)
        self._create_metric_secret(kube_stack)
        self._create_metric_cert_secret(kube_stack)
        deployment = self._create_deployment(
            kube_stack,
            grafana_image=kube_stack.workspace.grafana_image
        )
        self._create_service(kube_stack)
        self._create_ingress(kube_stack)
        self._create_hpa(kube_stack)
        self._create_metric_deployment(kube_stack)
        self._check_deployment_pods_running(deployment, kube_stack)

        # NOTE: _reset_grafana_admin_password deliberately skipped
        # Vault password is preserved from original stack creation
        # Reset-admin pod is not touched during activate/deactivate

        self.sg_connect_service.update_redirect_url(
            kube_stack.workspace.sg_connect,
            kube_stack.dns.fqdn
        )
        import time
        time.sleep(RETRY_SLEEP)

    except Exception as e:
        self.logger.exception(f"{logname(kube_stack)}: Reactivation failed")
        self.logger.info(
            f"{logname(kube_stack)} Celery retry process will start soon..."
        )
        kube_stack = self._update_stack_with_and_return(
            kube_stack,
            status=self.failed_or_retrying_status
        )
        deployments = self._get_deployments(stack=kube_stack)
        workspace_count = len(deployments)
        self._update_namespace_with(
            kube_stack.kube_namespace,
            workspace_count=workspace_count
        )
        raise KubeStackCreationError(kube_stack.id) from e

    else:
        deployments = self._get_deployments(stack=kube_stack)
        workspace_count = len(deployments)
        self._update_namespace_with(
            kube_stack.kube_namespace,
            workspace_count=workspace_count
        )
        return self._update_stack_with_and_return(
            kube_stack,
            status=Status.ACTIVE
        )
```

## Change 2 — Update `_activate_workspace` in `workspace.py`

```python
def _activate_workspace(self, workspace: Workspace) -> Workspace:
    self.logger.info(f"Reactivating {logname(workspace)}...")
    try:
        kube_stack = workspace.kube_stack

        if kube_stack is None:
            self.logger.error(
                f"{logname(workspace)}: No kube stack found — cannot reactivate"
            )
            raise WorkspaceActivationFailedError(workspace.id)

        # Reset the existing stack back to CREATION_REQUESTED
        # so the reactivate_stack async job will process it
        self._kube_service._update_stack_with(
            kube_stack,
            status=Status.CREATION_REQUESTED,
        )

        # Fire reactivate_stack instead of create_stack
        # reactivate_stack recreates all Kubernetes resources
        # WITHOUT resetting the Grafana admin password —
        # vault password is preserved, reset-admin pod not touched
        self._kube_service.workflow_executor.async_exec_core_function(
            service="kube",
            function="reactivate_stack",
            kwargs={
                "stack_id": kube_stack.id,
                "temp_ns_check": False,
            },
        )

        self.logger.info(
            f"{logname(workspace)}: Kube stack reactivation requested "
            f"for stack_id={kube_stack.id}"
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

## Summary of what changed

| File | Change |
|---|---|
| `kube.py` | Added new `reactivate_stack` function — copy of `_create_stack` with `_reset_grafana_admin_password` removed |
| `workspace.py` | `_activate_workspace` now calls `reactivate_stack` instead of `create_stack` |
| Everything else | Untouched ✅ |

## Why this is correct

```
Deactivate:
  → request_stack_deletion removes ingress/deployment/service/HPA
  → reset-admin pod stays alive ✅
  → vault password unchanged ✅

Reactivate:
  → reactivate_stack recreates ingress/deployment/service/HPA
  → _reset_grafana_admin_password SKIPPED ✅
  → vault password unchanged ✅
  → reset-admin pod not touched ✅
  → Grafana comes back with same vault password ✅
  → LOGIN WORKS ✅

Initial workspace creation:
  → create_stack runs as before ✅
  → _reset_grafana_admin_password runs ✅
  → reset-admin pod created ✅
  → vault password set for first time ✅
```
