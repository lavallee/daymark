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
