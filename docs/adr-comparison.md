# Daymark comparison decisions and evidence trace

Date: **2026-11-09 UTC**. Status: **proposed research recommendations**. This is an analysis record, not a declaration that implementation exists or has passed acceptance. The companion [comparison](comparison.md) explains the mechanisms; [research-sources.json](research-sources.json) contains source identities, retrieval states, claim records, contradictions and gaps.

Evidence receipt: **111 primary sources and 30 claim records support 16 proposed decisions**. The comparison and JSON inventory distinguish extraction failures, rejected shells, version limits and remaining gaps. No acceptance fixture below was executed by this research.

The objective is a general-purpose flexible core that can serve personal, group and organizational work. Synthetic household examples exercise semantics without defining a household-specific domain. No adoption here authorizes copying source, importing private data, granting access, sending notifications or publishing a deployment.

Research is **finalized and retrieval is closed**. The 16 dispositions remain proposed design recommendations; finalizing the research does not mark the engine complete or its acceptance fixtures passed.

## Execution guidance

Implementation can proceed with stable identities, explicit scope, distinct date meanings, lifecycle history and independent reminder delivery state. It does not need to wait for exhaustive product parity or resolution of every optional integration. P0 below means a semantic boundary worth preserving early, not necessarily a first-release screen or public promise.

“Borrow” adopts a distinction or mechanism in principle; “try” requires the stated fixture before acceptance; “track” preserves an option; “reject” rejects a specified default for this brief. Every record remains proposed until the implementation owner records its actual disposition elsewhere. These documents deliberately do not edit implementation status or make approval decisions for that owner.

| Decision | Recommendation | Priority | Evidence claims |
| --- | --- | --- | --- |
| ADR-C01 Authority independent of organization | Borrow | P0 | C01, C02, C03 |
| ADR-C02 Optional typed structure | Borrow / try | P0 | C04, C05 |
| ADR-C03 Separate temporal meanings | Borrow | P0 | C06, C07, C08 |
| ADR-C04 Preserve date precision and timezone policy | Borrow / try | P0 | C09, C10 |
| ADR-C05 Recurrence policy and occurrence history | Borrow / try | P0 | C11, C12, C13 |
| ADR-C06 Explicit missed-slot and calendar arithmetic | Borrow / try | P0 | C11, C14 |
| ADR-C07 Reminder intent separate from delivery | Borrow / try | P0 | C15, C16, C17 |
| ADR-C08 Dependencies separate from hierarchy | Borrow / try | P1 | C18, C19 |
| ADR-C09 Saved views as authorized queries | Borrow / try | P1 | C20, C21 |
| ADR-C10 Declare offline/conflict contract | Try / track | P0 boundary, optional capability | C22, C23 |
| ADR-C11 Canonical export plus loss reports | Borrow / try | P1 | C24, C25 |
| ADR-C12 Scoped extensibility and adapters | Borrow / track | P1 | C26, C27 |
| ADR-C13 Privacy and license decisions remain explicit | Borrow | P0 boundary | C28, C29 |
| ADR-C14 Personal planning overlays | Try | P2 | C06, C30 |
| ADR-C15 Review/goals without mandatory methodology | Borrow / track | P1 | C04, C05 |
| ADR-C16 Standalone execution and deployment separation | Borrow / try | P0 boundary | C16, C17, C22 |

## ADR-C01 — Authority independent of organization

**Problem.** A project can organize work, define visibility, or both. Reusing it as an implicit universal security boundary makes assignment, moving work and shared views surprising.

**Proposal.** Give every task an explicit owning authorization scope. Keep creator, responsible person, team membership and role grants distinct. Begin with a small auditable capability set; do not infer read access from a label, dependency, parent, assignment or view. Treat transferring ownership as a deliberate operation with a preview of changed access and custody. Prefer one owner scope per task initially.

**Evidence.** Todoist distinguishes personal custody from team-owned data; Vikunja distinguishes team administration from project permission and requires existing access before assignment. Asana demonstrates permission elevation across multi-home projects; Linear distinguishes public-team visibility from joined membership. [T11](https://www.todoist.com/help/account-and-billing/teams/teams-faq-lQhBHo0y6), [V11](https://vikunja.io/help/sharing-and-teams/), [V04](https://vikunja.io/help/tasks/), [S05](https://help.asana.com/s/article/task-permissions), [L05](https://linear.app/docs/teams). Claims C01–C03.

**Alternatives.** Per-task ACL everywhere gives flexibility with substantial complexity. A single shared workspace gives simplicity but may not meet private-work needs. Permission unions across projects are legitimate product choices but are rejected as an invisible default.

**Acceptance fixture.** An administrator, editor, reader and outsider exercise search, detail, counts, comments, attachments, export and reminder delivery. Reassignment does not grant access. Removing a team member affects future operations, including queued notifications. A shared view never widens source-item visibility.

**Reverse or extend when.** A concrete cross-organization collaboration requirement justifies per-task sharing or multiple homes, with an explicit permission-composition rule. Open question: one accountable person plus collaborators, or multiple equal assignees?

## ADR-C02 — Optional typed structure

**Problem.** A checkbox, subtask, project, goal and responsibility all look like nested text but have different lifecycle and authority needs.

**Proposal.** Support independent tasks with optional projects and one decomposition parent; distinguish lightweight checklist entries. Keep ongoing areas/responsibilities and strategic outcomes optional. A project can represent an outcome or an ongoing list through explicit kind/policy, without forcing every user to create a goal hierarchy. Promote a checklist entry when it needs task-level identity.

**Evidence.** Things exposes areas, projects, headings and checklist details; OmniFocus distinguishes project modes; Taskwarrior organizes through optional attributes; Org allows outline conventions; Asana and Linear model higher-level objectives separately from actions. [H05](https://culturedcode.com/things/support/articles/2803573/), [O10](https://support.omnigroup.com/documentation/omnifocus/universal/4.10.1/en/perspectives/), [W14](https://taskwarrior.org/docs/examples/), [G15](https://orgmode.org/manual/Document-Structure.html), [S09](https://developers.asana.com/docs/goals), [L06](https://linear.app/docs/initiatives). Claims C04–C05.

**Acceptance fixture.** Capture an unprojected task, complete a finite project with cancelled historical children, archive an ongoing list, promote a checkbox, and move a subtree without changing dependency edges. Detect parent cycles. A goal's status is not silently inferred from an arbitrary count of completed tasks.

**Deferred.** Arbitrary multi-parent task graphs and automatic project/goal completion. Reconsider when a concrete workflow cannot be represented with associations and independent dependencies.

## ADR-C03 — Availability, intention, target, and deadline

**Problem.** One due field cannot safely mean earliest start, desired work day, latest preferred finish, mandatory cutoff and recorded actual start.

**Proposal.** Preserve independent meanings: availability/not-before, planned day or soft window, optional latest preferred target, hard deadline and actual lifecycle timestamps. UI can reveal these progressively. Moving a plan must not move a deadline. Do not copy a container's dates into children without distinguishing explicit and effective values.

**Evidence.** Current OmniFocus has defer, planned and due semantics with different inheritance; Todoist and Things separate plan from deadline; Vikunja separates planned window from due; Taskwarrior distinguishes scheduled, wait, start and until. [O07](https://support.omnigroup.com/documentation/omnifocus/universal/4.10.1/en/outline/), [T07](https://www.todoist.com/help/articles/introduction-to-deadlines-in-todoist-uMqbSLM6U), [H01](https://culturedcode.com/things/support/articles/2803579/), [V10](https://vikunja.io/help/dates-and-reminders/), [W13](https://taskwarrior.org/docs/using_dates/), [W17](https://taskwarrior.org/docs/task/). Claims C06–C08.

**Acceptance fixture.** Synthetic “prepare report”: available April 1, preferred April 5–8, latest preferred April 10, hard deadline April 20. Move the preferred window; the cutoff and actual history remain fixed. A missed preference is distinguishable from lateness. If availability exceeds the cutoff, expose the contradiction instead of silently correcting either value.

**Default/reversal.** Prefer explicit values and explain any computed defaults. Add inherited constraint calculations only when the product needs them and can display their provenance.

## ADR-C04 — Date precision and zone policy

**Proposal.** Keep civil date, floating local time, zoned wall-clock schedule and instant distinct. Use a governing timezone for date-only lateness and reminder derivation. Store the rule needed to explain future calculations, not just a UTC conversion. Treat recurrence timezone separately from display timezone.

**Evidence.** Todoist and OmniFocus document floating/fixed choices; Asana uses alternative day-only and timestamp fields; Taskwarrior explicitly converts date input to midnight; RFC 5545 defines interoperable temporal representations. [T08](https://www.todoist.com/help/todoist/features/set-a-fixed-time-or-floating-time-for-a-task-YUYVp27q), [O08](https://support.omnigroup.com/documentation/omnifocus/universal/4.10.1/en/inspector/), [S02](https://developers.asana.com/docs/tasks), [W02](https://taskwarrior.org/docs/dates/), [R01](https://www.rfc-editor.org/rfc/rfc5545). Claims C09–C10.

**Acceptance fixture.** Two viewers in different zones see the same civil-date deadline; a floating 09:00 changes instant when its governing local zone changes; a fixed instant does not; a zoned daily 09:00 preserves wall time across DST. The interface displays enough context to distinguish them. Test midnight boundaries and travelling users.

**Open choice.** Whether a shared floating schedule follows each recipient or one scope zone. Proposed default for shared obligations: explicit scope/series zone; per-person floating reminders may be optional. This is a reversible recommendation, not an accepted product decision.

## ADR-C05 — Recurrence definitions and occurrence history

**Proposal.** Use explicit series identity, stable occurrence identity, nominal scheduled slot, lifecycle state and an auditable series version. Offer calendar-fixed and completion-relative bases independently of whether occurrences are materialized ahead. A user may see one current task while the model retains each outcome. Support completing, skipping, cancelling and ending the series as distinct actions.

**Evidence.** Taskwarrior has template-linked instances; Things has templates/copies; Vikunja advances the same task; Org shifts its entry; Asana duplicates on completion; Linear generates based on schedule. These are incompatible history mechanisms despite all being called recurrence. [W01](https://taskwarrior.org/docs/recurrence/), [H02](https://culturedcode.com/things/support/articles/2803564/), [V10](https://vikunja.io/help/dates-and-reminders/), [G02](https://orgmode.org/manual/Repeated-tasks.html), [S04](https://help.asana.com/s/article/scheduling-tasks-using-start-dates-due-dates-and-times), [L03](https://linear.app/docs/creating-issues). Claims C11–C13.

**Acceptance fixture.** Two duplicate completion commands create one transition and one next logical occurrence. Editing this occurrence preserves the series; editing future rules preserves old history. Deleting an instance does not accidentally terminate the series. Undo behavior has an explicit effect on a next occurrence already edited by a collaborator.

**Reject/defer.** A recurrence string plus one mutable due date as the only historical record; nested repeating containers until clone/reset rules are specified. Multiple repeated levels can be intentionally supported later, with safeguards against duplication.

## ADR-C06 — Missed slots, arithmetic, and DST

**Proposal.** Declare keep-all, skip-missed or coalesce policies; distinguish skipped from completed history. Specify calendar versus elapsed arithmetic, month-end policy, leap-day policy, DST gaps/folds, count/date endings and completion timestamp authority. Preview future dates before saving a rule. Bound catch-up work.

**Evidence.** Org distinguishes one-step advancement, catch-up and completion-relative repeaters. OmniFocus provides catch-up and rounding choices. Vikunja documents month-end clamping, while RFC 5545 recurrence ignores invalid generated instances. These are different contracts. [G02](https://orgmode.org/manual/Repeated-tasks.html), [O08](https://support.omnigroup.com/documentation/omnifocus/universal/4.10.1/en/inspector/), [V10](https://vikunja.io/help/dates-and-reminders/), [R01](https://www.rfc-editor.org/rfc/rfc5545). Claims C11, C14.

**Acceptance fixture.** Complete an overdue weekly rule; compare calendar cadence with completion-relative cadence. Test January 31, leap day, local 02:30 in a spring gap and fall fold, daily local clock time versus every 24 hours, and a long scheduler outage. A count-based ending must specify whether skipped or cancelled slots consume count.

**Open choice.** No universal missed-slot default is justified by these sources. Prefer showing a choice at rule creation, with an understandable suggested default and reversible history. Arbitrary business calendars and natural-language parsing can wait.

## ADR-C07 — Reminder intent and delivery attempts

**Proposal.** Separate reminder definition, logical firing, recipient/channel delivery and each attempt. Persist reminder intent atomically with relevant changes, or demonstrate equivalent recovery. Use unique logical firing keys, bounded retries, expiry, cancellation and reconciliation. Track provider acceptance independently of receipt or human attention. Never advertise exactly-once human notification.

**Evidence.** Tasks.org distinguishes repeated reminders from repeated tasks. User guides specify channels without proving internal reliability. Vikunja's inspected reminder file demonstrates scan/dispatch and local deduplication only. Asana's webhook contract combines retries with possible lost nonreplayable events. [A03](https://tasks.org/docs/notifications/), [T05](https://www.todoist.com/help/articles/introduction-to-reminders-9PezfU), [V18](https://raw.githubusercontent.com/go-vikunja/vikunja/main/pkg/models/task_reminder.go), [S10](https://developers.asana.com/docs/webhooks-guide). Claims C15–C17.

**Acceptance fixture.** Restart before/after claim, before/after send and before/after result persistence; inject provider timeout and duplicate callback; run two workers; change a date while leased; complete or revoke access while pending. Show the resulting state and reason. Enqueue success alone is not delivery acceptance.

**Tradeoff.** At-least-once attempts can duplicate external effects if providers lack idempotency. At-most-once attempts can lose messages after uncertain sends. Document this boundary, expose ambiguity, and allow reconciliation. Do not infer a whole-product guarantee from one code path or one retry policy.

## ADR-C08 — Dependencies independent of hierarchy

**Proposal.** Model directed blocking relations separately from decomposition and related/duplicate links. Detect cycles. Specify whether the edge affects visibility, completion eligibility, or scheduling; initially prefer availability advice with an explicit override policy. Do not automatically shift hard deadlines.

**Evidence.** Org provides ordered/child guards; OmniFocus sequential structure affects availability; Taskwarrior marks blocked tasks; Vikunja has typed relation/source validation; OpenProject separates hierarchy from schedule relations and calendar-aware lag. [G03](https://orgmode.org/manual/TODO-dependencies.html), [O10](https://support.omnigroup.com/documentation/omnifocus/universal/4.10.1/en/perspectives/), [W16](https://taskwarrior.org/docs/man/task.1/), [V21](https://raw.githubusercontent.com/go-vikunja/vikunja/main/pkg/models/task_relation.go), [P01](https://www.openproject.org/docs/user-guide/work-packages/work-package-relations-hierarchies/). Claims C18–C19.

**Acceptance fixture.** A parent has two independent children and one external blocker. Completing one child does not complete the parent or satisfy an unrelated edge. Exercise cancelled/skipped prerequisites, a cycle, a private blocker and recurrence across slots. Decide whether an edge points to a series, current occurrence or a fixed occurrence; avoid guessing.

**Deferred.** Critical paths, working-day lag, resource leveling and automatic dependency-driven date shifts.

## ADR-C09 — Views are queries, not owners or grants

**Proposal.** Save a versioned typed predicate with sorting, presentation and visibility metadata. Apply current viewer authorization. Specify nulls, undated items, lifecycle defaults, timezone, relative-time refresh and stable tie-breakers. Query text and visual builder should express the same structure. A view link should never silently change grants.

**Evidence.** Todoist documents saved filters and refresh behavior; Tasks.org defines Boolean grouping in its builder; Vikunja has explicit date-math timezone/null parameters; Linear says links do not grant access. [T04](https://www.todoist.com/help/articles/introduction-to-filters-V98wIH), [A07](https://tasks.org/docs/filters/), [V02](https://vikunja.io/docs/filters/), [L07](https://linear.app/docs/custom-views). Claims C20–C21.

**Acceptance fixture.** Saved query spans projects but shows different authorized results for two viewers. Rename a referenced project, delete a field, cross midnight, change timezone and revoke access. No stale title/count leaks. Explain unsupported expressions; do not silently drop them.

**Deferred.** Arbitrary executable query functions and complex subscription-trigger notifications until view evaluation semantics are stable.

## ADR-C10 — Offline is a contract, not a cache

**Proposal.** Declare whether the first release supports read cache, queued edits, or full offline collaboration. Preserve command identity, revisions, tombstones and a visible pending/error state. If server-authoritative initially, be candid. If offline writes are promised, define conflict policies before acceptance.

**Evidence.** Todoist documents reconnect synchronization; Linear documents restart-persistent pending edits but warns of overwrites; Taskwarrior warns against unsupported database file sync and documents per-member map representation for concurrent sets. [T18](https://www.todoist.com/help/todoist/features/use-todoist-while-offline-4rbaZw), [L12](https://linear.app/docs/get-the-app), [W06](https://taskwarrior.org/docs/sync/), [W17](https://taskwarrior.org/docs/task/). Claims C22–C23.

**Acceptance fixture.** Two disconnected devices edit title, add separate checklist items, complete the same occurrence and delete/reopen work. Reconnect in both orders. A revoked client cannot restore deleted or newly private data. Pending commands survive restart. Loss or rejected edits remain visible and recoverable.

**Deferred.** A CRDT or other distributed merge mechanism is not selected by this research. Data-specific operations and a server revision model may be sufficient; choose only after the offline requirement is explicit.

## ADR-C11 — Export preserves meaning or reports loss

**Proposal.** Provide a canonical versioned archive and separate projections for CSV/iCalendar. Include stable IDs, typed temporal values, series/occurrence history, relations, extensions, attachment manifests and appropriate principal references. Distinguish backup/restore from user-portable export and public sanitized templates. Emit import loss reports.

**Evidence.** Todoist documents exclusions from project CSV and active-project backups; Tasks.org excludes attachment files from backups and documents backend field differences; Linear excludes attachment files from CSV. [T19](https://www.todoist.com/help/account-and-billing/security/import-or-export-a-project-as-a-csv-file-in-todoist-YC8YvN), [T20](https://www.todoist.com/help/account-and-billing/security/download-or-restore-backups-in-todoist-ywaJeQbN), [A08](https://tasks.org/docs/backups/), [A02](https://tasks.org/docs/sync/), [L10](https://linear.app/docs/exporting-data). Claims C24–C25.

**Acceptance fixture.** Round-trip active/completed/cancelled/skipped work, nested tasks, attachments, recurrence anchor/exception, floating time, hard deadline, soft window and custom fields. Compare semantics and links. Export/import must not dispatch stale reminders or reactivate completed work inadvertently.

**Reject.** “Exports CSV” as proof of a full archive or backup. **Track.** Standards adapters with explicit capability and loss matrices.

## ADR-C12 — Scoped extensibility and adapter boundaries

**Proposal.** Expose a documented interface; use scoped integration principals and typed, namespaced extension fields. Keep authorization, recurrence, temporal precision and firing identity in the core. Imported data should retain external identity/provenance without treating external instructions as authority.

**Evidence.** Taskwarrior UDAs/hooks and Things local automation show distinct extension shapes; Asana custom fields and API show typed data surfaces; Vikunja's CalDAV warning and Tasks.org's sync matrix show adapter limitations. [W04](https://taskwarrior.org/docs/udas/), [W05](https://taskwarrior.org/docs/hooks/), [H13](https://culturedcode.com/things/support/articles/2967034/), [S11](https://developers.asana.com/docs/custom-fields-guide), [V14](https://vikunja.io/docs/caldav/), [A02](https://tasks.org/docs/sync/). Claims C26–C27.

**Acceptance fixture.** A read-only adapter cannot complete or share tasks. Unknown fields survive compatible round trips. Unsupported recurrence or date types trigger a loss warning. Duplicate webhook/import retries do not create duplicate tasks. Hooks/plugins cannot bypass scope checks through an alternate interface.

**Deferred.** In-process arbitrary third-party plugins, a broad marketplace and full protocol compatibility claims.

## ADR-C13 — Privacy and licensing are independent choices

**Proposal.** Choose deployment custody, encryption boundary, notification preview policy, export sanitization and source license explicitly. This comparison borrows concepts and does not copy implementation. If code is later reused, review the actual files, dependencies and assets under their applicable terms.

**Evidence.** Surveyed repository licenses include MIT, GPLv3 and AGPLv3; commercial service terms offer use rights rather than underlying source. OmniFocus documents remote sync encryption versus local OS protection; Tasks.org documents E2EE only for particular sync routes. [W11](https://raw.githubusercontent.com/GothenburgBitFactory/taskwarrior/develop/LICENSE), [A09](https://raw.githubusercontent.com/tasks/tasks/main/LICENSE), [V19](https://raw.githubusercontent.com/go-vikunja/vikunja/main/LICENSE), [L21](https://linear.app/terms), [O12](https://support.omnigroup.com/documentation/omnifocus/universal/4.10.1/en/database/), [A02](https://tasks.org/docs/sync/). Claims C28–C29.

**Acceptance fixture.** A public sample/export contains only synthetic principals and work; no endpoints, account identifiers, delivery targets or bearer capabilities. A private task does not leak through push/email preview or an unauthorized attachment link. Restore and account deletion have explicit custody behavior.

**Not decided here.** Daymark's license, formal compliance, legal interpretation of reuse, or a security certification. Those require separate decisions/evidence; documentation retrieval is not such an audit.

## ADR-C14 — Personal plans over shared obligations

**Proposal.** Try a personal planning overlay that can express “I will work on this Tuesday” without changing a shared deadline or everyone else's view. Keep optional personal reminder preferences separate from shared task obligations and authority.

**Evidence.** Three-date models establish the value of separating intention from constraint; Super Productivity's README describes personal local planning linked to external issue systems. It is suggestive product evidence, not a tested interoperable design. [O07](https://support.omnigroup.com/documentation/omnifocus/universal/4.10.1/en/outline/), [U01](https://github.com/johannesjo/super-productivity). Claims C06, C30.

**Acceptance fixture.** Two collaborators plan the same shared task on different days. Neither changes the task's hard cutoff, assignee or the other's private plan. A workspace-wide query clearly selects shared fields or the viewer's overlay. Export labels which layer is included.

**Reverse when.** The extra layer causes more confusion than value in actual workflows. Initial implementation may use a shared plan while preserving room for later overlays.

## ADR-C15 — Review and goals without mandatory methodology

**Proposal.** Provide review/neglected-work views and optional outcome links. Keep capture cheap. Do not force GTD vocabulary, organizational objectives, sprint cycles or quantified goal progress on all tasks.

**Evidence.** Official GTD guidance distinguishes action and higher-horizon thinking; OmniFocus has review/perspective mechanisms; Asana and Linear separate goals/initiatives from individual tasks/issues. [D01](https://gettingthingsdone.com/what-is-gtd/), [D02](https://gettingthingsdone.com/2011/01/the-6-horizons-of-focus/), [O10](https://support.omnigroup.com/documentation/omnifocus/universal/4.10.1/en/perspectives/), [S09](https://developers.asana.com/docs/goals), [L06](https://linear.app/docs/initiatives). Claims C04–C05.

**Acceptance fixture.** An individual uses only tasks; a group adds projects; another group adds goals and periodic review. Each can find neglected work without fabricated deadlines. Linking work to a goal does not imply completion proves the outcome occurred.

**Deferred.** Enterprise portfolios, resource management, formal OKR scoring and mandatory review ceremonies.

## ADR-C16 — Standalone engine, independent product surface

**Proposal.** Keep scheduling/domain behavior callable independently of the interactive UI. A standalone engine can own deterministic recurrence calculation, due firing materialization and delivery-state transitions, with durable persistence and visible ownership/leases. Separately choose how the UI and dispatcher are hosted. This is a proposed seam, not an assertion that any specific process topology is necessary.

**Evidence.** Linear distinguishes cached pending edits from backend synchronization; Vikunja's source demonstrates periodic reminder dispatch; Asana's webhook guide distinguishes events and transport limits. These support separating concerns, but do not prove any Daymark engine implementation. [L12](https://linear.app/docs/get-the-app), [V18](https://raw.githubusercontent.com/go-vikunja/vikunja/main/pkg/models/task_reminder.go), [S10](https://developers.asana.com/docs/webhooks-guide). Claims C16–C17, C22.

**Acceptance fixture.** With the UI closed, run the engine over a fixed clock and synthetic fixture; restart it and rerun the same interval. Stable occurrence/firing keys prevent duplicate logical records. A second engine cannot take the same live lease. Moving between embedded and standalone execution does not change domain results. Use an inert delivery sink until actual outbound delivery is explicitly authorized.

**Rejected inference.** Standalone means reliable, cron means unreliable, or serverless means stateless delivery is acceptable. Reliability follows the implemented persistence/recovery contract and tests, not the process label.

## Evidence gaps and reversal conditions

Do not close an unknown by copying a familiar UI assumption. The source inventory tracks unresolved API/filter coverage, offline/export/search gaps, timezone edge cases, cross-client preservation and reminder recovery. The current recommendations depend chiefly on documented differences rather than feature absence; most remain useful if a gap later reveals additional capabilities.

Revisit these decisions when the implementation brief changes materially: enterprise governance, full offline collaboration, regulated retention, cross-organization sharing, task exchange with a specific service, complex business-calendar scheduling, or E2EE with server-side reminders. Record the new requirement and evidence before widening the core. Passing a unit test, creating an outbox row, accepting a provider request and human receipt are different acceptance levels.
