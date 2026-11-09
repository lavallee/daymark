# Decisions for the first release

Decision date: 2026-11-09. The implementation owner chose these defaults under the
commissioner's delegated routine-decision authority. This records design choices;
it does not assert that an unfinished implementation has passed acceptance. The
[comparison ADRs](adr-comparison.md) retain the evidence and alternatives. Release
readiness remains governed by [RELEASING.md](../RELEASING.md).

| Research decision | Disposition for v1 | Contract and limit |
|---|---|---|
| C01 Authority independent of organization | Adopt distinction; defer team-role system | Workspace is the authorization scope. Projects, goals, labels, assignment and saved views grant no access. The standalone service maps server-configured bearer identities to one workspace; applications own membership and provisioning. No invitations or per-task sharing. |
| C02 Optional typed structure | Adopt | Independent task, project, goal, person, principal, label, context, filter and reminder kinds. One decomposition parent, separate project/goal associations, lightweight checklists. No automatic child-to-parent completion or multi-parent ownership. |
| C03 Separate temporal meanings | Adopt; structured period labels deferred | Deadline, planned, earliest start and latest target stay independent. A preferred evening can be retained as explicit preference/provenance without manufacturing a clock instant. No automatic interpretation of natural language. |
| C04 Date precision and timezone | Adopt date and zoned instant; defer floating local-clock type | Each temporal value is either a civil date plus governing IANA zone, or an aware instant plus zone. All-day lateness changes after the local day. The API rejects naive instants. A future floating-time type needs explicit migration semantics. |
| C05 Recurrence basis and history | Adopt | Fixed-anchor and after-completion recurrence differ. Materialized successors retain a durable series/occurrence identity and actual occurrence index. Replaying a completion must not spawn twice. User activity is never inferred from passage of time. |
| C06 Missed slots and calendar arithmetic | Adopt explicit bounded subset | Calendar rules support daily, weekly, monthly and yearly frequencies; subday expansion is rejected. DST gaps move forward; overlaps use first fold. Search limits raise an explicit error rather than pretend a series ended. Complex scheduling engines and automatic dependency date shifts are deferred. |
| C07 Reminder intent/firing/delivery | Adopt | Reminder state is separate from task timing and completion. Durable retry, leases, per-claim fencing and stable delivery keys support recovery. Default recording creates no external notification. A receiver must deduplicate for external side effects; exactly-once delivery is not promised. |
| C08 Dependencies separate from hierarchy | Adopt | Explicit dependency edges with workspace/kind checks and cycle rejection. No critical-path planner, automatic rescheduling, effort estimation or resource leveling. |
| C09 Saved views as authorized queries | Adopt | Bounded filter AST evaluates only authorized workspace records. No raw SQL, executable expressions, or view-granted permissions. Query semantics distinguish hard overdue from soft planned work. |
| C10 Offline/conflict contract | Adopt conflict safety; defer replicated offline editing | Stable operation receipts and expected versions make replay and stale-write outcomes explicit. SQLite is the local authority. No CRDT, device sync or automatic collaborative merge claim. |
| C11 Canonical export and loss reporting | Adopt canonical snapshots; defer third-party interchange | Native export/import preserves identities, versions, event history and operation replay receipts. Imports validate references atomically. Any application-specific downgrade must report/refuse semantic loss. No CalDAV/CSV/vendor round-trip guarantee. |
| C12 Extensibility and adapters | Adopt | Typed core fields plus structured metadata and source references. Application adapters and delivery sinks are separate; core does not import private producers. No plug-in execution from untrusted imports. |
| C13 Privacy/licensing | Adopt | MIT standalone source, synthetic examples, protected local state, redacted HTTP/worker errors. Source, dependencies and archives require privacy/security review. A successful secret scan does not establish absence of private data. |
| C14 Personal overlays | Defer | Planning fields currently belong to the shared record. Private per-person planning overlays need separate visibility/version semantics. |
| C15 Goals without mandatory methodology | Adopt basic kind; defer computed metrics | Goals and projects are optional explicit records; assignee and beneficiary differ. No mandatory household, GTD, sprint or OKR workflow. No implicit goal-progress formula. |
| C16 Standalone execution/deployment | Adopt | Embedded SQLite first, optional authenticated HTTP service/SDK/CLI. Supplied connections preserve the caller's unit of work; path-owned connections manage their own commits. Durable scheduler and restore drills are operational acceptance, separate from package build success. |

These choices deliberately avoid parity claims with any surveyed product. A future
feature should reopen its linked decision with a concrete use case, migration
impact and a regression test, rather than inherit a vendor's label without its
meaning.

## Harness adaptation

The [implementation review](implementation-review.md) inspects pinned upstream
source and test harnesses without executing or copying them. For this release,
borrow the independent reference-model technique through bounded deterministic
operation histories (`tests/test_state_sequences.py`): five seeded histories,
forty command opportunities each, two isolated workspaces, deliberate stale
writes, replay, restart and native snapshot transfer. The reference model tracks
only expected title/status/version and cannot stand in for the implementation's
own database logic. Barrier/lease and subprocess-crash regressions remain separate.

Adding a property-testing runtime is deferred; a new dependency is unnecessary to
exercise this bounded contract. A useful future generated trace should become a
small explicit regression before broadening the trial. Neither a bounded run nor
an inspected upstream test is a proof of all interleavings or deployment behavior.
