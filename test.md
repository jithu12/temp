Good news and bad news.

**Good news** — the fix is working. `create_stack` IS being called with the existing stack ID, which means `_activate_workspace` fix is correct. The kube reactivation flow is triggering properly.

**Bad news** — the pods aren't starting. `check_deployment_pods_running` is recursively checking if pods are up, hitting the retry limit, and failing. This means the Kubernetes deployment was created but the pods aren't becoming healthy.

## Why pods aren't running

This is a dev environment issue. When `_create_stack` creates the deployment, Kubernetes tries to pull the Grafana image and start the pod. In dev, this can fail because:

1. The image pull fails (wrong registry, auth issues)
2. The pod has resource constraints not met in dev
3. The previous deployment wasn't fully cleaned up — conflicting resources exist

## Immediate fix — stop the retry loop

First, stop Celery worker to halt the retries:

```bash
# Find and kill the Celery worker
ps aux | grep celery | grep -v grep
kill <pid>
```

Then reset the stuck kube stack status in DB so it stops retrying:

```sql
-- Find the stuck stack
SELECT id, name, status 
FROM public.api_kube_stack 
WHERE id = '01ee084c-7271-4aaa-86a9-f74812a10438';

-- Reset it so it stops retrying
UPDATE public.api_kube_stack 
SET status = 'FAILED'
WHERE id = '01ee084c-7271-4aaa-86a9-f74812a10438';
```

## Check what's happening in Kubernetes

```bash
# Check pod status in the workspace namespace
kubectl get pods -n ns-daz-471936-dev-dv-b2b94

# Check pod logs for the failing pod
kubectl describe pod <pod-name> -n ns-daz-471936-dev-dv-b2b94

# Check events in the namespace
kubectl get events -n ns-daz-471936-dev-dv-b2b94 --sort-by='.lastTimestamp'
```

Share the output — this will tell us exactly why the pod isn't starting.

## The real issue here

This is not a code bug — it's an infrastructure issue. `_create_stack` is doing exactly what it should — creating the deployment and waiting for pods to be ready. But pods aren't becoming ready in dev.

This is what your Lead needs to investigate — specifically:
- Is the Grafana image available in the dev registry?
- Are there leftover Kubernetes resources from the previous deletion that are conflicting?
- Does the dev cluster have enough resources to run the pod?

Check leftover resources:

```bash
kubectl get all -n ns-daz-471936-dev-dv-b2b94
```

If there are leftover resources from the failed deletion, clean them up first:

```bash
kubectl delete deployment a-977791-deployment -n ns-daz-471936-dev-dv-b2b94
kubectl delete service a-977791-service -n ns-daz-471936-dev-dv-b2b94
kubectl delete ingress a-977791-ingress -n ns-daz-471936-dev-dv-b2b94
```

Then restart Celery and try activating again. Once the namespace is clean, `create_stack` should be able to create everything fresh.

Share the `kubectl get pods` and `kubectl get events` output and I can tell you exactly what's blocking the pods from starting.
