# Daymark in real MCP clients

A returning agent needs to know what it can pick up, which decisions still
belong to the person and whether an earlier write actually landed. We
tested those questions in fresh agent-host processes and independent Python
and TypeScript SDK clients. Each trial used synthetic records. We checked
stored state after the runs, including instances where a worker gave a
plausible answer but missed a matching record or selected the wrong person.

The first handoff asked a worker to find explicitly delegated work, claim
exactly one task, read the previous worker’s note and leave the task open
for the next shift. Later trials cover working routines, source lookup,
retries, review and authenticated policy.

## Fixture and grading

Six tasks shared one workspace. The eligible workshop task `label` carried an
earlier note giving label series Q-41. One task belonged to a person, another
depended on `label`, a third had an active lease, and a fourth was a proposal.
The remaining task belonged to a different scope. The prompt asked the agent
to claim the next delegated workshop task and return it with a note that it
had verified the label against the sample sheet and that packing remained.

A successful write trial had three expected events from the incoming actor on
`label` alone, in order: claim, note, release. Its final status had to be open,
its holder empty, and its note had to include the series and the remaining
work. Each client started in a fresh process. The second Claude shift used the
first shift’s database, disabled session persistence, and had read-only tools.

| Host and interaction | Calls and observed result |
| --- | --- |
| Claude Code 2.1.323, Opus 5.5, text tools at `af1ce39` | Six calls. Prime, ready, show, claim, release with note, show. The event history and final state met all write criteria. |
| Same host and model, working tree with structured reads | Six calls. Prime, filtered ready, atomic take, show, release with note, show. The event history and final state met all write criteria. |
| Same host in a fresh read-only session on the second database | Six reads. It recovered Q-41, what had been checked, the remaining packing work, the note author, the empty holder and the downstream task ID `count`. No new events appeared. |
| Codex CLI 0.166.0, ephemeral session, client approvals disabled | Structured ready and show reads succeeded. The host required approval for take and declined the call before Daymark received it. The agent reported the task unclaimed and the handoff unwritten. The database had zero events from that actor. |

These observations cover one synthetic assignment per condition. Different
client permission settings can change whether a write reaches Daymark. The
model-directed runs also exposed a display difference: Claude presented
structured result fields to the model, so the write receipt now carries the
task title and the lease outcome there. A returning process using the same
actor name can identify the recorded author but cannot distinguish two
sessions under that name.

## A read-only status check

A fresh Claude Code session was given only the read-only Daymark connection
and asked what blocked `seal-bin`. The host listed two tools. It made four
reads and reported the exact blocker ID `check-label`, its title and the
`dependencies` reason. The fixture had two events before and after the trial.

We repeated the same prompt on a fresh fixture after adding direct blocker
titles and statuses to show. That single response contained all requested
facts. The host still made four reads: in each run it issued all four before
seeing any result. This pair of trials therefore shows a more complete
individual response, with no observed reduction in call count.

A long-history fixture gave five blockers 100 four-thousand-character notes
each and gave their dependent task 60 notes. The dependent brief grew from
1,421 bytes (IDs alone) to 1,724 bytes (IDs, titles and statuses). Five
separate full blocker details totaled 2,042,621 bytes. After three warm-up
reads, 15 in-process reads of the named brief had a median of 10.66 ms;
suppressing the added lookup in the same process yielded 3.61 ms. The run
used Python 3.14.6 and SQLite 3.53.1. These are fixture measurements, not a
client latency estimate.

A separate Python SDK 1.30.0 process opened the same read-only surface against
current and 0.5.0 authenticated HTTP services. Search worked against both.
Calls to hidden write names and a repeated write attempt were refused in each
case. Each server retained its initial two events.

## A decision changed while the reviewer was away

We seeded a proposal for two labels, changed it to two hundred, and gave a
fresh Claude Code session the person's approval for version 1 only. It
attempted a version-guarded acceptance, received a conflict, read version 2
and left it pending. It identified the quantity change from the prompt but
could not verify whether other terms had changed. The store recorded zero
reviewer writes.

We repeated the trial with a second, undisclosed change: the deadline moved
from May 20 to May 16, 2030. A fresh session with the version comparison
available made three reads: prime, show, and show with `since_version: 1`.
It reported both quantities and both dates, asked for a fresh decision and
left the proposal pending at version 2. The event log again contained only
the fixture’s create and update.

## A two-turn proposal through an authenticated proxy

We configured the client with workspace `local-display` and actor
`session-label`. The service bound the credential to workspace `sample-lab`
and actor `shift-agent`. In an initial programmatic trial, a normal capture
stamped its first note with the client label while a follow-up note and both
write events recorded the server actor. This led to a change in ordinary HTTP
captures so the service now stamps the initial note inside the write.

A fresh Claude Code process then ran two turns on the revised surface. The
first asked it to record one idea as a proposal with an initial note and to
identify the connection. It reported the authenticated workspace and actor.
The second said the idea sounded promising and asked who had recorded it,
whether it was accepted, and who could pick it up. The agent made one read
in that turn, identified the authenticated note author and reported that the
proposal was still pending. The database contained one proposed task at
version 1 and one create event from `shift-agent`.

## Four cold sessions with a changing workspace routine

Four independent Claude Code sessions used all six writable tools against the
same synthetic workspace. Each received a fresh conversation. Six open tasks
included decisions estimated at 12, 20 and 45 minutes, a high-priority decision
with no estimate, and two ordinary tasks that fit a 15-minute sitting. A second
workspace supplied an isolation check. After each process exited, a separate
reader inspected the active settings, default advice, memory and task events.

| Session and request | Calls | What persisted after exit |
| --- | ---: | --- |
| One decision that fits 15 minutes, for today | 8 | The 12-minute decision was recommended. Default settings stayed at version 0. |
| The same temporary constraint again. Is this already a standing routine? | 11 | The worker found the default active profile and explained the limits of keyword memory searches. Version stayed at 0. |
| Save one fitting decision under 15 minutes, falling back to ordinary work | 20 | One workspace settings write produced version 1. Its default advice picked the 12-minute decision. |
| A different worker requests two options under 25 minutes and one look at the complete queue | 12 | The worker read version 1, guarded the revision by version and saved version 2. It showed all six ready tasks once. Default advice then returned the 12- and 20-minute decisions. |

The six task versions stayed at 1 through all four sessions. No worker added
a memory and the comparison workspace retained its default settings. On a copy
with the fitting decisions completed, the original 15-minute routine picked
the eight-minute ordinary task. This checks the fallback without changing the
shared trial data.

Two sessions opened four and six full task records to check estimates. The
structured advice already included estimates for its picks, but structured
ready rows omitted them, including the candidates a temporary filter excluded.
Ready and ready-explanation rows now carry each known estimate in minutes.
An absent estimate still means the duration is unknown. Repeating the first
prompt with the new projection produced the same selection and eight calls.
The worker opened two full records, down from four, but also made two invalid
attempts to pass ready filters directly to advice. Those refusals now show
the supported temporary settings syntax. This single repeat establishes no
overall call-count improvement.

## Restart after the write committed and the connection dropped

A loopback HTTP proxy forwarded each operation to an authenticated Daymark
service, read its successful response, then closed the downstream connection
before forwarding any bytes. Each tool call ran in a new stdio process with
the Python MCP SDK 1.30.0. Two operations lost their first reply.

- A task capture included a generated ID, relative due date and attributed
  initial note. Retrying the same request returned the original ID at version 1.
  A changed note or a different authenticated actor in the same workspace
  produced a conflict. Reusing the operation ID in a separate workspace
  created an independent task there.
- Releasing a claim included a handoff note in the same batch. The retry
  returned the unclaimed task at version 4. Changing the handoff text produced
  a conflict. The final event sequence was create, claim, note, release.
  The task retained exactly one initial note and one handoff note.

In both cases the client initially reported a transport failure even though
the server had committed. Client-side failure alone cannot settle what
happened. Retrying with the original arguments and operation ID recovered the
original receipt.

A separate trial omitted the optional operation ID. Previously the failure
offered no key and a retry created a second task. The bridge now generates
the key before sending the write and includes it in an ambiguous HTTP
failure. Repeating that cold-session trial with the returned key left one
task and returned its original ID.

The same proxy also dropped a successful CLI capture. An unkeyed rerun
previously created a second record. With the returned `error.operation_id`
and the original arguments, the second CLI process recovered the first ID
and left one task with one initial note. A confirmed credential refusal and
a failed read each omit the write-recovery hint.

## Assigning a standalone task to a person

A cold worker received an open task and an existing person in the same
workspace. Asked to assign the task while preserving other details, it found
both records. The first host had no assignment field in its MCP tools. It
left the task untouched but incorrectly suggested that assignment required
a project. The CLI already supported assignment on standalone work.

After adding `assignee` to MCP capture and edit, a fresh worker given the same
request made one version-guarded edit and verified the responsible person.
The record advanced once, stayed open, and had no claim, wait or project.
The worker still consulted activity while locating the person. The discovery
text now says that find defaults to tasks, and the guide names the person
and principal kind filters.

A second fixture gave a person and a principal the same display name. On
the first run the worker searched only people, selected that ID and assigned
the task without asking. After the primer and manifest advised passing the
supplied name through to the resolver, a fresh worker on the same prompt
received both candidate IDs, inspected their details and asked which was
intended. The task stayed at version 1 with no assignee. Hosts can still
supply an explicit ID; this trial measures the worker’s choice, while the
ambiguity check itself runs in the plain-verb layer.

## A half-hour clock change

Two separate Claude Code conversations used authenticated MCP in the
`Australia/Lord_Howe` zone. Each involved a 1:45 am deadline on April 7,
2030. The clocks repeat that reading with a 30-minute difference. Both
workers asked which occurrence the person intended and recorded the later
one only after clarification. Reading full detail returned
`2030-04-07T01:45:00+10:30`, equivalent to 15:15 UTC the previous day.

The first prompt explicitly requested a question before creating anything.
The database stayed empty until the person selected the later time. The
second prompt simply asked for the task and deadline. That worker created
an undated task with a note while waiting and added another note after
setting the deadline. The observed difference was in partial capture and
note-taking. Both sessions preserved the uncertainty about the instant.

## First setup alongside two existing trackers

We opened a synthetic checkout with one instruction file assigning code
issues to Beads and another assigning approval decisions to an editorial
board. The published 0.5.0 CLI printed the onboarding snippet and exposed
six tools. Its MCP primer did not report the launched database, workspace
or actor. It lacked the read-only switch and instruction-check command.

A clean development wheel let us preview the instructions and check file
selection before setup. After one project initialization, two independent
stdio clients in different working directories each reported two tools,
the same absolute database and workspace, and their distinct actor names.
A symlink resolved to the expected file; a second initialized file with
the same workspace produced a different path. No tasks or events were
needed. Both databases kept their byte hashes. SQLite created WAL sidecars
for the first read of one database.

Both baseline executables treated a mistyped database filename as an empty
ready queue and added eight Daymark tables to a second tracker’s SQLite file
on a read. The corrected wheel refused both selections. The missing file
remained absent and the tracker kept its single table. A first write to a
deliberately selected new file still worked. Both instruction files retained
their original tracker rules outside Daymark's section.

A fresh read-only Claude Code worker read both instruction files. It assigned
the defect to Beads and the approval to the editorial board. It left the
printer follow-up unassigned, reported the correct workspace and actor, and
found zero tasks. After the person assigned personal follow-ups to Daymark,
it selected only the printer call and explained that the instruction files
had not recorded this choice for future sessions. The files retained their
hashes, with zero entities and zero events. An earlier harness configuration
timed out before emitting its first session event.

On HTTP the base wheel identified the required client extra. An absent token
and a rejected token each produced `unauthenticated`. A stdio client with an
invalid token still listed tools, then failed on its first data read. With
valid credentials both clients confirmed the shared remote workspace and
their respective service actors. A rejected limit of 1,001 could be
corrected on the same connection.

## TypeScript SDK across local and authenticated connections

The official TypeScript SDK 1.32.1 on Node.js 25.1.0 completed the same
journey against local stdio and stdio backed by an authenticated HTTP
service. It discovered six tools, captured a task by a case-insensitive
Unicode assignee name, and read it back by a source reference containing an
accent and slash. Its structured result preserved the note text and the
selected instant within a repeated half hour.

After renaming the person, the old-name retry returned a conflict containing
the recorded assignee ID. Repeating the original assignment using that ID
returned the version-1 receipt. Each workspace had exactly three events
across person creation, task creation and the person's rename. A second
read-only client discovered two tools, refused a direct call to complete the
task, and read back its open state. The local workspace path contained spaces,
a number sign and a non-ASCII character.

A second qualification used the stable split client `@modelcontextprotocol/client`
2.4.0 and the maintained `@modelcontextprotocol/sdk` 1.32.2. We installed
each version from the official npm registry outside the checkout. With the
v2 client, default and automatic negotiation each selected 2025-11-25
on both local and authenticated connections. Each run used three fresh
stdio processes and 14 calls. The second process recovered the original
capture, read the changes behind a stale approval refusal and accepted the
revised task with a fresh guard. A final read-only process saw two tools,
received a structured refusal on a direct write and confirmed one note,
an open task and three events. The v1 client passed the same journey.
[Official TypeScript SDK](https://github.com/modelcontextprotocol/typescript-sdk).

One older official client, SDK 1.12.3, initially stopped at initialization:
it requested 2025-03-26 and received an unsupported later revision.
After enabling the March revision, three fresh sessions discovered tools,
retried the original capture after approval and read the open task back
at version 2. JSON views and errors remained available as text. A separate
wire-level check exercised March's required batch support, including
notification-only batches and hidden write refusal in a read-only session.
[March 2025 protocol](https://modelcontextprotocol.io/specification/2025-03-26/basic).

## A scoped goal with work in another scope

Three studio conditions shared a goal elsewhere. One had an in-scope proposed
task and an open task in another scope, one had no linked work, and one
depended on a prerequisite elsewhere. A read-only worker was asked which
conditions could move, which lacked work and which remained blocked.
The first worker made 26 calls, never used `explore`, confused an empty
ready-task list with an empty condition frontier and named the other goal
and prerequisite. Two follow-up workers found the scoped condition view
but still used full record detail and repeated out-of-scope information.

The scoped `explore` result reports related work in other scopes by count
and status. Session guidance distinguishes those aggregates from full detail.
A fourth fresh worker used eight reads, all scoped, named both conditions
that could move and identified the true gap and the blocked condition.
It reported the one open linked task elsewhere without naming that task,
its goal or the prerequisite. It also inferred that neither condition was
actually being advanced. Task status and linkage establish recorded work;
they cannot establish whether a person is working at the moment.
All four trials left the seven fixture events intact.

## Undo after another worker has edited the task

Two fresh MCP sessions were asked to undo only the previous worker's title
change while preserving an evening shift's edits. When the evening shift
had changed priority and added a note, the first worker restored just the
old title with a guarded edit. The priority and note survived. When the
evening shift had also supplied a newer title, the second worker showed
both alternatives and asked which one to keep. It wrote nothing.

The plain `undo` operation has a narrower meaning than either conversation:
it reverses the latest event on that entity. A deterministic interleaving
exposed an earlier failure. A worker read version 2 to undo a rename. While
the request was in flight a second worker changed the priority at version 3.
An automatic retry then reversed the priority instead. The new implementation
returns a conflict and preserves both fields. A successful reviewed undo can
still be retried with its original operation ID after later work.

## Asking about authority without deciding

A read-only authenticated worker was asked whether its service identity
could decide a pending proposal on a write-enabled connection. Before
proposal detail exposed policy, it could confirm the actor and pending
status but had no evidence of the workspace permission.

In the original trial the worker also tried `proposal`, `decision` and
`all` as search kinds. Each returned an empty page despite the pending task.
Typed searches now reject unknown kinds and statuses. A fresh worker asked
for pending proposals in one scope first tried `kind=proposal, status=pending`.
It followed the returned correction and retrieved the single matching ID and
title with `kind=task, status=proposed`. The source record stayed at version 1.

Two more cold workers used the same prompt and fixture under different
policies. One correctly reported that its credential was allowed by the
workspace, the other that it was denied. Both identified the read-only
connection as a separate constraint. Neither attempted a decision, and the
proposal stayed at version 1. Follow-up tests removed permission after an
allowed read; the subsequent guarded decision was refused, leaving the
proposal pending.

## Mixed client and service versions

An independent Python MCP SDK 1.30.0 process connected over stdio to each
client, which in turn connected to an authenticated HTTP server on loopback.
We ran three client/service combinations: working/working, working/0.5.0,
and 0.5.0/working. Each could capture new work and read its own active claim.
The working client projected compact ready rows even when 0.5.0 supplied a
full record. It declined unsupported context filters before broadening the
queue, and declined a retry when the older service could only provide the
result. The 0.5.0 client accepted a changed request under a previously used
operation ID and returned the first result, even against the newer service.
Upgrading both sides matters for retry verification.

We also sent a version-1 proposal from the HTTP service through the current
stdio client, deleted it and created a new proposal under the same ID. The
original review token plus version 1 produced a conflict, and the replacement
remained proposed. A newly observed token survived an unrelated capture and
allowed exactly one acceptance; retrying returned its original result.

## Result size for a populated queue

A second fixture gives 12 delegated tasks three eight-line notes each. This
measurement serializes the complete MCP tool result as compact UTF-8 JSON,
including the outer text block. The last row substitutes full public task
records into the same response envelope to measure the cost of carrying
unrequested detail. Token counts use tiktoken 0.9.0 with `o200k_base`; the
host's prompt and tool definitions are outside the measurement.

| Format | UTF-8 bytes | Tokens |
| --- | ---: | ---: |
| Default text | 773 | 256 |
| Structured ready projection | 1,860 | 513 |
| JSON text for the legacy protocol | 2,156 | 526 |
| Full records in structured output | 48,576 | 13,013 |

The structured queue cost 257 additional tokens over the text queue and
retained IDs, versions, classification and readiness reasons. Sending the full
records would have consumed another 12,500 tokens on this fixture. Actual
cost depends on the workspace, serialization and tokenizer. Reproduce the
byte counts with `uv run python scripts/measure_mcp_views.py`. Add
`--with tiktoken==0.9.0` to the `uv run` options and `--tokens` to the
script arguments to reproduce the tokenizer measurement with Python 3.12.

## Exact source lookup inside a working scope

Two cold workers were asked to check whether a given source reference
belonged to a task in the studio scope. The question and titles were the same
in each run. In one database the reference belonged only to a home task;
in the other it belonged to a studio task. Initially both workers protected
the requested scope and declined to fetch unfiltered detail. Their scoped
text searches could not match source references, so the second worker
missed the existing studio task.

With an exact `source` filter on `find`, fresh workers correctly returned
“No match” for the home-only source and the studio task ID for the positive
case. The latter took a single call. The former used two calls after a
rejected attempt at scoped full detail. Neither opened home detail or
changed a record. In a separate larger queue, a scoped prime followed by
scoped ready returned all eleven eligible studio IDs in two calls. After the
session instructions also distinguished full detail from a scoped source
check, two more fresh workers each used one scoped source search. Their
answers identified the studio task and correctly reported the home-only
source as “No match.”

## Choosing the first read

We compared three primers in 12 fresh Claude Code sessions. Each variant
ran once on each of four requests against a fresh synthetic workspace. The
requests were to find a record by source and attach a note, distinguish a
hoped-for finish from a hard deadline, show two studio decisions for this
meeting only, and resume from a prior activity checkpoint. We checked stored
facts and the returned answer, including whether the studio answer named
unrelated home work.

| Startup | Source and note | Dates | Studio decisions | Resume |
| --- | ---: | ---: | ---: | ---: |
| Full CLI command guide | 5 calls | 4 | 6 | 2 |
| Compact MCP tool guide | 5 calls | 4 | 7 | 2 |
| Native guide with scoped startup and first-read instruction | 5 calls | 5 | 2 | 2 |

The first and third variants passed all four checks. The second correctly
identified both studio decisions but also named a home task. The first
variant read the home task without mentioning it. The third used a scoped
primer followed by a scoped ready query for decisions. Those two tools
returned 518 bytes of text versus 8,130 and 3,940 bytes across the earlier
studio runs. All three left workspace settings untouched. The comparison
changed both wording and scoped startup availability. It cannot isolate
their contributions or predict another host's behavior.
