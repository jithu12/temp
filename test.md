Great bro 👍 since **outage is already created**, here is the **clean exact step-by-step procedure** you should follow now. I’ll structure it so you can **execute + document in JIRA easily**.

---

# PCP Auto-Failover Onboarding – Correct Steps

## 1️⃣ Verify Current Cluster Status

First confirm the cluster state.

Run:

```
GET /applications/{application-id}
```

Check in response:

```
dataplaneClusters
```

Expected (your current state):

```
version: 17
status: ACTIVE
autoFailover: false
envType: nonprd
```

Meaning:

✔ PostgreSQL 17 running
❌ AutoFailover not enabled yet

Take **screenshot for Jira proof**.

---

# 2️⃣ Enable AutoFailover

Your teammate confirmed you must **patch the application (not environment)**.

Use:

```
PATCH /applications/{application-id}
```

Payload:

```json
{
  "options": {
    "dataplane": {
      "autoFailover": true,
      "envType": "nonprd"
    }
  }
}
```

This will:

• Enable PCP failover manager
• Activate replica monitoring
• Allow automatic promotion

Execute in **Swagger UI**.

---

# 3️⃣ Wait for Configuration Update

Give it **1–2 minutes**.

Then verify again:

```
GET /applications/{application-id}
```

Now it should show:

```
version: 17
status: ACTIVE
autoFailover: true
```

Take **another screenshot**.

---

# 4️⃣ Validate Failover

Now you must confirm the feature actually works.

Possible validation methods (depends on your access):

### Method A (Most common)

Restart the **primary DB pod**.

Example:

```
kubectl delete pod <primary-db-pod>
```

or restart DB instance.

---

### Expected Behaviour

```
Primary node fails
↓
Replica detected
↓
Replica promoted automatically
↓
Application reconnects
```

Failover time usually:

```
10–30 seconds
```

---

# 5️⃣ Validate Application Connectivity

Check:

✔ Application running
✔ No DB connection errors
✔ Queries working normally

---

# 6️⃣ Close the Outage

Since you already created outage earlier:

Notify team that **activity completed**.

Example message:

```
Maintenance activity completed.

PCP auto-failover has been successfully enabled
for the DEV dataplane cluster.

Cluster Version: PostgreSQL 17
AutoFailover: Enabled

Failover validation completed successfully.
Services are fully operational.
```

---

# 7️⃣ Update the JIRA Ticket

Add final documentation.

Example comment:

```
PCP auto-failover onboarding completed in DEV environment.

Steps performed:
1. Verified dataplane cluster configuration
2. Confirmed PostgreSQL 17 cluster active
3. Enabled autoFailover using application patch API
4. Verified cluster status after configuration update
5. Performed failover validation by simulating primary node failure
6. Confirmed automatic replica promotion
7. Validated application connectivity post failover

Result:
Auto failover is functioning correctly for the DEV dataplane cluster.
```

---

# Visual Flow of What You Just Did

```
Old State
PG17 ACTIVE
AutoFailover = false

        ↓

PATCH application

        ↓

New State
PG17 ACTIVE
AutoFailover = true

        ↓

Failover Test

        ↓

Replica becomes primary automatically
```

---

# Important Tip (Senior Engineer Trick)

Before closing the ticket attach **3 screenshots**:

1️⃣ Before change

```
autoFailover : false
```

2️⃣ Patch request executed

3️⃣ After change

```
autoFailover : true
```

That makes the **JIRA look very professional**.

---

If you want, I can also show you one **very small thing most engineers miss in this task** that could save you from **a production incident later**. It’s a **real DBA trick.**
