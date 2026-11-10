# Work and life on one substrate

Daymark's aim is a task system that simplifies the complexities of real life.
This page explains what that asks of the design, how Daymark relates to Beads,
the best-known task tracker built for coding agents, and what is deliberately
left to the layers above. It records design reasoning; the
[decisions](decisions.md) table is the contract.

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
it was not installed or run for this comparison, so the statements below are
about its documentation.

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
  boundary. Claims, proposals and `next_move` coordinate cooperating actors;
  they do not stop a hostile one.
- **Delivery.** The reminder outbox decides what should fire and records what
  happened. Sending a message is a sink the application supplies.

## Open questions

These are known gaps for the shared-substrate role, in rough order of value.
None is promised.

1. **Actor on every event.** Notes and claims name an actor; other writes do
   not. A multi-agent system needs to answer "who changed this" from history.
2. **Enforced decision authority.** Today any writer can `accept`. A substrate
   under several agents needs a way to say that only certain actors may.
3. **A person-ordered Today.** Some people keep an explicit, hand-ordered
   short list that is neither a plan date nor a priority.
4. **Conditions checked against other systems.** "Ready when the pull request
   merges" or "done when the form is acknowledged", observed from the owning
   system with a freshness state, as an alternative to dependency edges.
5. **Scope as a first-class filter.** Separating personal from professional
   views today uses workspaces or labels; neither is a perfect fit.
6. **Sync between machines.** One file is simple and safe; it is also one
   machine.
