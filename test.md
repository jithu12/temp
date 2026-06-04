Sure bro. Here’s a detailed description you can paste directly into GitHub Copilot, VS Code AI, Claude, or any LLM to explain the expected workflow and implementation.

⸻

Account Lifecycle Resource Management Workflow

Overview

The system manages workspace resources based on an account’s lifecycle status. There are two mechanisms that trigger the same actions:

1. Event-driven Garbage Collector
    * Triggered immediately when lifecycle events are received.
    * Identifies resources linked to the account in Dataviz.
    * Executes the required action.
2. Scheduled Cron Job
    * Runs daily at 12:00 AM.
    * Checks all account statuses.
    * Performs the same actions as a fallback mechanism in case events were missed.

⸻

Account Lifecycle States

1. Disabled

When an account status changes to Disabled:

Action:

* Shutdown all associated workspace resources.
* Do not delete resources.
* Preserve data and configuration.

Account Status = Disabled
        ↓
Shutdown Workspace

⸻

2. Active

When an account status changes to Active:

Action:

* Start or enable all associated workspace resources.

Account Status = Active
        ↓
Turn On Workspace

⸻

3. Deleting

When an account status changes to Deleting:

Action:

* Delete all workspace resources associated with the account.
* Remove resources permanently.

Account Status = Deleting
        ↓
Delete Workspace Resources

⸻

4. Deleted

When an account status changes to Deleted:

Action:

* Verify whether all resources were successfully deleted.
* If some resources could not be deleted:
    * Send a run-update notification email to the Dataviz team.
    * Include details of remaining resources requiring manual intervention.

Account Status = Deleted
        ↓
Check Remaining Resources
        ↓
If resources still exist
        ↓
Send Notification Email to Dataviz Team

⸻

Event-Driven Garbage Collector Flow

The garbage collector listens for lifecycle events:

LifecycleEvent.ResourceDisabled
LifecycleEvent.ResourceActive
LifecycleEvent.ResourceDeleting
LifecycleEvent.ResourceDeleted

Workflow:

Receive Lifecycle Event
        ↓
Identify resources linked to the account in Dataviz
        ↓
Switch based on event type
ResourceDisabled  → Shutdown Workspace
ResourceActive    → Turn On Workspace
ResourceDeleting  → Delete Workspace Resources
ResourceDeleted   → Send notification if resources remain

Pseudo-code:

def handle_lifecycle_event(event, account_id):
    resources = get_account_resources(account_id)
    if event == "ResourceDisabled":
        shutdown_workspace(resources)
    elif event == "ResourceActive":
        start_workspace(resources)
    elif event == "ResourceDeleting":
        delete_workspace_resources(resources)
    elif event == "ResourceDeleted":
        remaining = find_remaining_resources(resources)
        if remaining:
            send_notification_email(
                account_id,
                remaining
            )

⸻

Scheduled Cron Job Flow

Schedule:

Runs every day at 00:00 (12 AM)

Workflow:

Cron Job Starts
        ↓
Fetch Accounts
        ↓
For each account
        ↓
Check Account Status
Disabled  → Shutdown Workspace
Active    → Turn On Workspace
Deleting  → Delete Workspace Resources
Deleted   → Send notification if resources remain

Pseudo-code:

def nightly_cron_job():
    accounts = fetch_all_accounts()
    for account in accounts:
        status = account.status
        resources = get_account_resources(account.id)
        if status == "Disabled":
            shutdown_workspace(resources)
        elif status == "Active":
            start_workspace(resources)
        elif status == "Deleting":
            delete_workspace_resources(resources)
        elif status == "Deleted":
            remaining = find_remaining_resources(resources)
            if remaining:
                send_notification_email(
                    account.id,
                    remaining
                )

⸻

Design Goal

The Garbage Collector provides immediate action through events, while the Cron Job acts as a safety net to ensure consistency and recover from missed events, failures, or synchronization issues.

This guarantees that workspace resources always match the current account lifecycle state.
