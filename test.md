This error is a **database migration issue** in the Async repo — nothing to do with your API code. The Celery worker is trying to store task results in a `celery_taskmeta` table but the sequence `task_id_sequence` doesn't exist in the DB.

## What the error means

```
psycopg2.errors.UndefinedTable: relation "task_id_sequence" does not exist
```

Celery uses a DB table to track task results. The table exists (`celery_taskmeta`) but the sequence it depends on (`task_id_sequence`) was never created — meaning the DB migrations for the Async repo haven't been run.

## This is NOT your problem to fix

This is an infrastructure/DB setup issue for the Async repo. Raise it to your Lead:

> "When I start the Celery worker with `celery -A dataviz_async.app:app worker`, I get `UndefinedTable: relation 'task_id_sequence' does not exist`. Looks like the DB migrations for the Async repo haven't been applied on dev. What's the correct command to run the migrations?"

## What they'll likely tell you

Run the migrations for the Async repo. It's probably one of:

```bash
# Option 1 — Alembic (most common in this codebase based on alembic.ini you saw earlier)
cd Dataviz-Async
alembic upgrade head

# Option 2 — Flask migrate
flask db upgrade

# Option 3 — Custom script
python manage.py migrate
```

Check the Async repo for an `alembic.ini` file:
```bash
ls Dataviz-Async/
```

If `alembic.ini` is there, run:
```bash
cd Dataviz-Async
alembic upgrade head
```

Then restart the Celery worker:
```bash
celery -A dataviz_async.app:app worker
```

## Summary of where everything stands

| Item | Status |
|---|---|
| API admin gate | ✅ Working |
| Path param passing | ✅ Working |
| Core admin gate (`ADMIN_ACCOUNTS`) | ✅ Fixed |
| `workflow_executor` injection | ✅ Fixed |
| Accounts with 0 workspaces | ✅ Deactivate/activate/delete all work |
| Accounts WITH workspaces | ⏳ Waiting for Celery worker DB migration |
| Request body removed from YAML | ✅ Just fixed |

Your code is done. The only remaining blocker is the Async repo DB migration which is an infrastructure issue. Once that's sorted and the Celery worker starts cleanly, the full cascade (account + all workspaces) will work end to end.
