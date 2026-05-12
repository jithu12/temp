OK I hear you — Lead says absolutely do not touch the reset-admin pod during activate/deactivate.

So the root cause is: `create_stack` calls `_reset_grafana_admin_password` which generates a new password. We need to stop `create_stack` from doing that during reactivation.

Since we can't touch kube.py, the only option is to **not call `create_stack` at all for reactivation**.

Instead we need a different kube function that recreates only the Grafana resources WITHOUT the password reset.

Check what's available:

```bash
grep -n "def request_\|def _create_\|def _delete_" dataviz_core/services/kube.py | grep -v "secret\|hpa\|ingress\|config\|service\|deployment\|namespace\|metric\|fluentd\|ldap\|nginx\|tls" | head -20
```

And check if there's any existing function for partial stack recreation:

```bash
grep -n "def.*stack\|def.*activate\|def.*deactivate\|def.*suspend\|def.*resume" dataviz_core/services/kube.py | head -20
```

Share those outputs. If there's no suitable function, then I need to tell you honestly — **there is no way to fix this without either:**

1. Touching kube.py to add a reactivation-specific function, OR
2. Deleting the reset-admin pod before reactivation

There's no third option. The password sync issue is caused by `create_stack` generating a new password but the pod failing to run. Without touching kube.py or the reset-admin pod, we can't fix it.

**This needs to be raised to your Lead directly:**

> "During reactivation, `create_stack` calls `_reset_grafana_admin_password` which generates a new vault password but the reset-admin pod already exists from original creation → 409 → vault and Grafana out of sync → login fails. To fix this without touching the reset-admin pod, we need either a new kube function that skips password reset during reactivation, or we accept that reactivation will always reset the Grafana admin password. Which approach do you prefer?"

That's the honest answer. What does your Lead say?
