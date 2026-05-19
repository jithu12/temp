Here's exactly what to do for each file — super simple:

## 1. CREATE new file
```
dataviz_core/services/garbage_collector.py
```
Copy the full content from `garbage_collector.py` download.

## 2. EDIT existing file
```
dataviz_core/models/account_details.py
```
Just add this one line after `soft_limit`:
```python
grace_period_days = Column(
    Integer,
    nullable=False,
    default=30,
)
```

## 3. CREATE new migration file
```
dataviz_core/migrations/versions/add_grace_period_days.py
```
Copy content from `migration_grace_period.py` download. Then run:
```bash
alembic upgrade head
```

## 4. EDIT existing file
```
dataviz_async/app.py
```
Two things to add:

**Add this task** after `check_certificate_expiration` task:
```python
@app.task(name="run_garbage_collector")
def run_garbage_collector() -> None:
    logger.info("GarbageCollector: BEGIN")
    get_core(app).garbage_collector.run()
    logger.info("GarbageCollector: DONE")
```

**Add this to `beat_schedule`:**
```python
"run_garbage_collector_every_hour": {
    "task": "run_garbage_collector",
    "schedule": crontab(minute=0),
},
```

## 5. EDIT existing file
```
dataviz_core/services/accounts.py
```
Three small changes:

**In `__init__`** add:
```python
self.garbage_collector_service = None
```

**Add new method:**
```python
def set_garbage_collector_service(self, garbage_collector_service):
    self.garbage_collector_service = garbage_collector_service
```

**In `request_account_deactivation`** after `_update_account_with_and_return` and before firing async job add:
```python
if self.garbage_collector_service is not None:
    grace_period_days = getattr(account, "grace_period_days", 30)
    self.garbage_collector_service.set_deletion_date_for_account(
        account=account,
        grace_period_days=grace_period_days,
    )
```

## 6. EDIT existing file
```
dataviz_core/core.py
```
Add import at top:
```python
from dataviz_core.services.garbage_collector import GarbageCollectorService
```

Add after `self.account` is set up:
```python
self.garbage_collector = GarbageCollectorService(
    session_provider=self.session_provider,
    repository_context=self.repository_context,
)
self.garbage_collector.set_workspace_service(self.workspace)
self.account.set_garbage_collector_service(self.garbage_collector)
```

## Summary

| Action | File |
|---|---|
| CREATE | `dataviz_core/services/garbage_collector.py` |
| EDIT | `dataviz_core/models/account_details.py` |
| CREATE | `dataviz_core/migrations/versions/add_grace_period_days.py` |
| EDIT | `dataviz_async/app.py` |
| EDIT | `dataviz_core/services/accounts.py` |
| EDIT | `dataviz_core/core.py` |

That's all 6 changes — 2 new files, 4 small edits. Want me to clarify any specific one?
