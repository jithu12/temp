Simple — two changes in Core.

## Change 1 — `dataviz_core/services/accounts.py`

Add `workflow_executor` to `__init__`:

```python
# Add this import at the top if not already there
from dataviz_core.adapters.celery_workflow_executor import WorkflowExecutor

class AccountService(SessionManagerMixin):
    def __init__(
        self,
        account_client: AccountClient,
        session_provider: SessionProvider,
        repository_context: Optional[RepositoryContext] = None,
        is_retrying_func: Callable[[], bool] = lambda: False,
        logger: Optional[LoggerType] = None,
        workflow_executor: WorkflowExecutor = None,  # ← ADD THIS LINE
    ) -> None:
        super().__init__(
            session_provider,
            repository_context=repository_context,
        )
        self.is_retrying_func = is_retrying_func
        self.account_client = account_client
        self.logger = logger if logger else get_default_logger(__name__)
        self.workspace_service = None
        self.workflow_executor = workflow_executor  # ← ADD THIS LINE
```

## Change 2 — `dataviz_core/core.py`

Find where `AccountService` is instantiated. It will look something like this:

```python
self.account = AccountService(
    account_client=...,
    session_provider=...,
    repository_context=...,
)
```

Add `workflow_executor`:

```python
self.account = AccountService(
    account_client=...,
    session_provider=...,
    repository_context=...,
    workflow_executor=self.workflow_executor,  # ← ADD THIS LINE
)
```

## How to find the exact spot in `core.py`

Run:
```bash
grep -n "AccountService(" dataviz_core/core.py
```

That'll give you the line number. Open that line and just add `workflow_executor=self.workflow_executor,` to the constructor call.

## How to verify the import path for `WorkflowExecutor`

From your grep output earlier I saw:
```
dataviz_core/core.py:3: from dataviz_core.adapters.celery_workflow_executor import (
dataviz_core/core.py:4:     celery_workflow_executor_from_config,
```

So the import in `accounts.py` should be:
```python
from dataviz_core.adapters.celery_workflow_executor import WorkflowExecutor
```

But to be safe, run:
```bash
grep -n "class WorkflowExecutor\|WorkflowExecutor" dataviz_core/adapters/celery_workflow_executor.py | head -5
```

That confirms the exact class name and import path.

## After making both changes

1. Clear pycache:
```bash
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
```

2. Restart server.

3. Test with a **real `owner_account_id`** that HAS active workspaces.

4. Check the DB after the call:
```sql
SELECT id, status, owner_account_id 
FROM public.api_workspaces 
WHERE owner_account_id = 'your-real-uuid-here';
```

All workspaces should show `INACTIVE`. And the account in `api_account_details` should also show `INACTIVE`.

That's the complete fix. Two lines in Core and the full cascade works end to end.
