Sure! Here's a clean summary:

## Deactivate

1. **API receives request** — admin calls `PATCH /admin/v1/accounts/{owner_account_id}/deactivate`
2. **Admin gate check** — caller must be in `ALLOWED_ACCOUNT_IDS`
3. **Account status check** — account must be `ACTIVE`
4. **Async job fires** — Celery worker picks up `deactivate_account`
5. **Account status** → `INACTIVE` in `api_account_details`
6. **For each workspace** under that account:
   - Calls `_deactivate_workspace`
   - Deletes Kubernetes resources — ingress, deployment, HPA, service, secrets, config
   - Workspace status → `INACTIVE` in `api_workspace`
7. **Result** — Grafana shows 404, all data preserved in database

## Activate

1. **API receives request** — admin calls `PATCH /admin/v1/accounts/{owner_account_id}/activate`
2. **Admin gate check** — caller must be in `ALLOWED_ACCOUNT_IDS`
3. **Account status check** — account must be `INACTIVE`
4. **Async job fires** — Celery worker picks up `reactivate_account`
5. **Account status** → `ACTIVE` in `api_account_details`
6. **For each workspace** under that account:
   - Calls `_activate_workspace` directly (bypasses `_refresh_workspace` to avoid stale Kubernetes status)
   - Resets existing KubeStack status → `CREATION_REQUESTED`
   - Fires async `create_stack` job with same stack ID
   - Kubernetes recreates — ingress, deployment, HPA, service, secrets, config
   - Workspace status → `ACTIVE` in `api_workspace`
7. **Result** — Grafana accessible again, all data intact ✅

## Key points about data safety

- **Database never touched** during either operation — all Grafana dashboards, users, data sources preserved
- **Same KubeStack record reused** during activation — same database connection, same namespace
- **Only Kubernetes resources** are removed/recreated — stateless components that can be safely rebuilt
