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
