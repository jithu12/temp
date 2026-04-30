Got you bro 👍 — this section is important for approval, but don’t worry, I’ll give you exact values + content to paste.

⸻

✅ Change Impact Analysis (Fill like this)

🔹 1. Rollback feasibility

👉 Select:
YES

⸻

🔹 2. Impact Type

👉 Select:
Service Degradation / Service Interruption

(If you see multiple options, choose the one closest to Service Interruption)

⸻

📝 3. Technical and Functional Impact (Paste this)

Technical Impact:
- RabbitMQ broker will be temporarily unavailable during the upgrade activity.
- Messaging services depending on the broker will be impacted during the downtime.
- No infrastructure or configuration changes apart from version upgrade.
Functional Impact:
- Applications relying on CCP messaging (daz-prd) may experience temporary disruption in message publishing and consumption.
- No permanent impact on business functionality after successful upgrade.
Scope:
- Impact limited to CCP application "daz-prd" (Region: Paris).
- No impact on other applications/services.
Duration:
- Impact limited to planned maintenance window (~1.5 hours).

⸻

🔹 4. Impacted Environment

👉 Already correct:
Production ✅

⸻

🔹 5. CRB Comments (Optional but good to fill)

Planned activity with controlled downtime. All risks identified and mitigated. Rollback plan available. CCP team support available if required.

⸻

🔹 6. Impact Start / End

👉 Use same as your change window:

* Start: 05/06/2026 – 08:00 PM
* End: 05/06/2026 – 09:35 PM

⸻

🔹 7. Downtime / Degradation

👉 Fill:

Planned downtime during upgrade window (~90 minutes)

⸻

🔥 Small tip (this helps approval a LOT)

Since this is Production + Security change, reviewers check:

* ✔ Impact clearly defined
* ✔ Downtime mentioned
* ✔ Scope limited (you already did 👍)

⸻

If you want next, I can help you with:
👉 Deployment Complexity (what to choose)
👉 Outage YES/NO (important)
👉 Final review before you click submit

Just tell me 👍
