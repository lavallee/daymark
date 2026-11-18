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
snapshot restore. [Calendar tests](../../tests/test_temporal_adversarial.py)
[Store and restore tests](../../tests/test_store.py)
[Occurrence identity and skip tests](../../tests/test_boundaries.py)
[Brief and mixed-date checks](../../tests/test_brief.py)

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
