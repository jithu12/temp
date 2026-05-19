Based on what we already know from the PPT and the code, here’s what you can build today without needing Lead’s answers:
Do today
1. Garbage Collector Service in Core
We have enough info to build this:
	•	Check INACTIVE accounts → compare updated_at with current time
	•	If grace period expired → move to DELETING
	•	If DELETING for 24+ hours → move to DELETED + cascade workspaces
2. Celery Beat Task in Async
Add run_garbage_collector task to app.py — runs every hour. We already saw how the existing tasks are structured.
3. Add grace_period_days to AccountDetails
Add the column with default 30 days. When Lead confirms the actual source, we just update the default or the setter.
What to wait for
	•	Where grace period value comes from (platform API or manual?)
	•	Event Bus integration (need to see existing Event Bus code)
Start with this
Share:

grep -rn "class ResourceModel\|updated_at\|created_at" dataviz_core/models/sqlalchemy.py | head -20


And:

ls dataviz_core/migrations/versions/ | tail -5


That’ll tell us if updated_at exists on ResourceModel and how migrations work — then I’ll generate all the files ready to apply today. 💪​​​​​​​​​​​​​​​​
