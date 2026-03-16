Yes bro 👍 I checked both the **JIRA card** and the **documentation screenshots** you shared. I’ll simplify the **exact task you need to do** so you can execute it without confusion.

Your JIRA task is basically:

**Test PCP Database Auto-Failover in DEV by upgrading the dataplance cluster and verifying failover.**

---

# What Your JIRA Task Means (Simple)

You need to:

1️⃣ Create a small outage in **DEV**
2️⃣ Shutdown the **Control Plane**
3️⃣ Upgrade the **PCP dataplane PostgreSQL version (to 15/16/17)**
4️⃣ Enable **Auto Failover**
5️⃣ Restart Control Plane
6️⃣ Test failover
7️⃣ Document the steps

---

# Full Step-by-Step Execution

## Step 1 — Verify if AutoFailover already exists

Before doing anything check this.

Call:

```
GET /applications/{application-id}
```

Look inside response:

```
dataplaneClusters.autoFailover
```

If it says:

```
autoFailover : true
```

➡ Already enabled
➡ No upgrade required

If:

```
autoFailover : false
```

➡ Continue with upgrade

---

# Step 2 — Shutdown Control Plane

Doc says you must **stop control plane during upgrade**.

Send this API:

```
PATCH /applications/{application-id}/environment/{environment-id}
```

Payload:

```json
{
  "options": {
    "kube": {
      "webserver": {
        "replicas": 0
      },
      "async": {
        "replicas": 0
      }
    }
  }
}
```

This will:

* Stop webserver pods
* Stop async workers

👉 Basically **Control Plane OFF**

Then redeploy the last artifact.

---

# Step 3 — Upgrade PostgreSQL (Dataplane)

Now upgrade PostgreSQL cluster.

API:

```
PATCH /applications/{application-id}
```

Payload:

```json
{
  "options": {
    "dataplane": {
      "pgVersion": "17",
      "envType": "nonprd"
    }
  }
}
```

This will:

* Upgrade PostgreSQL
* Enable auto failover support

⏱ Downtime: ~30 minutes

---

# Step 4 — Wait Until Upgrade Completes

Check status:

```
GET /applications/{application-id}
```

Look for:

```
status: ACTIVE
```

Check:

```
dataplaneCluster.version
```

Example:

```
pgVersion : 17
```

---

# Step 5 — Restart Control Plane

Now restore replicas.

```
PATCH /applications/{application-id}/environment/{environment-id}
```

Payload:

```json
{
  "options": {
    "kube": {
      "webserver": {
        "replicas": 3
      },
      "async": {
        "replicas": 3
      }
    }
  }
}
```

Redeploy artifact again.

---

# Step 6 — Verify AutoFailover

Again check:

```
GET /applications/{application-id}
```

Confirm:

```
autoFailover : true
```

---

# Step 7 — Test Failover

Now simulate outage.

Possible tests:

* Kill primary DB pod
* Stop primary node
* Trigger failover manually

Then verify:

* Replica becomes **primary**
* Application reconnects automatically

---

# Step 8 — Update JIRA Documentation

Add:

✔ Steps performed
✔ Version upgraded
✔ Failover test result
✔ Logs/screenshots

---

# Example JIRA Comment (You Can Use)

```
Performed PCP dataplane upgrade to PostgreSQL 17 in DEV environment.

Steps executed:
1. Verified current cluster configuration
2. Shutdown control plane by setting replicas to 0
3. Upgraded dataplane PostgreSQL version
4. Waited for application status to become ACTIVE
5. Restarted control plane
6. Verified autoFailover configuration
7. Simulated failover and validated successful promotion of replica

Result:
AutoFailover enabled and functioning correctly in DEV environment.

Documentation prepared for step-by-step process.
```

---

# ⚠️ Two Important Warnings From Doc

1️⃣ **Do NOT upgrade RabbitMQ and DB at same time**

2️⃣ Ensure you have scopes:

```
ccp:read
ccp:write
```

---

# My Advice (As a DBA 👍)

Before upgrade confirm:

* replication healthy
* backup exists
* no active deployments

---

# If you want, I can also explain

* **How PCP failover actually works internally**
* **How to safely trigger failover test**
* **How to write a very strong JIRA documentation**

Just tell me bro. This task is actually **very easy once you see the flow.**
