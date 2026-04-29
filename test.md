Sure. Here's exactly what to do step by step.

## Step 1 — Find the right line in `core.py`

Run this:
```bash
grep -n "set_workspace_service" dataviz_core/core.py
```

It will show something like:
```
284:    self.account.set_workspace_service(self.workspace)
```

Note that line number.

## Step 2 — Open `core.py` and go to that line

```bash
nano dataviz_core/core.py
```
Or open it in VS Code.

## Step 3 — Find this line

```python
self.account.set_workspace_service(self.workspace)
```

## Step 4 — Add ONE line directly below it

```python
self.account.set_workspace_service(self.workspace)
self.account.workflow_executor = self.workflow_executor  # ← add this
```

That's it. Just one new line right after the existing `set_workspace_service` line.

## Step 5 — Save and restart

```bash
# Clear pycache
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null

# Restart server
python app.py
```

## Step 6 — Test with a real UUID that HAS workspaces

Use the real `owner_account_id` from your DB (not `bbbb2222-...`).

You should get **202** back, and then check the DB:
```sql
SELECT id, status 
FROM public.api_workspaces 
WHERE owner_account_id = 'your-real-uuid';
```

All workspaces should now show `INACTIVE`.

---

The reason this works: `set_workspace_service` is called after everything is initialized, so `self.workflow_executor` already has its real value at that point. By adding the line right below it, we guarantee `workflow_executor` is also properly set on the account service before any request comes in.
