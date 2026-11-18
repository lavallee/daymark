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
