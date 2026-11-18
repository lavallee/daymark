# How Daymark helps a person and their agents work together

A coordinator leaves for a week. When they return, the printer still owes a
proof, a deposit has a cutoff, and an invitation they hoped to draft on
Monday is still unwritten. A new agent should be able to tell them which
deadline passed, whose reply would unblock the proof, and which plan they
can move. The coordinator gets to decide what still matters.

A second question changes the answer. “I have ten minutes” calls for a few
tasks that might fit. “What can you take off my hands?” calls for work the
person has delegated. A five-minute approval could fit the first request
and still require the person's own decision. Daymark keeps those facts
across both conversations.

## What the agent can learn

| What a person tells the agent | What the next session can rely on |
| --- | --- |
| “The printer said the proof will come Friday.” | A waiting task can name the outstanding reply. A note can preserve who said Friday and when. A promise from the printer supplies evidence for a follow-up. |
| “Only show me two choices before this meeting.” | A one-call shortlist changes this answer. The next session begins with its usual settings. |
| “Please use that shortlist for this workspace from now on.” | The agent can save the preference, show the configured limit, and reverse a reviewed change. Other callers of the shared workspace can see it. |
| “That was just for me.” | The host can select a personal recipe for that person on each request. An agent name alone cannot identify which person it represents. |

A method supplies another way to ask about the same records. Someone can
use GTD's contexts to pick phone tasks today and review stalled projects on
Friday. A Things-style plan and deadline can coexist on one task. Someone
else can ask for the ordinary ready list throughout. The examples below
connect those choices to actual Daymark reads.
[Working preferences](working-preferences.md) · [Working methods](research/working-methods.md)

“I'd like to keep that idea around” can also mean the person wants no prompt
to decide. If they already keep reference material elsewhere, use that
destination. If they choose a separate Daymark workspace for ideas, a memory
there stays out of their usual agenda and session startup. They can search
for a phrase later and explicitly ask to turn it into a proposal in their
working workspace. [Worked example](research/capture-clarification.md#keeping-an-idea-out-of-the-working-queue)

## Why life is harder than a backlog

A software backlog has a forgiving shape. Work is ready or blocked, somebody
picks it up, and it closes. A person's commitments do not behave that way:

- **Dates mean different things.** The tax return has a cutoff. The call to an
  old friend has only an intention. Treat both as "due" and the list is soon
  full of overdue items that nobody was ever late for, and the real deadline is
  lost among them.
- **Things recur, in two different ways.** The bins go out on Thursday whether
  or not last week's went out. The filter is due three months after it was
  actually changed. Skipping a week is not the same as doing it.
- **Much of it waits on other people.** The plumber's quote, the school's
  reply, a partner's decision. Waiting work must stay visible without
  pretending to be actionable.
- **Not every suggestion is a commitment.** An agent that reads the mail can
  see a hundred things that might deserve a task. If each becomes one, the
  person stops trusting the list. A suggestion needs to stay a suggestion until
  they say yes.
- **The same person has work.** Repositories, reviews and releases compete for
  the same hours as the dentist and the passport. A system that holds only one
  side forces the person, or their agent, to merge two pictures by hand.

Daymark's answer is to keep each distinction in the record and derive the
simple views from it. `ready` and `agenda` are simple because the model
underneath is not lossy.

## What Daymark learned from Beads

[Beads](https://github.com/steveyegge/beads) is a dependency-aware issue graph
for coding agents. Its README and command reference were read on 2026-11-09;
the tables below summarize that documentation. A later [hands-on comparison](research/agent-journeys.md#running-the-same-dependency-handoff-in-beads) exercised dependency, claim, explanation and close on Beads v1.3.4.

Beads showed what makes a tracker usable by an agent, and Daymark adopts those
ideas directly:

| Idea in Beads | In Daymark |
| --- | --- |
| `bd ready`: only work with no open blockers | `daymark ready`, extended with start dates, deadlines, plans and live claims |
| `bd update --claim`: atomic claim | `daymark claim`, with a lease that lapses if the session dies |
| `bd prime`: workflow and context in one call | `daymark prime` |
| `bd remember`: durable project facts | `daymark remember` |
| `bd onboard` and `AGENTS.md` setup | `daymark onboard [--write FILE]` |
| Short hash IDs such as `bd-a1b2`, child IDs `bd-a1b2.1` | Short IDs such as `dm-k3f9`; rounds of a repeating task are `ID.1`, `ID.2` |
| JSON output everywhere | `--json` everywhere, compacted by default |
| An MCP server | `daymark mcp`, dependency-free |

The two differ in what they are for:

| | Beads (per its documentation) | Daymark |
| --- | --- | --- |
| Centre of gravity | A code repository and the agents working in it | A person, across projects and life |
| Storage and sync | Dolt database with push and pull between clones | One SQLite file; no sync |
| Time | A due date and a defer date per issue | Hard deadline, plan, earliest start and soft target as separate fields, each a civil date or a zoned instant |
| Recurrence | A "patrol" molecule type for recurring operations; no calendar recurrence rules found in the command reference | Fixed-schedule and after-completion repeats, with skip distinct from done and a history of rounds |
| Reminders | None found in the command reference | A durable reminder outbox with leases, retries and stable delivery keys |
| Suggestions | Issues are created directly | A `proposed` status that a person accepts or dismisses |
| Multi-agent scale | Built for many agents and branches, with merge | Claims and optimistic versions within one database |
| Priority | 0 is highest, default 2 | 1 is highest, 0 means not set |

If the work is a codebase with many agents on many branches, Beads is designed
for that and Daymark is not. If the work is a person's commitments, where the
hard part is time, recurrence, waiting and consent, that is what Daymark
models. The two can sit side by side: a Daymark task can carry a source
reference to an issue elsewhere without copying it.

For example, Beads can own the issue “Verify the booklet export.” In Daymark,
the separate commitment “Book the print slot” can depend on a condition
that cites the issue as its source. In a run with Beads v1.3.4 we closed
the issue and read its status. Recording that observation with
`assess --evidence` made booking ready. Retrying the assessment added
nothing. When the issue reopened, recording the new observation held the
booking again. At both transitions the agent had to check the issue and
record the change. Booking the slot remained its own action.

“Keep the cover review as a possibility” poses a different problem. A
Todoist project export can contain two identically named tasks with
different schedules. Ask which original task the person means. With its
ID and their confirmation, Daymark can hold a proposal with Wednesday as
the intended workday and Friday as the deadline. If the person later
renames it, the same source finds the proposal again. The other cover
review and its reminders can stay in Todoist.
[Worked CSV and Things example](research/capture-clarification.md#reconcile-one-external-task)

For a recent test of startup, handoff and task selection, see the
[agent journeys](research/agent-journeys.md).

## One substrate, many owners of truth

Daymark is meant to sit underneath other things: a personal assistant, a
household application, a studio's operating system for its agents. Four
choices follow from that role.

**Reference, do not absorb.** A person's code issues live in a tracker, their
goals in a planning tool, their contacts in an address book. A substrate that
tries to own all of it becomes a stale copy. Daymark entities carry
`source_refs` (system, kind, identifier), and a source can belong to only one
entity. A capture pipeline can therefore run repeatedly without duplicating
work, and the owning system stays the authority on its own records.

**Proposals before commitments.** Anything an agent infers enters as
`proposed`. It never appears in `ready`, never fires a reminder, and becomes
real only through `accept`. Silence is not consent: an unanswered proposal
stays a proposal.

**Whose move it is.** `next_move` separates what the person must do
(`person`), what they must answer or decide (`answer`), and what an agent may
carry out (`agent`). `daymark ready --move agent` is an agent's work queue;
`--move answer` is the short list of decisions only the person can make. The
field is never inferred. Unset means nobody has decided.

**The person's order, and the parts of a life.** `focus` is a hand-ordered
short list that leads `ready`; it is neither a date nor a priority. `scope`
separates household from studio inside one view. A workspace is the boundary
when two bodies of work must never see each other.

**Evidence over inference.** A relative date keeps the phrase that produced
it. Notes are append-only. Every change is an event with before and after
state. An agent picking up a task can see what was said and done; it does not
have to guess.

## What stays above Daymark

These belong to the application or agent harness on top, and Daymark does not
attempt them:

- **Ranking by value.** `ready` orders by time pressure and stated priority.
  Deciding what deserves a person's limited attention today is a judgement for
  the layer that knows their goals.
- **Planning.** Working backward from an outcome to the tasks that achieve it
  produces tasks and dependencies; Daymark stores the result.
- **Capture and interpretation.** Reading mail, messages or repositories and
  deciding what they imply. Daymark receives proposals with source references.
- **Access control between people.** A workspace is the authorization
  boundary. Claims and `next_move` coordinate cooperating actors. The decider
  policy protects one decision, accepting a proposal, and only as firmly as
  the caller's identity is established.
- **Delivery.** The reminder outbox decides what should fire and records what
  happened. Sending a message is a sink the application supplies.

## Open questions

These are known gaps for the shared-substrate role, in rough order of value.
None is promised.

1. **Stronger decision authority.** Events now record the actor, and a
   workspace can name who may accept or dismiss. That is enforced for
   authenticated HTTP callers and advisory for local ones; per-action grants
   beyond accept and dismiss do not exist.
2. **Proposal discipline.** Nothing forces an agent to propose; it can create
   an open task. A policy that makes some actors propose-only is the natural
   next step.
3. **Limits a host system may exceed.** A title is at most 2,000 characters
   and a description 100,000. An application that accepts more must narrow
   its own limits before Daymark can be its only store.
4. **Conditions checked against other systems.** An agent can already
   record that a pull request merged, cite its source and reassess the
   condition if the issue reopens. Automatic source checks and an explicit
   rule for how long an observation stays fresh are open design questions.
5. **Sync between machines.** One file is simple and safe; it is also one
   machine.
