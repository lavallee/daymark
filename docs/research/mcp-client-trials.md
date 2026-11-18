# Agent handoffs through MCP

Could a fresh agent find explicitly delegated work, claim exactly one task,
read the previous worker’s note, and leave the task open for the next shift?
We tried this through two real MCP hosts with separate synthetic databases.
We checked the task state and event history after each run.

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
