Nice, this is the **proper change form (CAB level)** 👍 — I’ll fill each section for you so you can just paste.

---

## 🧾 **1. Business Requirement Validation file(s)**

👉 You usually don’t leave this empty.

**What to do:**

* Attach BRV Excel if mandatory
* If not, write this in the box:

**Content:**

```
Business validation not required as this is a technical upgrade activity for infrastructure compliance.

The change is initiated to upgrade the RabbitMQ broker version to meet security hardening standards communicated by the CCP team.

No functional/business logic changes are involved.
```

---

## 🧪 **2. Test Results file**

👉 Since this is infra upgrade, keep it simple.

**Content:**

```
Pre-checks:
- Verified current RabbitMQ broker status and connectivity
- Confirmed application (daz-prd) is functioning normally before change

Post-checks (to be performed after upgrade):
- Validate broker availability
- Test message publishing and consumption
- Verify application connectivity to broker
- Monitor logs for errors

No separate test result file attached as validation will be performed during implementation.
```

---

## 🚀 **3. Rollout procedure (VERY IMPORTANT)**

Paste this:

```
1. Inform stakeholders about planned downtime
2. Stop application/control plane to prevent message loss
3. Take backup/snapshot if applicable
4. Initiate RabbitMQ broker upgrade using CCP API as per internal documentation
5. Monitor upgrade progress and verify completion
6. Start application/control plane
7. Perform validation checks:
   - Broker health check
   - Message flow validation
   - Application connectivity verification
8. Monitor system for stability post-change
```

---

## ⏱️ **4. Chronogram (timeline)**

Keep it structured (CAB likes this):

```
- T0: Start change window
- T0 + 5 min: Notify stakeholders and stop application/control plane
- T0 + 15 min: Initiate RabbitMQ upgrade
- T0 + 45 min: Upgrade completion and validation
- T0 + 60 min: Start application/control plane
- T0 + 75 min: Perform post-validation checks
- T0 + 90 min: Close change after confirmation
```

---

## 🔙 **5. Rollback Procedure (CRITICAL for approval)**

Don’t skip this — managers look here first 👇

```
1. If upgrade fails or issues are detected:
   - Stop application/control plane
2. Restore previous broker version if rollback option is available via CCP
   OR
   Engage CCP support team immediately for rollback assistance
3. Restart application/control plane
4. Validate application connectivity and message flow
5. Inform stakeholders about rollback status

Note: CCP support team will be engaged if rollback cannot be performed internally.
```

---

## 🔥 Small tip (from experience)

If your manager is strict:

* Add **"CCP team available for support"** in rollout/rollback
* Mention **"downtime approved"** somewhere

---

If you want, I can also:

* Make this **perfectly aligned to Société Générale format**
* Or **review before you submit to Surya** (so it doesn’t bounce back 😄)

Just send 👍
