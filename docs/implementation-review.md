# Implementation mechanisms worth learning from

Evidence date: **2026-11-09 UTC**. Scope: **8 upstream repositories**, inspected as exact-commit static snapshots. No assessed package was installed, imported, built, or executed. Upstream test assertions were read, not run. The accompanying [implementation source inventory](implementation-sources.json) records full commits, licenses, file hashes, line ranges, symbols, observations, limits and proposed regressions. This report complements the [semantic comparison](comparison.md); it does not replace its product-level evidence.

Daymark should retain its existing bounded dateutil dependency, keep its own temporal policy and durable state authoritative, and borrow **independent regression ideas** before adopting more infrastructure. The most useful mechanisms are captured-version acknowledgments, immutable nominal recurrence slots, explicit cursor serialization, input-preserving expansion, batch graph validation, transaction-aware side effects, and generated operation sequences checked against a small reference model. No inspected scheduler supplies a transferable guarantee of exactly-once external delivery or same-owner ABA fencing merely by having leases.

The immediate handoff is a compact set of synthetic tests for the core, temporal and API/worker owners. Research does not require their build to wait. Findings and tests below are **proposals**, not declarations of Daymark acceptance. Separately reported implementation progress is outside this static review's verification boundary.

## Local baseline and decision boundary

At the start of this pass, the local Python project already declared dateutil and Pydantic, represented date-only versus aware temporal values, separated fixed from completion-relative recurrence, and carried occurrence budgets. Its documented policy moved nonexistent local times forward and selected the first fold for ambiguous times. The worker used stable logical delivery keys and a recording sink; persistence work was underway. These observations identify owners and adaptation points, not a complete audit of a changing implementation.

The core is general-purpose. A home application can be its first client without encoding household identities, data, schedules, endpoints, or product-specific integration ownership into the library. All examples in this report are synthetic. No upstream code or fixtures have been copied into Daymark by this research worker.

Evidence labels are deliberately narrow:

- **Source-observed:** the specified code path contains the described operations. Other paths, concurrency assumptions and deployment behavior are not automatically covered.
- **Test-observed:** a test contains the stated fixture and assertion. This is evidence of intended regression coverage, not a passing test result.
- **Source claim:** a docstring, comment or metadata states an intention. It is separated from executable observations.
- **Inference/proposal:** an adaptation or new independent test for Daymark. Its authority is the local requirement plus the cited mechanism, not a claim that upstream guarantees it.
- **Unknown:** not established in the inspected scope. Missing evidence never means unsupported. An explicitly expected-failing upstream test is not counted as a demonstrated working behavior.

Retrieval used shallow read-only Git snapshots and static file reads/search. Git object identity pins the reviewed tree; file SHA-256 values record inspected bytes. Neither is a signed release attestation. Default branches can include unreleased work. In particular, APScheduler's inspected history begins with an unreleased section, and Org's source identifies a prerelease. No source-release-to-installed-version parity is claimed. [APS-VERSION], [ORG-REPEAT]

## Repository selection and reuse posture

All full commit hashes and commit dates are in the JSON inventory. Short hashes below are labels, not floating branch references.

| Repository | Pinned commit | License evidence | What to take now | Dependency disposition |
| --- | --- | --- | --- | --- |
| python-dateutil | `a78cbd81d5d0` | Metadata says BSD-3-Clause; LICENSE retains historical Apache/BSD notices. [DU-LICENSE], [DU-METADATA] | Existing calculation boundary; timezone/cursor/finite-rule tests | Retain the existing dependency under Daymark validation; this review does not authorize upgrading to the inspected development snapshot |
| recurring-ical-events | `7a0bd1d637e4` | LGPL-3.0-or-later metadata and license. [RI-LICENSE], [RI-METADATA] | Original-slot identity, input immutability, exception/range test matrices | Track for a future calendar-import adapter; reject adding it as a second core recurrence authority now |
| Taskwarrior | `efa603a5d60d` | MIT. [TW-LICENSE] | Template/index reasoning, import normalization and round-trip fixtures | Borrow concepts/tests; no C++ engine dependency |
| Vikunja | `55f5418b0e4f` | AGPLv3 text; inspected headers permit version 3 or later. [VK-LICENSE], [VK-REPEAT] | Constant-time elapsed catch-up, graph prevalidation, transaction/effect tests | Test ideas and independent mechanisms only; no embedding/copying implementation |
| Tasks.org | `3f57c2ae6cc3` | GPLv3 repository license. [TO-LICENSE] | Captured-version acknowledgment, typed recurrence, preservation and merge fixtures | Independent tests and contracts; no Kotlin/Android dependency or copied code |
| Org | `cf41132d0b21` | GPLv3 text; source header permits version 3 or later. [ORG-LICENSE], [ORG-REPEAT] | Controlled-clock fixtures, explicit catch-up semantics, visible known failures | Borrow testing distinctions; reject interactive editor machinery as server implementation |
| APScheduler | `05b4e2d69f8` | MIT. [APS-LICENSE] | Lease boundaries, recovery categories, coalescing matrix, resumable trigger state | Borrow patterns/tests; defer dependency adoption until it solves a demonstrated execution need |
| Hypothesis | `9ab0be61aaaa` | MPL-2.0 in both package metadata and license at this snapshot. [HY-LICENSE], [HY-METADATA] | Stateful action traces, shrinking, fold-aware generated values | Try a development-only dependency through a bounded harness decision; no production dependency needed |

Licensing is a reuse boundary, not a product score. Conceptual invariants and newly written synthetic fixtures can be implemented independently. Copying code, substantial test bodies, data fixtures or documentation requires a separate file/dependency/license review. Permissive code still has notice obligations. This report makes no legal compliance determination.

## 1. Dateutil: retain a parser/calculator, own the surrounding policy

**Source-observed.** `rrule.__init__` fills defaults, normalizes dates and enforces some timezone compatibility. At this snapshot, simultaneous COUNT and UNTIL emit a deprecation warning; they are not universally rejected by that constructor. The emission loop stops when a candidate is after UNTIL and decrements count on emitted candidates. This supports an inclusive-boundary test, while also showing why Daymark must enforce its narrower input contract itself. A familiar parser is not an input-budget, finite-rule or application-state validator. [DU-RRULE], [DU-RRULE-TEST]

`datetime_exists` checks whether wall time survives a round trip through UTC. `datetime_ambiguous` compares fold-dependent offsets, and `resolve_imaginary` shifts a nonexistent time by the change in offsets around the transition. Its tests include a whole-day timezone jump and a historical 44-minute-30-second change. Separate tests assert that resolution leaves ambiguous values unchanged under either fold. Therefore Daymark's first-fold policy is its own choice; calling the gap resolver does not implement that policy by itself. [DU-TZ], [DU-TZ-TEST]

**Test-observed.** Property tests generate finite rules, vary disabled/empty/partial/complete caches, and compare continuation, count, representation and unaffected original iterators after copying or serialization. This is more useful than a single “serialization works” example. The generated timezone choices in that particular property file are naive/UTC/fixed-offset cases, so it is not a complete DST property harness. [DU-PROPERTY]

**Borrow mechanism:** bounded parser/calculator delegation plus explicit policy checks before and after it. **Borrow tests:** inclusive UNTIL; COUNT boundary; unusual gaps; both folds; continuation after partial materialization. **Reject inference:** dateutil's RFC-oriented documentation or a successful parse proves Daymark's completion-relative semantics, expansion bounds, normalization policy or persistence behavior. **Track:** release parity before relying on a newly observed snapshot feature or fix.

**Daymark adaptation.** Generate nominal slots first, apply the declared gap/fold policy, then preserve original slot identity and compare actual instants in UTC. Keep COUNT consumption and completion-relative anchoring in Daymark state. Test an impossible rule, a very old anchor and the search-budget boundary independently of the parser. Prefer an operation/iteration sentinel over a fragile elapsed-time assertion. Do not use pickle as an interchange format merely because upstream tests use it for trusted object copies.

**Cost/reversibility.** Retaining one existing dependency is low integration cost. Reimplementing its calendar algebra would increase maintenance. A small policy wrapper and independent vectors make a later version change reviewable; they do not justify changing versions automatically.

## 2. Recurring-ical-events: original identity and compatibility are separate concerns

**Source-observed.** The series implementation indexes modifications by recurrence identity and chooses between competing adapters by sequence. Helpers normalize recurrence identities into comparable forms. The compatibility path also repairs certain malformed UNTIL representations before calling dateutil and removes negative COUNT syntax in its parser path. These are adapter decisions aimed at imperfect external calendars, not appropriate default behavior for every engine's public write API. [RI-SERIES], [RI-UTIL]

**Test-observed.** One regression explicitly mutates an exported occurrence's DTSTART and checks that RECURRENCE-ID remains the original slot and is not the same mutable property object. Another varies date/floating/UTC/zoned range moves, checks the original recurrence identifiers, and asserts that expansion leaves the input calendar unchanged. A single-occurrence edit after a moved range remains local to that slot. Count tests include exclusions and UNTIL before the start. [RI-ID-TEST], [RI-RANGE-TEST], [RI-COUNT-TEST]

**Borrow mechanism:** separate the nominal slot from displayed/edited execution time and keep pure expansion input-preserving. **Borrow tests:** move a slot, expand twice, export/reimport, and verify stable original identity and no extra occurrence or reminder. **Track dependency:** an interoperability adapter if actual iCalendar import needs exception/range handling. **Reject now:** a second core recurrence engine, silent repair of malformed core commands, or treating highest sequence as same-identity/different-content conflict detection. The inspected selector is not a content-hash replay ledger. [RI-OCCURRENCE], [RI-UTIL]

**Daymark adaptation.** A moved plan should not change series/occurrence identity or cause a scheduler retry to create another successor. Preserve the original nominal slot in snapshot exports. If duplicate imported event/operation identity carries different content, reject or explicitly reconcile; do not use a calendar sequence heuristic to overwrite a newer local command result.

**Limits.** These tests target calendar component semantics. They do not prove workspace authorization, an outbox, lease fencing or durable command replay. A test file with an old issue description saying something “breaks” is read together with its actual assertions; it is not reported as a live failure.

## 3. Taskwarrior: materialization identity and import equality, with semantic traps

**Source-observed.** `handleRecurrence` uses a template's mask and generated index to identify which instances need materialization, assigns a new instance UUID and a parent reference, and updates the template. `generateDueDates` limits future lookahead, but its loop advances through historical dates before the future counter increases. That is not proof of a bound on total catch-up work. The monthly branch in `getNextRecurrence` clamps an invalid day down; the weekday branch uses elapsed-second arithmetic. These choices must not be treated as identical to an anchored RRULE that skips invalid month days. [TW-RECURRENCE]

The import path normalizes generated/default timestamps against an existing task before comparing content. The tests verify repeated identical task import leaves export unchanged, warn on duplicate UUIDs within input, and compare exports across two task databases after a round trip. These are task-state equality tests, not proof of an operation ledger returning an earlier command result after a later edit. [TW-IMPORT], [TW-IMPORT-TEST]

**Test-observed counterexample.** The recurrence UNTIL test deliberately preserves propagation to generated instances and eventual expiration/deletion. Borrowing “until” mechanically would therefore import more than a series ending condition. Daymark must preserve its own finite-series and historical-retention contract. [TW-RECUR-TEST]

**Borrow tests:** same-state import under a changed clock; dependency/parent links after a round trip; template/index consistency under rerun. **Borrow concept:** a slot index and series identity can distinguish replay from a new occurrence. **Reject:** mask strings as the only history, inherited expiry that deletes completed-work evidence, or lookahead limits advertised as total compute limits. **Dependency decision:** none.

**Daymark adaptation.** Persist each occurrence identity and explicit outcome rather than infer completion from one mutable mask. Keep operation identity/content hash/result history separate from entity UUID/content. Snapshot replay should return the original recorded result without rewinding newer state or appending another event, even when the entity now has a later version. This last requirement is a Daymark inference, not a Taskwarrior guarantee.

## 4. Vikunja: bounded arithmetic, batch validation, and transaction/effect boundaries

**Source-observed.** `addRepeatIntervalToTime` computes elapsed catch-up using a quotient rather than stepping through every missed interval, with saturation and multiplication-overflow guards. The tests exercise ancient timestamps, small intervals, equality, future dates, nonpositive duration and overflow. A separate test caps accepted recurrence interval size. Borrow the computational-boundary idea, while retaining Daymark's calendar semantics: an elapsed-duration shortcut cannot replace arbitrary monthly/yearly selector evaluation. [VK-REPEAT], [VK-TASK-TEST]

`CreateTaskRelationsForImport` first establishes imported-batch membership, rejects outside-batch endpoints, collects relations, and checks the graph before insertion. Its tests distinguish a long valid chain, a cycle and an outside-batch endpoint, checking that rejected relations do not partially appear. The round-trip test exports links as compact references and reconstructs inverse relationships without embedding a large related task repeatedly. [VK-RELATION], [VK-RELATION-TEST], [VK-ROUNDTRIP-TEST]

**Borrow mechanism:** validate a complete incoming relationship graph before applying it. **Borrow tests:** forward references, cycle versus depth/resource limit, outside-batch reference and no partial graph effects. **Adaptation:** Daymark must additionally enforce its workspace boundary. Vikunja's ordinary relation tests allow authorized cross-project links; “different project” is not a universal authorization failure.

**Source-observed limitation.** Pending events and callbacks live in process memory, are dispatched after a caller commits, and can be discarded on rollback. Tests assert ordering, one-shot dispatch and cleanup. A rollback test also verifies attachment blobs survive a task-delete rollback. This is a useful effect-ordering seam, but it is not a durable transactional outbox. A process can stop after database commit and before memory dispatch. [VK-EVENTS], [VK-EVENT-TEST], [VK-TASK-TEST]

The file importer exposes byte/version bounds and separate stages, including a separate transaction for saved filters after structural import. That is explicit counterevidence to assuming a whole archive import is one transaction just because individual sections use transactions. No live partial-import failure was demonstrated here. [VK-IMPORT]

**Borrow:** root-transaction versus side-effect tests and explicit rollback cleanup. **Reject as sufficient:** an in-memory after-commit queue or unconditional callbacks on library savepoint release. **Dependency/reuse decision:** independent contracts and tests only; no source embedding. **Cost:** a durable outbox and caller-transaction integration require more state than callbacks, but they can be tested through an inert sink and reopened database without any external notification service.

## 5. Tasks.org: acknowledge captured revisions and preserve what was not edited

**Source-observed.** Dirty-state tracking increments a revision, and `markPushed` advances the synchronized watermark only to the supplied captured revision. `withDirtyVersion` records that version after the operation succeeds. Its stale-push test edits again after capturing a version, acknowledges the old version and checks that the newer edit remains dirty. This is a concrete anti-stale-ack pattern, not a lease-generation implementation. [TO-DIRTY], [TO-DIRTY-TEST]

**Borrow mechanism:** an acknowledgment must cover the revision actually processed, not whatever revision happens to be current under the same entity ID. **Daymark adaptation:** claim tokens/generations and entity versions have different roles. A delivery acknowledgment should verify both the current claim identity and any task/reminder state needed for validity. Reusing the same human-readable worker owner must not allow an old success or old failure to mutate a reclaimed delivery. Stable logical delivery keys should survive retries, while claim tokens change.

The recurrence representation stores typed known parts plus unmodeled parts. Tests distinguish untouched read/push preservation, deliberate editing, ordinary-rule round trips, remote change against a cached baseline, local edits and local recurrence removal. The engine-parity fixture supplies expected month-end/leap/ordinal/no-successor vectors. Its COUNT-zero normalization is a useful incompatibility to notice: Daymark's strict public validation need not accept the same input just because another adapter normalizes it. [TO-RECUR], [TO-SYNC-TEST], [TO-ROUNDTRIP-TEST], [TO-MERGE-TEST], [TO-PARITY-TEST]

The JVM wrapper delegates to ical4j and then restores local hour/minute on timed results. That demonstrates a wrapper-policy layer; it does not prove every timezone transition is correctly handled by the wrapper or that raw ical4j and Daymark are interchangeable. ical4j was not separately assessed. [TO-JVM]

**Borrow tests:** captured-version acknowledgment; preserve unknown extension fields during an untouched round trip; intentional recurrence edits do not resurrect removed parts. **Reject:** silently executing unknown recurrence extensions in the strict core, or treating field preservation as evidence of semantic execution support. **Dependency decision:** no Kotlin/Android dependency; no copied GPL test code. Preservation can belong in an import envelope or namespaced metadata while executable core rules remain bounded.

## 6. Org: controlled clocks, policy differences and honest expected failures

**Source-observed.** `org-auto-repeat-maybe` separates restart and catch-up handling, updates repeat-related state/history, and can prompt a person after repeated catch-up shifts. That prompt fits an editor; it is unsuitable for unattended execution. Tests use a controlled effective time to check one-step, catch-up and restart behavior without sleeping. [ORG-REPEAT], [ORG-TEST]

The timestamp-change tests explicitly show month-end clamping and a separate DST edit policy that errors on nonexistent local clock edits. They also mark a group of range/duration expectations as expected failures. A mature test suite can preserve known limitations; a test's presence is not proof of passing behavior. Daymark's declared forward-gap policy differs from this editor operation's error policy and should remain explicit. [ORG-TEST]

**Borrow:** small table-like date transformations with a controlled clock, round-trip interval checks and explicit known-failure status. **Borrow test idea:** changing a planned range should preserve its intended duration under the chosen calendar/instant interpretation, including crossing midnight. **Reject:** editor prompts, global mutable editor configuration as a scheduler API, or copying clamping/error policies because the codebase is mature. **Dependency decision:** none; independent tests only.

**Reversal condition.** A future Org import/export adapter may need to preserve Org's actual repeat syntax and representation. That would be an adapter compatibility contract, not a reason to change Daymark's core recurrence semantics.

## 7. APScheduler: learn the fault boundaries, do not borrow an unproved guarantee

**Source-observed.** SQL acquisition uses transaction-scoped selection/update and lease timestamps, with `skip_locked` requests where applicable. Tests freeze/shift the clock, assert that another worker cannot acquire at exact expiry equality, and can acquire immediately after. Separate fixtures cover multiple workers, expired schedule cleanup and abandoned jobs. The backend's exact SQL behavior is not generalized here to every database. [APS-STORE], [APS-STORE-TEST]

**Important limit.** The inspected schedule release update predicates on schedule ID and scheduler identity, without a per-acquisition generation in that update. The inspected job-release path records a result, updates task counters and deletes the job by job ID. These paths do not establish Daymark's same-owner ABA protection. This is bounded code observation, not an executed exploit or a finding that the entire upstream scheduler is defective. Its ownership/concurrency assumptions may differ. [APS-STORE]

**Borrow tests:** acquire at `expiry−ε`, `expiry`, `expiry+ε`; hold a claim across cleanup/restart; reject stale success and stale failure after same-owner reacquisition. **Do not borrow:** stable worker name as a sufficient fence. An opaque unique acquisition token or monotonic generation must be compared by storage, not only generated in a wrapper.

**Source-observed.** `_run_job` invokes the executor before recording success through the data store. Cancellation shields finalization, but cannot undo an external effect. A process crash after effect and before persistence remains an ambiguous boundary. Publishing an event after commit likewise does not automatically provide durable event delivery. This supports a deliberate crash-injection harness, not an exactly-once claim. [APS-RUNNER], [APS-STORE]

**Test-observed.** Coalescing fixtures distinguish every missed slot, earliest and latest. Calendar interval tests reject a gap day and preserve the nominal cursor when normalization crosses midnight; serialization variants test continuation. This differs from Daymark's forward-gap policy, but the “nominal cursor must not drift” invariant transfers. Trigger state has a version, and a utility rejects missing/future versions. [APS-SCHEDULE-TEST], [APS-CALENDAR], [APS-CALENDAR-TEST], [APS-STATE]

**Borrow:** missed-run policy categories, resumable trigger state, explicit state-version handling and deterministic clock fixtures. **Reject:** importing its gap-skipping policy or execution loop as Daymark's domain semantics. **Track dependency:** only if the optional service needs broader executor or broker integration; adding another job store now could duplicate the engine's authoritative outbox.

**Embedded-transaction limit.** The inspected transaction helper opens its own engine transaction and shields finalization against cancellation. That is useful cancellation hygiene, but not evidence that it preserves an arbitrary caller-owned SQLite root transaction/savepoint. Daymark needs its own tests for that contract. [APS-STORE]

## 8. Hypothesis: a bounded model harness, not a proof label

**Source-observed.** Rule-based state machines generate action sequences with explicit preconditions and after-step invariants. Tests demonstrate invariant failures propagate and redundant setup steps shrink away in a database-like failure sequence. Datetime generation has fold-aware instant ordering and explicit imaginary-value handling; tests cover generating fold-one values and rejecting an out-of-bound ambiguous instant. [HY-STATEFUL], [HY-STATEFUL-TEST], [HY-DATETIME], [HY-DATETIME-TEST]

**Try development dependency:** a small Daymark reference model with synthetic workspaces, tasks, recurrence slots, operations, leases and a separate durable receiver. Generate create/update/complete/cancel/reopen, clock advance/backstep, claim/reclaim/finish, rollback and export/reopen/import actions. Include rejected commands and assert state is unchanged. Avoid preconditions that exclude the very stale or foreign-reference cases the harness should exercise.

**Borrow tests:** after every step compare entity versions, recorded operation results, event identity, logical delivery keys and receiver effects with a deliberately simpler model. Preserve a minimal explicit regression for every useful discovered trace. Compare instants in UTC; ordinary same-timezone wall-clock equality can conceal fold differences.

**Reject:** calling a finite property run a proof, using the implementation itself as its oracle, or suppressing every failure as an expected conflict. Stateful tests must still distinguish unexpected exceptions, invalid transitions and true exhaustion from search-budget limits. Hypothesis does not model real process crashes or all interleavings by itself; explicit barriers, subprocess crashes and separate connections remain necessary.

### Smallest bounded trial

The test owner can decide whether to add a development-only Hypothesis dependency. Baseline: the existing deterministic regression suite. Variant: at most 50 generated histories of at most 20 actions over two synthetic workspaces, a temporary file database and a fake clock, with no external delivery. Grader: zero divergence from the reference model; rejected commands leave state unchanged; each logical receiver key has at most one effect when the receiver explicitly deduplicates; restarted continuation matches uninterrupted continuation. Keep a small held-out matrix of hand-written races, because generated sequential histories do not cover real concurrency.

Stop after the fixed history budget or the first reproducible failure; preserve the minimized trace before changing implementation. Do not broaden into upstream execution, network services or production data. Promotion requires the owner to review the new dependency/license and the resulting independent tests. This research worker did not install or run the trial.
