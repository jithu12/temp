This is the Grafana admin password reset issue — the reset-admin pod that’s failing with 409. Even though we said we’d ignore it, this is actually the root cause of why you can’t login.
Here’s what’s happening:
The flow for Grafana login

Stack creation
    → creates deployment (Grafana pod)
    → creates reset-admin pod
    → reset-admin pod runs: grafana-cli admin reset-admin-password <password-from-vault>
    → pod exits
    → you login with that vault password


Why login is failing
The reset-admin pod is failing with 409 on retries — meaning the password reset never actually completes successfully. So Grafana has either:
	•	The old default password (admin/admin)
	•	The previous vault password from before deactivation/reactivation
Quick things to try right now
Try 1 — Default Grafana credentials:

username: admin
password: admin


Try 2 — Check what password is in vault for this workspace:

grep -rn "reset_admin\|reset-admin\|grafana.*password\|vault.*grafana" dataviz_core/services/kube.py | head -10


Try 3 — Check if the reset-admin pod actually completed:
Ask Sakshi to run:

# Check reset-admin pod status
kubectl get pods -n ns-daz-471936-dev-dv-b2b94 | grep reset-admin

# Check pod logs to see if password reset succeeded
kubectl logs <reset-admin-pod-name> -n ns-daz-471936-dev-dv-b2b94


The real fix
Since you don’t want to touch kube code, the simplest fix is to manually trigger the password reset. Find what password is stored in vault for this workspace:

grep -rn "def _reset_grafana_admin_password\|reset_admin_password\|grafana_secret" dataviz_core/services/kube.py | head -10


Share that and I’ll tell you exactly where to find the password and how to manually reset it so you can login today without touching any code.​​​​​​​​​​​​​​​​
