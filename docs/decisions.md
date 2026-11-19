# Decisions for the first release

Decision date: 2026-11-09. The implementation owner chose these defaults under the
commissioner's delegated routine-decision authority. This records design choices;
it does not assert that an unfinished implementation has passed acceptance. The
[comparison ADRs](adr-comparison.md) retain the evidence and alternatives. Release
readiness follows `RELEASING.md` in the matching source checkout.
The [tagged release checklist](https://github.com/lavallee/daymark/blob/v0.5.0/RELEASING.md)
records the procedure shipped with 0.5.0.

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
| C11 Canonical export and loss reporting | Adopt canonical snapshots; defer third-party interchange | Native export/import preserves identities, versions, event history and operation replay receipts. Imports validate references atomically. Any application-specific downgrade must report/refuse semantic loss. The CLI checks that the input is readable JSON before opening the destination. No CalDAV/CSV/vendor round-trip guarantee. |
| C12 Extensibility and adapters | Adopt | Typed core fields plus structured metadata and source references. Application adapters and delivery sinks are separate; core does not import private producers. No plug-in execution from untrusted imports. |
| C13 Privacy/licensing | Adopt | MIT standalone source, synthetic examples, protected local state, redacted HTTP/worker errors. Source, dependencies and archives require privacy/security review. A successful secret scan does not establish absence of private data. |
| C14 Personal overlays | Defer | Planning fields currently belong to the shared record. Private per-person planning overlays need separate visibility/version semantics. |
| C15 Goals without mandatory methodology | Adopt basic kind; defer computed metrics | Goals and projects are optional explicit records; assignee and beneficiary differ. No mandatory household, GTD, sprint or OKR workflow. No implicit goal-progress formula. Amended by P13: a goal may carry one measure kept by hand, and its roll-up counts linked work; measured key results stay deferred. |
| C16 Standalone execution/deployment | Adopt | Embedded SQLite first, optional authenticated HTTP service/SDK/CLI. Supplied connections preserve the caller's unit of work; path-owned connections manage their own commits. Durable scheduler and restore drills are operational acceptance, separate from package build success. |

## Decisions for the agent surface

Decision date: 2026-11-09. These defaults were chosen by the implementing agent
under the commissioner's delegated routine-decision authority and are open to
reversal. [Work and life](work-and-life.md) gives the reasoning.

| Decision | Default chosen | Contract and limit |
|---|---|---|
| A01 One readiness rule | Adopt | Ready means open, unarchived, start date arrived, all dependencies completed or cancelled, no unfinished child task, and no live claim by another actor. The next round of a repeating task also waits for its planned day. Every surface uses `views.ready`. |
| A02 Cancelled prerequisites | A cancelled or archived dependency releases its dependents | Resolves the question C08 left open. The alternative, requiring an explicit unblock, strands work behind tasks nobody will do. |
| A03 Containers | A task with an open or waiting child task is not ready; a proposed child blocks nothing | Work the leaves. A parent becomes ready when its children are finished; it never completes automatically (C02 stands). |
| A04 Claims | Advisory lease, default 60 minutes, at most seven days | One live holder; the same owner may renew; completion, cancellation and archiving clear it. Not access control. |
| A05 Proposals | `proposed` status with `accept` and `dismiss` | Never ready; cannot be completed, skipped, reopened or moved to another status by update; its reminders stay pending but are not dispatched. Who may accept is set by A18. |
| A06 Next move | Optional `person`, `answer` or `agent`; never inferred | Unset matches no `--move` filter. |
| A07 Notes | Append-only, through the `note` action only | At most 1,000 per entity. Undo keeps notes and the current claim. A new round of a repeating task starts with none. |
| A08 Priority direction | 1 is most important, 4 least, 0 unset | Follows iCalendar. Previously undefined; ordering in `ready` now depends on it. |
| A09 Date input | Fixed grammar; anything else refused | No natural-language interpretation (C03 stands). The phrase used is stored as `expression` beside the resolved value. |
| A10 Readable identifiers | Short `dm-xxxx` IDs from the plain verbs; `series.N` for rounds of a repeating task | A round falls back to the hashed identity when `series.N` is or was used by an unrelated entity, or exceeds the ID limit; a slot that was ever materialized is never materialized again. Databases written before this change keep their hashed successors. |
| A11 Plain verbs and versions (amended) | By default `Tasks` reads the current version and retries stale reads. `edit` accepts a caller-observed version. | Plain verbs verify replay using recorded actor/action/target, the original before-state and the kernel request digest. Changed kernel writes under the same ID conflict. Existing operation receipts and event records carry the proof. An optional review token fingerprints the workspace, entity event and its recorded contents, preventing a prior read from authorizing a replacement record at the same version. Native history with no matching current event yields no token. |
| A12 Per-write validation | Each write validates its own references through derived lookup tables | Replaces whole-workspace validation on every write; snapshot import still validates the whole workspace. A randomized test checks the two agree. The 100,000-entity bound now applies to import only. |
| A13 Journal mode | Path-owned stores use WAL with default synchronous settings | One durable sync per commit. Caller-owned connections keep their own mode. Creates `-wal` and `-shm` files beside the database. |
| A14 Stored-shape upgrades | A schema marker triggers an in-place rewrite of stored entities when fields are added | Versions and event history are untouched. Snapshots exported by this version contain fields that 0.1.0 rejects. |
| A15 Error text | Domain failures print their stable code and rule; validation names fields without values | Other failures keep the generic message so stored content cannot reach logs. |
| A16 Database location | `--db`, `DAYMARK_DB`, nearest `.daymark/`, legacy `./daymark.sqlite3`, then the per-user database | The plain verbs refuse to run without a database and name `daymark init`. The engine commands keep creating `./daymark.sqlite3` as before. |

| A17 Actor on events | `execute(..., actor=)` is recorded on every event it causes | Scheduler and worker events have no actor. The HTTP service records the token's principal and overrides claim owner and note author with it. Snapshots carry the actor; events imported without one read as unattributed. Operation receipts do not include the actor. |
| A18 Decider policy | A workspace may name who can `accept` or `dismiss`; empty means any writer | Stored beside scheduler state, set locally (`daymark policy`), not exported in snapshots and not settable over HTTP. Enforced against whatever actor name the caller supplies: authenticated over HTTP, self-declared locally. It does not stop an actor creating open tasks directly. |
| A19 Private files | A database the command line creates is mode 0600 | Applies to every command, including the engine commands' legacy `./daymark.sqlite3`. |

| A20 Focus | Optional `focus` rank: the person's own short list, lowest first | Leads `ready` ahead of every computed order. Set only at the person's request; cleared when a task is completed, cancelled or archived. Ties fall back to the usual order; ranks need not be contiguous. |
| A21 Scope | Optional `scope` slug, a filter within a workspace | `ready`, `agenda` and `summary` accept a scope, or the empty string for unscoped items. It is never a boundary and is never inferred. |
| A22 Workspace isolation, stated | A workspace is the only boundary inside a database | Reads, references, history, exports, remembered facts and policy never cross. `Store.workspaces()` and `daymark workspaces` list them for local callers; the HTTP service exposes only the caller's own. |

## Decisions for projects

Decision date: 2026-11-13. Chosen by the implementing agent under the
commissioner's delegated routine-decision authority, open to reversal. A project
can be as plain or as structured as the work needs: each piece below is optional
and independent, and a bare project behaves as before. They also give an
application what it needs to show and edit a project with Daymark as the system
of record. C08 and C11 stand: this is a stored model with a read and write
surface, not a scheduler, and there is no third-party interchange guarantee.

| Decision | Default chosen | Contract and limit |
|---|---|---|
| P01 Stages | A project may define up to 30 ordered stages `{id, label, status, color?}`; a task has an optional `stage` | Lifecycle status stays the truth for readiness; the stage is only where the card sits. A defined set needs at least one open and one completed stage. Colours come from a fixed thirteen-name vocabulary. |
| P02 Default board | A project with no stages reads as To do (open), Waiting (waiting), Done (completed) | Derived, not stored: a task with no stored stage reads in the first stage for its status, so existing projects read as boards with no rewrite. |
| P03 Stage and status, both ways | Moving to a stage sets the task's status to the stage's; a status change by any verb clears a stored stage that no longer fits | Complete, cancel, skip, reopen, wait, resume, accept, dismiss and undo all land the card in its status's first stage. A waiting task may stay in an open stage when the board has no waiting stage. A cancelled task with no cancelled stage is off the board. A proposal sits in no stage and is listed apart. |
| P04 Changing stages and projects | Removing a stage, or changing its status, clears the stage of the tasks it no longer fits | Their status never changes; each such task gets a version and a `stage.reset` event in the same transaction. A task that moves to another project loses its stage (unless the write sets one) and its milestone membership, and goes last there; its type and custom values stay and are reported if the new project does not declare them. Undoing a configuration change is one unit: it also restores the stages it cleared on tasks nobody has written since (`stage.restore`). |
| P05 Manual order (amended) | One order per project: a `rank` key (fractional indexing with an integer part, base 62, compared as text), set only by the `place` action | A task never placed reads as its creation time, so new tasks go last and a placement writes only the moved task: no mass ranking, no version change on tasks nobody touched. Appends and prepends grow keys logarithmically; repeated inserts at one spot add about a character per six. `create` and `update` refuse a rank. Archived tasks keep their place. A new round of a repeating task goes directly after the round it follows. Equal ranks in damaged data still allow a placement. Children are listed in project order under their parent, so a reorder within an epic is a placement next to a sibling. Order is total: by key, then by ID. Placing between tasks that share a key re-keys only the tied tasks after the insertion point; an undo that would restore a key another task now holds moves just past it; a key longer than 128 characters re-keys the whole project evenly. Every key is validated on the way in (including import). `create` refuses `created_at`, which orders tasks never placed. |
| P06 Milestones (amended) | A milestone is a task marked `milestone`, dated by its own dates; other tasks may name it in `milestone_id` | Membership must point at a milestone task in the same project (not itself). The plan reports each milestone's progress, field totals, items ending after its date (`late`) and whether all its items are done. A milestone is reached only when someone completes it, never automatically. Removing the marker later keeps memberships and reports them. A milestone does not belong to a milestone. One that moves to another project releases its members, each visibly (`milestone.detach`). Every unarchived milestone is listed with its status, including a proposed or cancelled one. |
| P07 Plan dates | start = `planned`, else `earliest_start`; end = `latest_target`, else `deadline`; the deadline is also given on its own | `plans.schedule` writes start to `planned` and end to `latest_target`, so a plan move never moves a deadline; a deadline changes only when written as itself. A date equal to the current one changes nothing, so a timed value keeps its time; a moved timed value keeps its clock time. Only dates being written are checked; a start after an end, or a target before `earliest_start`, is refused, never clamped. Clearing a plan's start clears both `planned` and `earliest_start`, so it reads as unset; clearing its end clears `latest_target`, and a deadline still shows through as the end. |
| P08 Plan read and project feed | `Store.plan` reads a project in one transaction: stages, types, field declarations, tasks in order with resolved stage and dates, in-project dependencies (and outside ones), milestones and parents with roll-ups, facts, health, warnings and the head cursor | `Store.changes(project, after)` reads the head and project membership in one SQLite snapshot. It also notices changes to what the project's tasks depend on outside it, its goal, and any person, label or goal it can name. A cursor ahead of the head reads as changed. `Store.projects` reads the workspace once. One blocker rule everywhere (A02): a completed, cancelled or archived dependency, inside the project or outside it, releases its dependent. Date conflicts are reported for live work only. |
| P09 Batch writes (amended) | `Store.batch`: up to 100 operations in one transaction, each with its own operation ID, expected version and receipt; optional `request_id` and `request` | `atomic=True` writes all or nothing and reports the rest `aborted`; `atomic=False` rolls back only the refused ones. With a request ID, a committed batch replays as a unit when the same request is sent again, and a different request under the same ID is a `conflict`. Operation IDs must be text. |
| P10 Externally owned projects | An optional `managed_by` on a project names the system that rewrites it | Advisory: Daymark accepts writes as before; applications that edit plans should treat the project as read-only. Never inferred. |
| P11 Assigning by name | `--assignee` takes a person's ID or exact name, ignoring case | An unknown name is refused, not created; several matches are refused with their IDs. Workspace isolation (A22) is unchanged. |
| P12 Item types and hierarchy | A project may declare up to 20 types `{id, label, color?, container?}`; a task has an optional `type`; hierarchy stays `parent_id` | A type is checked against its project when written. Each parent's children roll up (counts by status, percent done, estimate, span, field totals); `tree` reads to a depth. A child of a non-container type, or a parent outside the project, is a warning, never a refusal. A3 stands: a parent with open children is not ready. Every parent in the project rolls up its children whatever its own status, and the roll-up carries that status: a cancelled epic's children are still work. |
| P13 Goals (C15 amended) | A goal has a target date (its plan end), its status, and an optional single `measure` `{label, target, current?, unit?}` | `goal_plan` rolls up the projects linked by `goal_id` and tasks linked directly: progress, counts, and live items at risk (overdue, or ending after the target). The measure is kept by hand: no formula, history or key-result tree. |
| P14 Custom fields | A project may declare up to 50 typed fields `{id, label, type, options?, unit?}` (text, number, option, options, date, bool, person, url); a task keeps `values` by field ID | Only values a write changes are checked; an unknown ID or wrong type is refused with the declared list. Removing a declaration or an option keeps every value and reports it as a warning. Filter with `values.ID`; number fields are summed in roll-ups. Points, cost, risk or an iteration are fields, not concepts. An `update` carrying `values` merges per field on every path (null clears one). Numbers are finite; whole numbers are exact within ±(2**53 − 1); anything else is refused. |
| P15 Presets and configuration | Five presets (simple, kanban, milestones, epics, event) write stages, types and fields once | A project never records or enforces a preset. Its configuration (stages, types, fields) reads and applies as one JSON document, part by part, so it can be reshaped in one reviewed write or copied. |
| P16 Project facts and health | A project's dates, lead (`assignee_id`), status and goal are facts; health is counts | Overdue, blocked, unowned, past a milestone, date conflicts, warnings, span and items beyond the target. No score, no colour rating. |
| P17 Dependency facts | Finish-to-start, no lag (C08 stands) | The plan lists each item's open blockers and, as facts, items that start before a blocker ends. Nothing is rescheduled. |
| P18 Iterations | No sprint entity | A time box is a milestone (dated; work counts toward it) or an option field. |
| P19 Schema upgrades are one-way (amended) | Commands opening a writable Store upgrade the file and announce the first upgrade on standard error; the library's reads never upgrade | Back up with the release you are leaving, before installing the new one (`daymark backup` copies without opening a Store). A newer schema is refused, never downgraded. Library reads on a schema the caller has not upgraded refuse with one `conflict` message. `Store(path, upgrade=False)` refuses an old schema without writing; a read-only connection on an old schema gets `conflict`. |

## Decisions for the kernel and strategy split

Decision date: 2026-11-15. [Architecture](architecture.md) gives the design and
[the research digest](research/working-methods.md) the evidence.

| Decision | Default chosen | Contract and limit |
|---|---|---|
| S01 Three layers | Kernel, strategies, plugins | The kernel stores facts and enforces invariants. Ways of working live above it and can be swapped per workspace. |
| S02 Strategies advise | A strategy returns an ordering, notes and suggestions; it never writes | A suggestion becomes a task only through `propose`. A test forbids the strategy package from importing the engine. |
| S03 Evidence labels | Every strategy states clinical, practitioner, folk or convention | Shown by `daymark strategies`. A label describes the idea, not Daymark's implementation of it. |
| S04 Plain is the default | With no profile chosen, `advise` uses the eligibility and order of `ready` | Optional waiting checks honor the recorded earliest start and use elapsed time since the last task update. The ordinary ready page defaults to 20 rows and advice inspects at most 1,000. A person explicitly chooses further changes to the order, limits or wording. |
| S05 Profiles per workspace | Stored as a workspace setting | Settings and their local replay receipts belong to workspace configuration. Entity history and exports omit them. Current HTTP services read the authenticated workspace’s active or named configuration and evaluate built-in stages. Older services use a labeled plain fallback. Preference mutations remain local. A stored entry that can no longer be loaded is skipped and reported. Independent edits preserve its position and configuration until explicitly removed. |
| S06 Plugins load by name | Entry points in `daymark.strategies`, imported only when a profile names one | Installing a package never runs its code by itself. No hook may rewrite a write. |
| S07 Conditions | A `condition` kind with a judged state; its status follows the state | A condition belongs to a goal, cannot be proposed, parked or repeated, and a write that sets its status against its state is refused. "Met" reads as completed, so existing dependency rules apply unchanged. Reopening a met condition marks it unmet; bringing back an archived or dropped one keeps its assessment. Frontier and gap are kernel facts; choosing the bottleneck is a strategy's judgement. |
| S08 Kernel admission | A field enters the kernel when three independent methods or clients need it stored, or an invariant depends on it | Otherwise it is plugin data. Existing fields that fail this test are listed as debt in the architecture document. |
| S09 Personal adjustments | A temporary instruction applies to one advice request. Named copies and active defaults are explicit workspace settings. | Named copies retain strategy order and parameters, support configuration export, and check their version on replacement or removal. Active settings have a separate revision. Each preference write and its replay receipt commit in one local transaction. A version-guarded undo restores the active setting preceding a change. Configuration receipts stay outside entity exports. |

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


## Starting a session

The full MCP primer describes native tool arguments. The CLI primer describes
shell commands. Initialization names the first scoped read so a request about
one setting can stay there from the start. [Cold client comparison](research/mcp-client-trials.md#choosing-the-first-read)

Show the current workspace, actor and live work before the command guide.
A returning session can opt into a brief view. Over HTTP the credential selects
the workspace and actor. Ready counts and task listings use the same claim
perspective, including work claimed by the current actor. The count includes
every matching task, even when only the first page is displayed.


## MCP results and retries

Clients negotiate a supported, dated protocol revision. Invalid requests return
JSON-RPC errors. A well-formed tool call that Daymark refuses returns an error
tool result so the agent can correct it. All write tools accept an operation ID.
Revisions with structured results expose the committed ID and version alongside
the existing human-readable line. A receipt describes the original write.

### Taking work from an agent queue

`take` uses the canonical ready order and selects only an unclaimed task
whose recorded next move is `agent`. This lets a worker request one piece of
authorized work without treating every open commitment as delegated. A named
local actor or an authenticated HTTP principal owns the claim. Selection,
the version check on the selected entity, and its ordinary claim event
share one database transaction. The workspace-level request uses expected
version zero because it names no entity. Its receipt binds the actor, scope, context
and lease duration to the selected task or the empty result. Replaying a
selection cannot claim more work.


## Initializing a project folder

`init --here` selects the current folder explicitly. If an explicit database
setting points elsewhere, the command reports the conflict before creating
either database. In a project store, Daymark writes ignore patterns for its
SQLite file and SQLite sidecars when the directory has no ignore file.
The project owner controls existing ignore rules. Project instruction updates
remain opt-in and preserve the text around Daymark's marked section.

Ordinary workspace display commands require a recognized Daymark file.
Previously a typo created an empty database, and a read of another tracker’s
SQLite file installed Daymark tables there. An explicit first write can still
initialize a new file. Recognized older Daymark databases follow the usual
upgrade rules.

### Accepting a proposal with a recorded wait

A proposal created with `--waiting-on` records a dependency on an outside
person or event while the proposal awaits a decision. On acceptance, its
status becomes `waiting`. Without a recorded wait, acceptance makes it
`open`. This preserves the same actionable state as direct capture with
the same fields. `accept` and `dismiss` accept an optional reviewed-version
precondition across the plain verbs, CLI and MCP.


## Reading by context

A context filter selects explicit membership in a context in the current
workspace. Filter before ranking and limiting. Prerequisites, child work and
claims elsewhere in the workspace still determine eligibility. The ordinary
ready list includes untagged work. A missing context ID produces a diagnostic
so the caller can discover the available IDs.


## Recording a waiting reason on existing work

Setting a nonempty `waiting_on` on an unarchived open task takes it into
waiting in the same edit. A waiting task can have a reason added, changed or
removed. Resuming is an explicit action. Editing a reason preserves a
proposal, a closed task and an archived task in their existing state. The
engine applies this inference after validating an operation receipt so that
an identical retry of an earlier release's edit returns the recorded result.


## Retiring remembered facts

Memory remains shared workspace context. A recorded scope gives the fact
an area of applicability and is visible in the startup preview. A source
names where it came from. `forget` checks the target is a memory in the
same transaction as archiving it. A review token or expected version can
guard a retirement. Ordinary archive receipts for memories from earlier
versions still verify and replay. Archive retains the record and its history.


The contributor source distribution includes the command guides, skill and
verification scripts. An installed CLI prints its matching agent skill with
`onboard --skill`. The project onboarding snippet asks agents to keep other
trackers authoritative for the commitments they already own.


## Resuming after a compact startup

Compact startup includes a portable checkpoint bound to the workspace and
the event at the returned cursor. Checked activity paging rejects a missing
or different boundary, including a workspace switch. It can resume after an
intact native transfer. Integer cursors and their response shape remain
available. Native import can merge materialized entities independently of
events, so a fresh read of affected records is required after such a merge.


## Interpreting a one-off local clock reading

An explicit Daymark timezone (Python, `--tz` or `DAYMARK_TZ`) must be an IANA
zone. A typo previously let the environment setting silently select the host
zone. This could attach a reminder to a different instant. The CLI now gives
an actionable error before capturing work. Host timezone discovery still
handles systems whose generic `TZ` value is a POSIX expression.

An explicit local clock reading denotes one instant. If a timezone
transition skips it, the person chooses an existing time. If the clock
repeats it, the person chooses the numeric offset. A dated input with an
explicit offset already identifies an instant. This applies to absolute and
relative inputs with clock times, including the supplied anchor of a series.

Recurring rules continue to advance through a gap and select the first
occurrence of a repeated slot when generating future occurrences. Existing
operation receipts retain their original interpretation, including the
historical handling of gaps and repeated hours.


## Restoring a recorded prior state

Entity history uses the workspace event number preserved by native export.
Undo requires the latest event to describe the current materialized record,
or a matching deletion to describe the absent record. Older event shapes
are compared after applying today's defaults. A sparse restore that lacks
this evidence needs the corresponding native events or an explicit edit.

## Setting revisions across schema 6 clients

v0.5.0 and the development version share the same SQLite setting rows.
SQLite triggers increment the revision on insert, changed value and delete,
even for an older writer. A deletion retains the revision so a later
recreation cannot reuse an old version. Entity-only readers can open an
untouched v0.5.0 file. The first writable open installs tracking in the
owner's transaction before guarded settings reads become available.


## Undo after a concurrent change

Undo reverses one verified latest event. If another write wins between
reading that event and trying to undo it, Daymark reports a conflict.
Automatically repeating the action against the new head would reverse a
different event. A matching successful receipt still replays exactly.
For an earlier field change, the caller can inspect history and submit a
guarded edit after checking intervening work.


## Restoring visibility

Unarchive is a plain verb for a versioned update of the archive flag. It
retains lifecycle status, completion time, waiting reason and source identity.
A fresh restoration of a visible record returns a conflict. Identical retries
return their original result. A fresh acceptance requires a visible proposal.
Existing accepted-operation receipts replay and the decider policy still
governs both acceptance and dismissal. Unarchive uses existing record and
event fields, including the recorded update in activity history.

A delayed wait or resume refuses finished and archived work at the version
being changed. Restarting a finished task requires an explicit reopen.
Receipts for earlier successful waits and resumes still replay.


## Correcting a recurring occurrence

Fresh skip and completion of a recurring round require a visible unfinished
occurrence. A one-off task can be cancelled. A successful skip from an older
release remains replayable under its original operation ID. Reopening or
undoing the outcome of an earlier round preserves every materialized later
round, with its distinct occurrence identity, notes, claims and reminders.
The next slot already reserved by a later round stays reserved after a
correction or deletion.


A new recurring task without an explicit first date uses the first rule
match on or after the current local day. The same default applies when an
undated one-off joins a series. An explicit first plan or deadline is
preserved and counts as the first occurrence even if it falls off-pattern.
For Tuesday January 1, 2030 followed by Fridays with COUNT=2, the final
round is Friday January 4. Existing materialized records and receipts
keep their dates. [Calendar trial](research/recurring-work.md)

## Starting from a terminal

Running `daymark` with no arguments prints a compact starting guide and
exits successfully before database selection. It introduces folder-local
setup, pending proposals, session pickup and command-specific help. Full
`--help` keeps the parser-generated command and option index. Commands
missing their own required arguments retain their usage errors.

The guide occupies 17 lines at 80 columns and also appears at the end of
full help, where an ordinary terminal leaves its cursor. In a synthetic
80×24 terminal, fresh Claude Code sessions saved one pending proposal in
the intended folder on v0.5.0, the development baseline and the revised CLI.
They used 21, 21 and 15 command or scroll calls respectively. With scrollback
unavailable, separate baseline and revised sessions used 20 and 10 commands.
Both found the proposal again. The counts describe these individual agent
sessions.

Local health, policy and export commands also require an existing database. A
misspelled path previously produced a zero-item export or a decision policy naming
no restrictions. Explicit initialization preserves the mode of an existing file.


## Guarding local decider policy edits

A policy handover replaces the full decider list. An editor working from the
previous list can overwrite that handover by applying their earlier review.
The opt-in local workflow carries the observed version and a stable operation
ID. A retry returns the original write result. The default CLI output and
unconditional writes remain available to existing callers.

The policy row keeps its existing format and remains authoritative. SQLite
tracks its revision even when an older client writes it. Existing local
configuration receipts provide replay without a schema marker change. The
native backup covers this state. Logical workspace imports keep destination
authority and local policy receipts.


## Questions, watching and follow-ups

A review of one deployment's records showed where shared work fails. Most
questions put to a person through an agent ledger were answered in
conversation and closed by the asker, not answered in the ledger. Most open
items belonged to a session that no longer existed. Some answers given after
a long wait reached no agent. A hosted agent message board
([Vercel Board](https://vercel.com/docs/board/concepts/tasks), 2026-11)
supplied the mechanisms to compare against.

| Choice | Decision | Why |
|---|---|---|
| Where a question lives | In `metadata["daymark.question"]`, with its wording and the answer as notes | Four clients store a question with options and an answer, which meets the admission rule (S08). It stays plugin data for one release so no embedding application needs an upgrade; promote it to a typed field with the per-kind record split (K1). |
| Asking gives the task up | `ask` releases the claim and sets `next_move` to answer; `answer` restores the earlier `next_move` | The answer must not depend on the asking session surviving. Any worker can take the task and read the thread. |
| Who may answer | Anyone but the asker; the asker may relay a named person's answer, recorded as relayed | Most answers arrive in conversation, so a rule that forbade relaying would be bypassed. An actor name is still a label, not proof. |
| Waiting | `watch` reads the event log after a cursor and returns the last event examined | A cursor, not a subscription: nothing has to stay running, and a change made before the watcher started is not lost. Own writes do not wake the watcher. A timeout is a distinct result with exit 8. |
| Follow-ups | An ordinary agent task with an earliest start, created by `followup` | A reminder needs a person and a delivery channel. Ready work needs neither and survives the session. Intervals use `min`, `h`, `d`; a bare `m` is refused because `+Nm` already means months. |
| Claims stay advisory | No fencing of a lapsed holder's writes yet | A person must be able to finish a task an agent holds. `--if-version` already fences a worker that keeps the version from `take`. Revisit if two workers are seen settling the same task. |
| Not adopted | Channels, free-form messages between agents, two-way sync, per-channel grants | Messages hang off a task. Other systems are linked with a source reference, not mirrored. |
