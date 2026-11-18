# What a returning worker can tell about a recurring task

Daymark advances one date per generated occurrence. If a task has a planned
day and a hard deadline, the next occurrence receives a new planned day and
inherits the hard deadline. This matters when a person says “I prepare the
sample every Thursday and must submit it each Friday.” Capturing those two
dates on one recurring task would leave the second sample due on the first
Friday. The current model also supports a different intent: repeated work
under one fixed cutoff. The two requests need different date behavior.

## Observed transitions

A local synthetic workspace used UTC and a fixed completion clock of April
12, 2030 at 17:00. Each row started with its own weekly task and used the
ordinary Tasks API. The results came from persisted successor records.

| Original dates or action | Observed result |
| --- | --- |
| Fixed schedule, due April 5 | Next deadline April 12. |
| Fixed schedule, planned April 4 | Next planned day April 11, even though completion occurred April 12. |
| Fixed schedule, planned April 4 and due April 5 | Next planned day April 11, deadline still April 5. |
| Seven days after completion, due April 5 | Next deadline April 19. On April 12 the deadline-only successor already appeared in ready. |
| Fixed schedule, due April 5, move this occurrence’s deadline to April 15, then complete | Next deadline April 12. The original slot supplies the fixed cadence. |
| Skip the April 4 planned occurrence | Original cancelled, no completion timestamp. Next planned day April 11. |
| Cancel the April 4 planned occurrence | Original cancelled, no completion timestamp or successor. |
| Weekly from April 4 with `COUNT=1` | No successor. |
| Weekly from April 4 with `UNTIL=20300411` | April 11 is generated. Completing it leaves two recorded occurrences and no open one. |

A worker taking from a delegated queue may receive a future deadline-only
occurrence immediately. A claim means the worker may act on the task.
Completion records that the work actually happened. For work that can only
be done on its recurring planned day, capture that day as the plan. A fixed
series that is several rounds behind advances one recorded round at a time.

The brief now shows the recurrence rule, mode, time zone, occurrence index
and which date advances. The text labels the first occurrence “Round 1” and
the structured record retains the engine’s zero-based index. The activity
event `recurrence.create` contains the actual successor ID when a round is
materialized. A finite rule can end with the completion event alone.

The engine tests also cover late completions across daylight-saving changes,
invalid civil calendar dates, bounded lookahead, snapshot/restart of a
completion-based count budget, and replay after a completed round is reopened.
Moving a fixed occurrence keeps its original series slot across a native
snapshot restore. In the matching source checkout, the calendar, restore,
occurrence identity and mixed-date checks are in
`tests/test_temporal_adversarial.py`, `tests/test_store.py`,
`tests/test_boundaries.py` and `tests/test_brief.py`, respectively.

## Representing two dates that both move

Today a person can capture separate recurring tasks when preparation and
submission are genuinely distinct actions. A dependency names one concrete
task ID. It does not automatically pair the next preparation with the next
submission. A user wanting that pairing can specify the two dates on each
new task after inspecting the previous round.

A future design could let a person say that each occurrence’s deadline is
one local calendar day after its planned day. This would require an explicit
relationship between those two dates. It should apply to future
occurrences the person selects, preserve past evidence and every existing
hard deadline, and preview the next pair before saving. A recurring deadline
at a fixed wall time, a deadline after an elapsed duration, and a series-wide
cutoff need distinct examples, including the daylight-saving boundary. No
assumption about the intended relationship follows from the current two dates.

## Where the first round falls

The 0.5.0 release created an undated `--every fri` capture on the current
day, even on Tuesday. A task converted from a one-off to the same weekly
rule had no planned day, series ID or occurrence key. Moving only that
first converted occurrence to a later Friday also shifted its fixed series.

On Tuesday, January 1, 2030, a new undated Friday series now begins January
4 in the person's selected timezone. Turning an undated one-off into the
same series produces the same planned Friday and gives the original record
its first occurrence identity. Moving that one occurrence to January 25
leaves the second round planned for January 11. An ordinary one-off merely
planned for Friday remains ready on Tuesday. The first round of a repeating
task with a future planned day follows the existing future-round readiness
rule. A deadline-only round remains available before its deadline.

Checking the first four matching civil dates in 4,000 synthetic combinations
of frequency, interval, selector and start date found 3,862 nonempty
sequences within a 100-year sample. All 3,862 had the same sampled dates
when anchored at their first matching day. The product keeps its separate
400-year search bound. A finite rule that ends before its first match gives
the caller an error. An explicit first plan or deadline still wins.

For a Tuesday explicitly supplied as the first date of a Friday rule with
`COUNT=2`, the two recorded rounds are Tuesday and the following Friday.
The released engine also generated a second Friday. RFC 5545 includes the
start in the occurrence count and leaves an off-pattern start's recurrence
set undefined. Daymark's choice preserves the explicitly recorded first
round and includes it in the requested total. [RFC 5545 §3.3.10 and §3.8.5.3](https://www.rfc-editor.org/rfc/rfc5545#section-3.3.10)

A clean 0.5.0 wheel created the Tuesday capture, converted another one-off
and skipped an ordinary task. A separately built development wheel replayed
all three release receipts after reopening the SQLite database and after
restoring the JSON export. The saved release results and workspace head
were unchanged. New captures and conversions in both destinations selected
Friday and retained the following Friday after rescheduling the first round.

## A calendar assistant after four missed weekly readings

An external Python app used the released 0.5.0 wheel and a synthetic
recording sink. It created an October 21 weekly equipment reading, then
returned on November 18, 2026. Its first actual completion left the
fixed-cadence successor planned for October 28. Three explicit skips passed
October 28, November 4 and November 11 without recording any readings.
The November 18 round was then available. A second series using seven days
after completion produced November 25 immediately.

The app scheduled two notices against the original reading. Running the
worker first recorded the overdue notice once. Completing the original
cancelled its future notice. Neither notice appeared on the next round.
A separate fixed-clock check completed the original before running the
worker. Both undelivered notices were then cancelled. A calendar app that
promises a reminder on every round must explicitly schedule that round’s
notice when it receives the new occurrence ID.

The new round kept its assignee, scope, context, next move and prerequisite
ID. Its checklist kept the item, marked unfinished. The old source
reference, claim, focus, notes and waiting reason stayed off the new round.
When a later worker claimed the second round and added a reading, undoing
the first completion left both records open. They had different
`occurrence_key` values. Closing the two in either order, including after
native transfer, preserved the second round’s reading and produced one
third round. An undo corrects the selected record. Scheduling or claims
already attached to another occurrence require their own review.

The released app also reproduced three unintended transitions. A late skip
could erase a completed timestamp, restart a cancelled series or produce
a visible successor from an archived round. On a one-off task it silently
cancelled the task. Fresh skips now require a visible, unfinished recurring
task. A closed recurring round must be reopened before another completion,
and an archived one must first be restored. Genuine release-era receipts
for an earlier one-off skip and completed-round skip replayed without new
events after both SQLite restart and native JSON transfer.

Revisiting an earlier round used to decode the complete change history of
its existing successor just to check the occurrence key. A fixture with 72
notes of about 2,000 characters peaked at 13.9 MB of Python allocations on
the development interpreter. Projecting the occurrence key and checking
the current successor first reduced that peak to 26 KB. Deleted or reused
IDs still reserve their recorded slots. The calendar return tests are in
`tests/test_recurring_return.py` in the matching source checkout.

## Checking local clock input across timezones

A development wheel was tested on the 312 zones in each tested copy of
`zone1970.tab`. The harness inspected UTC days from 1990 through 2040.
When the offset changed between daily samples it located the transition
second, tested both sides with explicit offsets, and tried a wall-clock
minute inside the gap or overlap. It tested both a written calendar date
and a relative day with a time. For overlaps it also parsed both explicit
offsets and checked that Daymark displayed distinguishable readings.

| Timezone data source | Forward jumps found | Backward jumps found | Mismatches in those checks |
| --- | ---: | ---: | ---: |
| System, 2026c | 6,842 | 6,847 | 0 |
| Wheel dependency, 2026f, with the system search path disabled | 6,813 | 6,816 | 0 |

The sampled changes included thirty minutes, two hours, three hours, seven
hours and a full day. These are checks on one-off clock input and display.
They cover offset changes detected by daily samples in these two database
versions. Recurring schedules have their own gap and overlap rules.

## Crossing midnight with running workers

Two installed builds, `19536c4` and `864515a`, each opened a new synthetic
New York workspace before midnight on November 18, 2026. A real 23:59
reminder recorded first. At 00:00, two worker processes raced on each
database. Together they claimed and recorded two due reminders, one for
open work and one for waiting work. A cancelled reminder remained
cancelled. A proposal's reminder stayed pending until an explicit
acceptance. Restarting added no recording. These were local recording-sink
outcomes.

In a separate retry experiment, an installed candidate resolved “tomorrow
09:00” at 23:59:48. The experiment held the write until 00:00:02 without
altering the system clock. A fresh process retried the same operation at
00:00:06. Both results identified the same reminder at November 18,
14:00 UTC, and the workspace still had three events.

## Pre-decision recurrence study

The coordinator returned on November 18 with four missed weekly press
inspections. The first plan had been moved from October 21 to October 23.
The original hard deadline was October 22. A second task repeated weekly from
completion, with a timed deadline and an inclusive `UNTIL` limit. The tasks
and records were synthetic. This was a walkthrough of the coordinator's
questions using the actual CLI and Python task interface.

| Question before approval | What the existing surfaces answered |
| --- | --- |
| Which round am I looking at? Which date changes? | `show` displayed current dates, status, cadence and history summaries. `show --brief` and `Tasks.brief` also identified the round number and advancing field. The how-to explained that the deadline is copied when the plan drives recurrence. |
| Does one skip clear the four misses? | The how-to says one transition advances one round. Forecasting the date of that round requires the recurrence calculation. The moved date in CLI detail would suggest October 30 if used as the weekly starting point. The engine uses the historical October 21 slot, yielding October 28. `Tasks.show` exposes structured recurrence and history, which still require interpretation. |
| Will COUNT or UNTIL stop the series, and what happens if I choose skip? | The views display the rule. Applying the count and cutoff, including the time zone and the instant of a completion-based skip, requires calculation. The previous how-to named actual completion as the anchor without spelling out the skip case. |
| Will a successor be created or was that slot already used? | The how-to warned that reopening keeps later evidence. The ordinary views did not resolve current and historical slot reservations. A successful operation's `recurrence.create` event confirms actual materialization and its ID. |
| When will a worker act or a reminder be delivered? | Readiness, claims and recorded reminder state describe known facts. Future action and delivery depend on the worker and delivery system. |

The coordinator needs the successor date and slot outcome to decide which
work to reconcile. The candidate adds `daymark show ID --forecast skip`
and `daymark show ID --forecast done` locally and over HTTP, with `--json` for
structured results. Read-only MCP uses `daymark_view` with `view=show` and
`forecast=skip|done`. Use an exact ID or source on each surface. Authenticated
HTTP uses the `forecast` query parameter on the existing context and source
reads. The forecast evaluates one fresh transition at the displayed instant
for a visible open or waiting recurring task. It shows the
snapshot's task version and UTC evaluation time, the selected round's
projected status, and whether a successor would be created, the rule ended,
or the slot or fallback ID was already reserved. When a successor would be
created it reports its ID, plan and deadline. Skip records cancellation with
no completion time. Both actions use the evaluation instant for a
completion-based rule.

The forecast uses the same historical first-slot lookup, recurrence engine and
ID reservation checks as materialization. The CLI opens SQLite read-only and
does not initiate a schema upgrade. The forecast covers the calendar effect.
A working claim is advisory. Both writes can proceed under another actor's
live claim and clear it on the finished round. The forecast does not model
authorization, readiness, notes, reminders, other write side effects, or
operation replay. Its version describes the selected round. Successor
availability is checked again when the write runs.
Refresh before approval. Even with the same task version, another record can
take the expected ID, and the effective time of the write can cross a cutoff.
The HTTP client checks the service schema for the exact selector route.
In a test against the v0.5.0 service, ordinary show by ID succeeded, and
forecast by either selector stopped at the authenticated schema read.
Explain the recorded rule with ordinary show when available and check
activity or events for `recurrence.create` after an authorized write.

An installed development wheel and an actual 0.5.0 installation each ran
behind a mounted, bearer-protected service on loopback. Both opened a
restored synthetic database read-only. The development client used HTTP,
CLI and the read-only stdio bridge. Its ID containing a slash, percent sign
and Unicode, and the matching source reference, produced the same
development forecast. Skipping or finishing advanced the October 5 plan to
October 12, with the hard deadline still October 6. A second credential
selected a different workspace with the same ID and a November 12 successor.
Missing and incorrect credentials returned 401. The tagged service returned
ordinary ID detail and refused forecasts by ID and source. All 19 requests
to the development service and nine to the older service used GET. Workspace
exports, event positions and database file hashes were unchanged in both.

Each skip and finish in the comparison ran as a genuine version-guarded
operation on its own SQLite backup. The moved round produced October 28 with
the October 22 deadline. In the four-miss trial, each skip advanced one slot,
reaching November 18 after four writes. The trials covered fixed and
completion-based COUNT, readable ID collision, and successors that were
live, deleted or deleted and replaced by unrelated work. For the timed
New York series, a write at November 18 16:00 UTC produced a deadline at
November 25 11:00 EST, exactly the UTC cutoff. At 16:00:01 UTC the rule
produced no successor. The source store's exported facts and main database
bytes matched before and after forecast reads. Separate read-only SQLite
connections for MCP and the authenticated HTTP service agreed for ID and
exact source, and the response stayed compact with large stored series fields.
The comparison tests are in `tests/test_recurrence_forecast.py` in the
matching source checkout.

### Asking before changing a missed round

Six fresh Claude Code sessions inspected two synthetic weekly print jobs over
read-only MCP. Both jobs started with an October 21 plan, an October 22 hard
deadline and a one-off move of the first plan to October 23. In the second
workspace, an earlier skip had generated the next round. Someone then deleted
that round and reopened the original. Each session received the same question
about the result of one skip, and each workspace export stayed unchanged.

| Available read | Moved first round | Previously created and deleted next round |
| --- | --- | --- |
| Ordinary detail and activity | Estimated November 20 and 19 after 18 tool calls. Qualified the estimate. | Estimated another round was likely after 25 calls. |
| Forecast with a short outcome code | Correctly predicted October 28 with the original October 22 cutoff in 7 calls. | Answered no in 11 calls, but said the reason was unavailable. |
| Forecast naming the reason in structured data | Returned the two dates in 6 calls. | Explained in 10 calls that the already-created round stays reserved after deletion. |

Each cell is one run. The repeated question exercised calendar consequences,
not permission to skip or whether the person wanted a different deadline.
