# Reading a worker queue

A session asking for twenty tasks needs the twenty full records it can act on.
The ready view now reads the fields that determine eligibility and order, keeps
the highest-ranked page in memory and hydrates its records from the same SQLite
snapshot. It uses the existing entity and relationship indexes. SQLite builds
without JSON functions stream complete records through the same readiness rule.

## Synthetic comparison

The baseline was Daymark commit `0ec6c67`. Each version built a temporary
SQLite database of 10,000 open tasks. Three quarters explicitly delegated work
to an agent. Every fifth task after the first depended on the first task.
One in twenty had a 16,100-character description. No task had a date.

Each query warmed the database once and then ran five times. CPU is the
median process CPU time for a complete library read. Peak Python allocation
was measured in a separate traced read with `tracemalloc`. It excludes SQLite
and OS allocations. Both runs used CPython 3.14.6 and SQLite 3.53.1 on an
x86-64 Linux machine. The nine queried pages had matching ordered-ID hashes.

| Filter | Page limit | Baseline CPU, ms | Candidate CPU, ms | Baseline Python peak, MiB | Candidate Python peak, MiB |
| --- | ---: | ---: | ---: | ---: | ---: |
| Workspace | 1 | 201.0 | 92.3 | 66.85 | 0.36 |
| Workspace | 20 | 221.5 | 90.5 | 66.65 | 0.46 |
| Workspace | 1,000 | 232.9 | 105.5 | 66.66 | 6.86 |
| Agent work in proofs | 20 | 178.7 | 96.0 | 66.62 | 0.44 |
| Empty scope | 20 | 166.6 | 38.8 | 66.01 | 0.35 |

Run `uv run python scripts/bench_ready.py` to generate the fixture and print
CPU time, wall time, memory and result hashes. Use the same script with each
checkout's package to compare revisions. The fixture measures warm local reads.
It provides no measurements for date-heavy queues, network transit, cold
storage, or delivery to an agent harness.

## Reading a session with a long history

For a synthetic task carrying 300 notes of 3,000 emoji each, the complete
HTTP context was 3,628,742 bytes. The task brief returned its current state
and three attributed previews in 3,362 bytes. Reading the last note by its ID
returned the full 12,091-byte JSON note. The byte counts are response bodies
from the local ASGI application, before any optional HTTP compression. The
fixture and comparisons are exercised in `tests/test_brief.py`.

The agenda now counts later or undated tasks from projected fields and reads
the complete records only for visible items. Workspace summary counts use the
same agenda classification and readiness rules within one SQLite snapshot.
On the 10,000-task undated workload, a CLI agenda using `tracemalloc` peaked
at 0.4 MiB of Python allocations, down from 67.4 MiB when it loaded every
task. The rendered agenda was 50 bytes in both runs.

Activity compares event snapshots inside SQLite and returns the changed field
names. A six-event page from a task carrying 300 notes of 6,400 characters
each used 0.4 MiB of traced Python allocations, down from 42.1 MiB when it
decoded full snapshots for every event. SQL-side comparison cost additional
processing time on that fixture. This change bounds the amount of historical
task content held by the client process as the page grows.

Full detail returns its own task body and short summaries of linked records.
A synthetic parent with 120 children, each also dependent on the parent and
carrying a 60,000-character description and a 6,000-character note, produced
10,895 bytes of canonical JSON in both implementations. Projecting the fields
used by those summaries lowered median warm process CPU from 29.37 ms to
9.13 ms across seven calls. Peak traced Python allocations fell from 24.36 MiB
to 0.09 MiB. Both ran against the same local SQLite database and returned
identical bytes. Setup, serialization, native SQLite memory and transport were
outside the measurements.

## Compact startup under concurrent work

Three independent reads could return a ready count of zero and a ready task
ID in the same startup response when another writer captured a task between
the count and the list. The current compact orientation counts and ranks a
single stream of live tasks, fetches the memory previews, and reads the
activity cursor within the same SQLite transaction. A two-connection test
commits changes during that read and checks that the next activity page
contains both changes. [Snapshot tests](../../tests/test_orientation_snapshot.py)

The benchmark creates 1,000 tasks with twelve notes of 1,024 characters each
plus twelve memories with 2,000-character titles. It compares one compact
snapshot with three separate public reads returning equivalent information in
a quiet workspace. Two runs of seven warm samples each on Linux x86-64,
Python 3.14.6 and SQLite 3.53.1 gave median process CPU of 40.60 and 88.54 ms
for the separate reads and 19.79 and 47.89 ms for the snapshot. Median wall
times were 41.09 and 168.18 ms versus 19.87 and 71.84 ms. Peak traced Python
allocations were 0.069 MiB and 0.032 MiB, respectively. The machine was also
running other tests. These measurements exclude setup, HTTP and native SQLite
allocations. Run `uv run python scripts/bench_orientation.py` to reproduce
the fixture and parity check. [Benchmark](../../scripts/bench_orientation.py)

A separate local ASGI trial used twelve 2,000-emoji memory titles and twenty
tasks with 50,000-character descriptions and 8,000-character notes. The three
responses for the prior workflow totaled 41,802 uncompressed body bytes.
The authenticated orientation response occupied 4,935 bytes, including
identity, evaluation instant and activity cursor. A separate run against the
v0.5.0 service confirmed the fallback still finds a scoped fact in a later
page; that service supplies views independently and returns no atomic
checkpoint.


### Cost of checking an activity boundary

A checked continuation fingerprints the metadata and both snapshots of one
event. A local synthetic run used a note edit on a task with 1 KiB, 64 KiB
and 1 MiB of existing ASCII note text. An in-memory SQLite 3.53.1 database
on CPython 3.14.6 produced median process CPU times of 0.096, 0.475 and
4.676 ms respectively over 11 warm reads of the boundary. Peak Python
allocation measured separately using `tracemalloc` was 6.0, 67.7 and
67.7 KiB. These samples measure only the fingerprint and read transaction.
A page that advances checks the incoming boundary and computes the outgoing
one. Work scales with the two boundary events' size, independent of the
number of earlier events.

## Listing a portfolio

The existing project-list regression creates 101 projects, 50 people and
20,000 tasks. Tasks have assignees and cross-project dependencies. On the
same synthetic SQLite file, seven pairs of fresh Python 3.12 processes
alternated the previous and revised readers. Median process CPU for
`projects` fell from 2.67 to 1.04 seconds. Each pair returned identical
canonical JSON. The reader projects planning fields, groups external
dependents in one pass and reuses the workspace's lookup of related records.

The four-version test matrix running concurrently exceeded this test's
four-second wall-clock limit on the loaded host. After the change Python
3.12 still took 4.64 seconds in one simultaneous run. A separate run of the
complete suite on that interpreter passed the existing limit. These
measurements describe this fixture and host. Serving time under load also
depends on the SQLite and application processes sharing the machine.

## An idle reminder worker with a history

A synthetic workspace contained 1,500 reminders, 40 tasks with long
descriptions and prior notes, and one recipient. The built-in worker recorded
the 1,000 due reminders. The other 500 were scheduled for a later date. Seven
further passes at a fixed earlier instant each returned zero claims, recordings
and failures. The SQLite file and worker limit were the same for both reads.

| One idle pass | Previous reader | Active-reminder projection |
| --- | ---: | ---: |
| Median process CPU, Python 3.12 | 111 ms | 27 ms |
| Peak traced Python allocations, separate pass | 8.85 MiB | 11 KiB |

The current reader filters on reminder state, reads linked task status within
the same transaction and fetches full records for the due batch. Ordering
uses aware datetimes at their stored precision. The measurement includes
tick, heartbeat, claim and the database commits. It excludes external
notification services and native SQLite allocations.


### Polling a project after a quiet interval

The project change check reads the workspace head and tests for a matching
event in one snapshot. Its SQL query returns only the event number for a
change flag. The full event page still returns the recorded states.
A fixture used one project member with a 64,000-character description and
8,000-character note, 45 external dependencies, and 240 unrelated events
with 8,192-character descriptions before a final project note.

| Warm read | Earlier CPU | Revised CPU | Earlier Python peak | Revised Python peak |
| --- | ---: | ---: | ---: | ---: |
| Poll at the current head | 0.081 ms | 0.034 ms | 13.9 KiB | 2.1 KiB |
| Poll one project edit | 0.287 ms | 0.169 ms | 308.5 KiB | 5.9 KiB |
| Poll across 240 unrelated events | 10.181 ms | 2.103 ms | 309.5 KiB | 5.4 KiB |
| Read the full matching event across the same interval | 10.741 ms | 3.044 ms | 307.9 KiB | 303.5 KiB |

Each value is a same-host local comparison on synthetic data using CPython
3.14.6 and SQLite 3.53.1. CPU is the median of nine process-time readings.
Python peak was measured separately with `tracemalloc`. A third query
variant scanned serialized strings for candidate fields before inspecting
JSON. Its median across the unrelated interval was 11.470 ms. SQLite's
top-level JSON field comparisons preserved the membership results and
were faster in this run. An embedded SQLite build without JSON functions
uses a streaming Python check.


## Process startup and a persistent client

We ran eight measured, alternating fresh processes for each command on the
same Linux host, Python 3.14, with the interpreter and dependencies already
installed. Deferring public imports until the caller requests an object
reduced median process CPU for `daymark --help` from 210 to 70 ms and for
local `daymark where --json` from 209 to 72 ms. A ready query still needs the
engine and its validation models. Its median CPU was 192 ms before and
186 ms afterward in this run. Full public imports were 189 and 191 ms.

A separate trial exercised five fresh stdio MCP sessions against one
40-task workspace. Each session performed ten five-item ready reads,
alternating with a new CLI process reading those same five items. The
median MCP handshake took 197 ms. A read on an established session took
1.1 ms elapsed, versus 246 ms for a fresh CLI process. These are local
synthetic measurements of Daymark. They exclude model generation and
network latency.
