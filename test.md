Now I have everything I need. Let me analyze what we have and what we need to build.

## What I can see

**`AccountDetails` model** — no grace period column exists yet. Has:
- `name`, `owner_account_id`, `soft_limit`, `status` (from `ResourceModel`)
- `updated_at` (from `ResourceModel` — this is what Lead said to use)

**`app.py` in Async** — already has Celery beat with 3 scheduled tasks. We just add ours the same way.

**No grace period storage** — we need to add it somewhere.

## What we can build today

Since grace period is **per account/team** and not global, we need to store it. Since Lead said use `updated_at` column, the approach is:

```
Account deactivated → updated_at = NOW (auto updated by ResourceModel)
Celery beat runs hourly → checks: NOW - updated_at > grace_period_days
                        → if yes → set DELETING
24 hours later → set DELETED + cascade workspaces
```

But where is the grace period days stored per account? Since `AccountDetails` has `soft_limit` (per account config), we can add `grace_period_days` the same way.

Let me check `ResourceModel` first:

```bash
grep -rn "updated_at\|created_at\|class ResourceModel" dataviz_core/models/sqlalchemy.py | head -10
```

And check if there's a migration system:

```bash
ls dataviz_core/migrations/versions/ | tail -5
```

Share those and while you do that, here's what we'll build today:

## Plan for today

### Step 1 — Add `grace_period_days` to `AccountDetails` model

```python
class AccountDetails(ResourceModel, Model):
    name = Column(String(1024), nullable=True)
    owner_account_id = Column(GUID, nullable=False)
    soft_limit = Column(Integer, nullable=False, default=5)
    grace_period_days = Column(
        Integer,
        nullable=False,
        default=30  # default 30 days if not set
    )
```

### Step 2 — Add garbage collector service in Core

New file: `dataviz_core/services/garbage_collector.py`

```python
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import List

from dataviz_core.models.shared_enums import Status
from dataviz_core.models.account_details import AccountDetails
from dataviz_core.services.filtering import FilteringCriterion
from dataviz_core.services.session import SessionManagerMixin
from dataviz_core.utils.logging import get_default_logger

logger = get_default_logger(__name__)

# How long an account stays in DELETING before being marked DELETED
DELETING_TO_DELETED_HOURS = 24


class GarbageCollectorService(SessionManagerMixin):
    """
    Automated account lifecycle management.

    Runs periodically via Celery beat to:
    1. Find INACTIVE accounts whose grace period has expired
       → move them to DELETING + fire ResourceDeleting event
    2. Find DELETING accounts that have been deleting for 24+ hours
       → move them to DELETED + cascade workspace deletion
    """

    def __init__(self, session_provider, repository_context=None, logger=None):
        super().__init__(session_provider, repository_context=repository_context)
        self.logger = logger if logger else get_default_logger(__name__)
        self.workspace_service = None
        self.account_service = None

    def set_workspace_service(self, workspace_service):
        self.workspace_service = workspace_service

    def set_account_service(self, account_service):
        self.account_service = account_service

    def run(self) -> dict:
        """
        Main entry point called by Celery beat.
        Returns summary of what was processed.
        """
        self.logger.info("GarbageCollector: Starting run")

        results = {
            "grace_period_expired": self._process_grace_period_expired(),
            "deleting_completed": self._process_deleting_completed(),
        }

        self.logger.info(f"GarbageCollector: Run complete — {results}")
        return results

    def _process_grace_period_expired(self) -> List[str]:
        """
        Find INACTIVE accounts whose grace period has expired
        and move them to DELETING.

        Grace period = grace_period_days column on AccountDetails
        Timer starts from updated_at (when account was deactivated)
        """
        self.logger.info(
            "GarbageCollector: Checking for expired grace periods"
        )

        # Get all INACTIVE accounts
        filters = [FilteringCriterion("status", Status.INACTIVE)]
        inactive_accounts = self.repositories.account_details.list(
            filters=filters
        )

        now = datetime.now(timezone.utc)
        processed = []

        for account in inactive_accounts:
            try:
                # updated_at is when the account was last modified
                # i.e. when it was deactivated
                updated_at = account.updated_at
                if updated_at is None:
                    continue

                # Make timezone aware if needed
                if updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=timezone.utc)

                grace_period_days = getattr(account, "grace_period_days", 30)
                grace_period_ends = updated_at + timedelta(days=grace_period_days)

                if now >= grace_period_ends:
                    self.logger.info(
                        f"GarbageCollector: Account {account.owner_account_id} "
                        f"grace period expired on {grace_period_ends}. "
                        f"Moving to DELETING."
                    )
                    self._move_to_deleting(account)
                    processed.append(str(account.owner_account_id))
                else:
                    remaining = grace_period_ends - now
                    self.logger.info(
                        f"GarbageCollector: Account {account.owner_account_id} "
                        f"grace period ends in {remaining.days} days."
                    )

            except Exception as e:
                self.logger.error(
                    f"GarbageCollector: Error processing account "
                    f"{account.owner_account_id}: {e}"
                )
                continue

        return processed

    def _process_deleting_completed(self) -> List[str]:
        """
        Find accounts in DELETING state for 24+ hours
        and move them to DELETED + cascade workspace deletion.
        """
        self.logger.info(
            "GarbageCollector: Checking for accounts ready to be deleted"
        )

        filters = [FilteringCriterion("status", Status.DELETING)]
        deleting_accounts = self.repositories.account_details.list(
            filters=filters
        )

        now = datetime.now(timezone.utc)
        processed = []

        for account in deleting_accounts:
            try:
                updated_at = account.updated_at
                if updated_at is None:
                    continue

                if updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=timezone.utc)

                deletion_completes_at = updated_at + timedelta(
                    hours=DELETING_TO_DELETED_HOURS
                )

                if now >= deletion_completes_at:
                    self.logger.info(
                        f"GarbageCollector: Account {account.owner_account_id} "
                        f"has been DELETING for 24+ hours. "
                        f"Moving to DELETED and cascading workspaces."
                    )
                    self._move_to_deleted(account)
                    processed.append(str(account.owner_account_id))
                else:
                    remaining = deletion_completes_at - now
                    self.logger.info(
                        f"GarbageCollector: Account {account.owner_account_id} "
                        f"deletion completes in {remaining.seconds // 3600} hours."
                    )

            except Exception as e:
                self.logger.error(
                    f"GarbageCollector: Error processing deleting account "
                    f"{account.owner_account_id}: {e}"
                )
                continue

        return processed

    def _move_to_deleting(self, account: AccountDetails) -> None:
        """Move account from INACTIVE to DELETING."""
        try:
            with self.autocommit():
                self.repositories.account_details.update(
                    id=account.id,
                    status=Status.DELETING,
                )
            self.logger.info(
                f"GarbageCollector: Account {account.owner_account_id} "
                f"moved to DELETING."
            )
            # TODO: Fire LifecycleEvent.ResourceDeleting to Event Bus
            # when Event Bus integration is ready
        except Exception as e:
            self.logger.error(
                f"GarbageCollector: Failed to move account "
                f"{account.owner_account_id} to DELETING: {e}"
            )
            raise

    def _move_to_deleted(self, account: AccountDetails) -> None:
        """
        Move account from DELETING to DELETED.
        Cascades workspace deletion via workspace_service.
        """
        try:
            # Delete all workspaces first
            if self.workspace_service is not None:
                self.logger.info(
                    f"GarbageCollector: Deleting workspaces for "
                    f"account {account.owner_account_id}"
                )
                self.workspace_service.delete_workspaces_by_owner_account_id(
                    account.owner_account_id
                )

            # Then mark account as DELETED
            with self.autocommit():
                self.repositories.account_details.update(
                    id=account.id,
                    status=Status.DELETED,
                )
            self.logger.info(
                f"GarbageCollector: Account {account.owner_account_id} "
                f"moved to DELETED."
            )
            # TODO: Fire LifecycleEvent.ResourceDeleted to Event Bus
            # when Event Bus integration is ready

        except Exception as e:
            self.logger.error(
                f"GarbageCollector: Failed to move account "
                f"{account.owner_account_id} to DELETED: {e}"
            )
            raise
```

### Step 3 — Register in `core.py`

Add to `dataviz_core/core.py` where other services are registered:

```python
from dataviz_core.services.garbage_collector import GarbageCollectorService

# In DatavizCore.__init__:
self.garbage_collector = GarbageCollectorService(
    session_provider=self.session_provider,
    repository_context=self.repository_context,
)
self.garbage_collector.set_workspace_service(self.workspace)
self.garbage_collector.set_account_service(self.account)
```

### Step 4 — Add Celery beat task in `dataviz_async/app.py`

Add this task and schedule — same pattern as existing ones:

```python
@app.task(name="run_garbage_collector")
def run_garbage_collector() -> None:
    logger.info("GarbageCollector: BEGIN")
    get_core(app).garbage_collector.run()
    logger.info("GarbageCollector: DONE")
```

And add to `beat_schedule`:

```python
"run_garbage_collector_every_hour": {
    "task": "run_garbage_collector",
    "schedule": crontab(minute=0),  # runs every hour at :00
},
```

## What's left for tomorrow after meeting Lead

- DB migration to add `grace_period_days` column
- Event Bus integration (`LifecycleEvent.ResourceDeleting`, `ResourceDeleted`)
- How grace period days is set per account/team

## For your update today

You can tell your Lead:

> "Started the automated lifecycle — garbage collector service built in Core, Celery beat task added to Async. Runs hourly, checks INACTIVE accounts for expired grace periods → moves to DELETING, then after 24h → DELETED with workspace cascade. Grace period uses `updated_at` column as timer. Event Bus integration and `grace_period_days` DB column pending tomorrow's discussion."

Share the `ResourceModel` output and I'll generate the final files ready to apply today!
