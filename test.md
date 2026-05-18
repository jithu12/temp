OK! Based on what your Lead said, let me break down what we CAN start today without needing more info.

## What we know for sure

1. **Grace period starts when admin deactivates** ✅
2. **Use `updated_at` column** to calculate when grace period started ✅
3. **Grace period is per team/account** — stored somewhere per account
4. **Celery already exists** — use it for the scheduler ✅
5. **Garbage collector needed** ✅

## What we can build today

### Part 1 — Find the grace period config

```bash
grep -rn "grace_period\|grace\|deletion_days\|retention" dataviz_core/models/ | grep -v __pycache__ | head -10
```

```bash
grep -rn "updated_at\|updated_time" dataviz_core/models/account_details.py
```

```bash
cat dataviz_core/models/account_details.py
```

Share those outputs — I need to see:
- What columns exist on `api_account_details`
- Whether grace period is already stored somewhere

### Part 2 — Find existing Celery beat config

```bash
cat dataviz_async/app.py
```

```bash
grep -rn "beat\|schedule\|periodic\|crontab" dataviz_async/ | grep -v __pycache__ | head -10
```

### Part 3 — Find garbage collector if exists

```bash
grep -rn "garbage\|gc_\|GarbageCollector\|collect" dataviz_core/services/ | grep -v __pycache__ | head -10
```

Share all those outputs and today we can build:

1. ✅ **Celery beat periodic task** — runs every hour, checks accounts in INACTIVE state
2. ✅ **Garbage collector logic** — finds accounts where grace period has expired → sets DELETING
3. ✅ **Second pass** — finds accounts in DELETING for 24+ hours → sets DELETED + cascades workspaces

This is enough for a solid update to your Lead tomorrow. The Event Bus part we can add once you get more info from him in office. Share those outputs and let's start! 💪
