# Daymark architecture and decisions

2026-11-09. Routine implementation choices delegated by the user.

ADR-001: Python 3.11+, Pydantic validation, SQLite transactional storage; optional
FastAPI/HTTPX service. Pure typed domain functions contain temporal and recurrence
rules. The core needs no LLM, network, credentials or application framework.

ADR-002: An Entity has an explicit kind, workspace, lifecycle, links, structured
person/source references and temporal fields. Extensible JSON metadata supplements,
never replaces typed semantics. Principals/persons and assignee/beneficiary differ.
Workspaces are authorization boundaries; projects/goals are organization. Reference
validation and bounded filter trees cannot introduce SQL or foreign-workspace links.

ADR-003: hard deadline, planned time, earliest start and latest target are separate
Temporal values (local date OR aware instant plus IANA zone). Soft intentions never
become hard overdue. Fixed recurrence advances from scheduled occurrence; completion
recurrence from completion local time. Stable series/occurrence keys prevent replay
spawning. Nonexistent local clock times move forward; ambiguous times use first fold.

ADR-004: Store accepts path OR sqlite3.Connection. Caller-owned connection uses a
savepoint and never commits the caller transaction. Application-owned migration
can therefore keep capture/action/undo atomic. Only daymark_ prefixed tables are
owned by this library. Import preserves supplied identities, versions and history;
application-specific archived fields remain structured metadata at the private seam.

ADR-005: all logical writes require operation IDs, store canonical payload hashes
and prior results atomically; mismatch is conflict. Updates require expected version.
Events carry before/after state for safe correction. Undo requires unchanged version.
Reminders have distinct lifecycle, retries, leases, delivery attempts and outcomes.
External exactly-once is impossible; sinks receive stable idempotency keys.

ADR-006: embedded consumer integration precedes independent service deployment.
Existing consumer APIs and source-specific domain records remain private; no copied
application code in public library. Task authority changes once, via application
migration under transaction, with reversible current-state export. Real existing
briefing/delivery ownership stays in place. A standalone recording-sink scheduler
proves durable execution without making notification claims.

Implementation sequence: domain/storage + contract; private application adapter;
service/SDK/CLI; adversarial and independent tests; packaging/privacy gates; controlled
migration/deploy/restore; source release. Research comparison runs in parallel and
supplies feature-level adopted/rejected/deferred ADR traceability.

## Comparison traceability and implementation limits

Chosen feature dispositions are in docs/decisions.md, linked to all sixteen
research ADRs. Important v1 limits: workspace-scoped bearer identities without a
team-role system; no replicated offline merge; civil dates and zoned instants,
without floating-clock type; basic goals without computed progress; recording
sink without real notification delivery. RRULE input is bounded, including
subday-expansion rejection, explicit search-limit errors and durable actual
occurrence budgets. These are explicit design choices, not parity claims.
