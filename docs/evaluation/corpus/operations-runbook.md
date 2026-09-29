# Operations runbook

## Deployment window
Routine production deployments run on Tuesday and Thursday between 10:00 and 16:00 local time. The release owner posts a change ticket before starting and records the deployed version afterward. Emergency fixes outside that window need the on-call lead's approval. The window reduces handoff risk, but it does not remove the requirement for automated checks.

## Rollback
If the error rate exceeds five percent for ten consecutive minutes after a release, the release owner starts rollback. The owner restores the last known good image and posts an incident update. Database migrations need an explicit rollback plan in the change ticket because a container rollback alone may not restore schema compatibility. The on-call lead confirms service recovery.

## On-call handoff
The primary on-call engineer hands off at 09:00 every Monday. The handoff note lists open incidents, risky changes and dashboard links. The secondary engineer acknowledges the handoff within one hour. If the primary cannot respond, the secondary becomes incident commander until a lead assigns a replacement. The note should not include credentials.

## Backup checks
PostgreSQL backups are checked every morning at 08:00. The daily check confirms the latest backup completed and that its checksum matches the stored manifest. A restore drill is run once each quarter in an isolated environment. The backup operator records failures in the operations ticket queue and pages on-call if two consecutive checks fail.
