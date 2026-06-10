Oh sorry bro! Here are the cleaned up versions without any code:

---

**OBSDATAVIZ-601 — Breaking Change Notification**

**Description:**
```
The /health endpoint response format is changing as part 
of the migration to the new SGCP Platform Health Standard.

The following breaking changes are being introduced:

- The "dependencies" array is removed from the response
- Module name format changed from "application" to 
  "dataviz:application"
- New fields added to the response: "description", 
  "time", "comment"
- Each module now includes a "description" field
- Module can now include optional "rootcause" field 
  when status is not UP

Any team or tool currently consuming the Dataviz /health 
endpoint needs to update their integration accordingly.
```

**Acceptance Criteria:**
```
- All consumers of the Dataviz /health endpoint 
  are identified
- All affected teams are notified about the 
  breaking changes
- Breaking changes are clearly documented and shared
- Affected teams confirm they have updated 
  their side
- Internal documentation is updated with the 
  new response format
- Grafana dashboards or alerts using /health 
  response fields are reviewed and updated 
  if needed
- Announcement posted on JIRA VIVA ENGAGE
```

---

**OBSDATAVIZ-630 — Validate Integration With Platform API Framework**

**Description:**
```
After migrating the /health endpoint to use the official 
platform_health Python library, we need to validate that 
the integration is working correctly end to end and fully 
complies with the SGCP Platform Health Standard.

This includes validating the response format, Redis 
caching behaviour, Celery task scheduling, and health 
checker execution for all Dataviz dependencies.
```

**Acceptance Criteria:**
```
- GET /health returns response matching the new 
  Platform Health Standard format exactly
- All required fields are present in the response: 
  comment, description, modules, status, time, version
- Module names follow the dataviz colon module-name 
  format
- Dependencies field is not present in the response
- Status values are only UP, DEGRADED, or DOWN
- All 6 dependency checkers are working correctly:
  * KubeCPClient checker
  * MyVaultClient checker
  * PostgresClient checker
  * CeleryWorkflowExecutor checker
  * DNSClient checker
  * PKIClient checker
- Redis Sentinel caching is working correctly and 
  /health is being served from cache
- When cache is empty, a default UP response is 
  returned in the correct format
- Celery beat task is scheduled and running 
  at the correct interval
- When a dependency goes DOWN, the module status 
  updates correctly in the response
- When a module is DOWN, the rootcause field is 
  populated correctly
- All unit tests are passing
- All component tests are passing
- Swagger schema is validated against the 
  actual response
- Platform team health validator confirms 
  compliance with the new standard
```

---

Clean and ready to paste into Jira bro! 👍
