OK let me think about this carefully before we touch anything.

## First — understand what deactivation SHOULD do vs what it's doing

Based on the PPT and the architecture, deactivation should **suspend** the workspace — not destroy it. The data should be preserved. When reactivated, everything should come back exactly as it was.

But right now `_deactivate_workspace` calls `request_stack_deletion` which is a **DELETE** operation — it's tearing down the Kubernetes stack entirely. That's why Grafana shows 404 — the ingress is gone.

This is a fundamental design question: **should deactivation call `request_stack_deletion` or something else?**

Let me see what kube operations are available:

```bash
grep -n "def request_stack" dataviz_core/services/kube.py
```

And:
```bash
grep -n "def request_" dataviz_core/services/kube.py | head -30
```

Share those outputs. I need to see ALL available kube operations — there might be a `request_stack_suspension` or `request_stack_pause` or similar that should be used instead of deletion.

Also check:
```bash
grep -n "def request_stack" dataviz_core/adapters/kube_client.py
```

And let me see what `_activate_workspace` does vs what it should do:
```bash
sed -n '860,910p' dataviz_core/services/workspace.py
```

Share all of those and I'll give you a proper fix. I don't want to guess at the kube layer — one wrong call could make things worse.
