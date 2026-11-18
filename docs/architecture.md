# Architecture: a kernel, strategies, and plugins

Daymark aims to be foundational software for people and agents working
together: a task system that simplifies the complexities of real life. That
needs two things that pull against each other. The data must be stable,
strict and fast, because several systems will build on it. The *way of
working* must vary, because a method that rescues one person buries another.

So Daymark has three layers. This page says what each is for, what is built,
and what is not yet. The evidence is in
[research/working-methods.md](research/working-methods.md).

```text
surfaces     command line · MCP · HTTP · your application
plugins      strategies and data that other packages add
strategies   ways of working: what to offer, what to mention, what to suggest
kernel       what is true, what must hold, what happened
```

## The kernel

The kernel stores facts and enforces invariants. It has no opinion about what
anyone should do next.

It owns:

- **Records and links**: tasks, projects, goals, conditions, people, and the
  references between them, checked on every write.
- **Time**: deadline, plan, earliest start and soft target as different things;
  fixed and after-completion recurrence.
- **History and safety**: versions, idempotent operations, an event log with
  the actor, undo, snapshots.
- **Boundaries and consent**: workspaces that never mix; proposals that are
  inert until a person decides; who may decide.
- **Facts that follow from the above**: whether a task is blocked, started,
  overdue, claimed; which conditions are next for a goal. A fact has one
  definition and every surface uses it.

It does not own ranking, limits, wording, rituals or advice. Where it still
does, that is debt (see below).

**Rules for kernel changes.** A field enters the kernel only if at least three
independent methods or clients need it stored, or if an invariant depends on
it. Anything one method needs belongs to a plugin. A kernel change comes with
an in-place upgrade, a test that upgrades a stored database written by the
previous release (`tests/fixtures/`), and a note for embedding applications.

## Strategies

A strategy is a small, named, parameterised opinion: *offer only three things*,
*chase what has been waiting a week*, *lead with work that unblocks a goal*.
The research found the same kinds recurring across methods and tools, and the
layer is organised around them:

| Kind | Examples in practice | Built in today |
|---|---|---|
| rank | urgency scores, "eat the frog", an assistant's batching of decisions | `decisions-first`, `frontier-first` |
| limit | work-in-progress limits, six tasks a day, capacity checks | `shortlist`, `overbooked`, `time-fit` |
| hygiene | weekly review, staleness warnings, a missing next action | `stale`, `first-step`, `project-review` |
| follow-through | waiting-for lists, nudges | `waiting-check` |
| anticipate | preparation worked backward from a date | `lookahead` |
| tone | neutral ageing in place of red overdue badges | `neutral-aging` |
| ritual, rollover, schedule, decompose | daily brief, migration, time blocking, task breakdown | not yet; see below |

**The contract** (`daymark.strategies`):

1. A strategy reads a `Snapshot` built from the kernel's public reads. It never
   opens the database or imports the engine; a test enforces this.
2. It returns `Advice`: an ordering, notes, and suggestions. It writes nothing.
   A suggestion becomes a task only through `propose`, which a person accepts
   or dismisses. This is the research's clearest instruction: suggest, do not
   decide.
3. It is deterministic for a given snapshot and parameters.
4. It declares what it needs beyond stored tasks (`needs`: a calendar, a
   clock, a language model, a question to the person). The host decides
   whether to provide it. The kernel never calls a model or a network.
5. It states the basis for the underlying practice (clinical, practitioner,
   folk or convention). For example, breaking a task into steps is part of a
   clinical skills-training program. The default number of days before showing
   a first-step prompt is a Daymark design choice. `daymark strategies NAME`
   describes a strategy and its adjustable parameters.

**Profiles** bundle strategies so a way of working is adopted in one step
(`daymark profile gentle`) and then adjusted
(`daymark profile --set shortlist.size=5`). A profile belongs to a workspace.
A saved profile that names a strategy no longer installed keeps working
without it and says so. `plain` preserves the ready order. `assistant`
promotes decisions and work advancing a goal, and flags quiet waiting items.
`gentle` offers up to three choices with first steps when recorded. `dense`
adds review prompts across the ready list. Advice inspects up to the first
1,000 ready candidates and reports when the view exceeds that boundary.
Each profile starts from explicit settings that the person can adjust.

`daymark advise` runs the workspace's profile. `daymark ready` stays the plain
kernel fact, so the two can always be compared.

A preview can adjust the current or named profile for one invocation. A
named copy is saved separately from the active workspace setting. Adopting it
copies the configuration. An actor changing a named copy supplies the version
it read. Active choices also have revisions. Preference writes commit the
configuration and a local replay receipt together. An active change retains
its prior settings for a version-guarded undo. Entity exports omit configuration
receipts. Existing plugins receive a full strategy snapshot by
default. A plugin can declare only the additional snapshot fields it reads
in `Strategy.reads` to avoid unrelated workspace queries. Project roll-ups
are fetched only when a strategy includes the additional `projects` read.
Local advice resolves installed code before the SQLite read transaction and
runs the stages after releasing it. The applicable configuration revision,
candidate rows, counts and project facts agree on one database snapshot and
one evaluation instant.

The authenticated HTTP advice read evaluates shipped strategies in the
credential's workspace. A service-supplied profile is never evaluated by the
client. A host can explicitly select a local extension on a built-in base
for one preview. Older services support client-side previews over individual
reads and cannot report active or named workspace settings.

## Plugins

A plugin is a Python package that offers strategies under the
`daymark.strategies` entry-point group. One is imported only when a profile
names it: installing a package never runs its code by itself. A plugin obeys
the same contract as a built-in. The contributor example builds a wheel and
shows how to try its method on one review. [Add a strategy](strategy-plugins.md)

Not yet built, in order of need:

- **Plugin-owned data.** Custom typed fields exist per project. Generalising
  them to a workspace lets a plugin keep what only it needs (interval counts,
  reward points) without touching the kernel.
- **Capabilities.** `needs` is declared but nothing provides a calendar, a
  clock trigger or a question to the person yet. Rituals (a daily brief, a
  weekly review, rollover) and scheduling wait on these.
- **Suggestions to proposals.** `Advice.suggestions` is returned; a surface
  that turns one into a `propose` with a source reference is the next step.

## Conditions: the path to a goal

Task-to-task dependencies say what must be *done* first. Much real planning is
about what must be *true*: the buyer has signed, the lease is agreed. A
**condition** records that. It belongs to a goal, may require other
conditions, and has a state judged from evidence: met, partly met, unmet or
unknown. Tasks say which conditions they `advance`; a task or condition can
wait on a condition, and waits until it is met.

`daymark explore GOAL` shows the path:

```text
move  Move house
  [x] buyer  A buyer has signed
        evidence: Contract countersigned
  [~] lease  The new lease is agreed  [next; nothing moving it]
  [?] keys   Keys are in hand  [needs lease]
```

The kernel states two facts. A condition is on the **frontier** when it is not
met and everything it requires is: progress is possible there now. A frontier
condition with no open, waiting or proposed task advancing it is a **gap**.
Conditions of a finished goal are on no frontier. Which frontier
condition is the real bottleneck, and what to do about it, is judgement, so it
is a strategy's job; `frontier-first` leads with work that moves the frontier
and names the gaps.

This models the dependency explorer in the commitments system Daymark now
underpins: an outcome, necessary conditions with states, and moves that
advance them. Not carried over yet: a named bottleneck with its reason, kinds
of move (investigate, reframe, stop), revisions of the whole graph with
lineage, and a person's free-form annotations on a canvas.

## Kernel debt

Stated plainly, so it is paid down on purpose:

1. **One record shape for everything.** `Entity` has about seventy fields
   covering tasks, project configuration, goal measures and reminder delivery
   state. Each kind should have its own shape over a small shared base.
2. **Ranking inside the kernel.** `views.urgency` orders `ready` by time
   pressure. That order is the first strategy and should move up a layer,
   leaving the kernel with eligibility alone.
3. **Method inside the kernel.** Project presets (kanban, milestones, epics)
   are bundles of method; stages and types are data. The presets belong with
   profiles.
4. **Limits clients exceed.** A title is at most 2,000 characters. One client
   accepts more. The limit should be the kernel's to state and the client's to
   meet.
5. **Engine size.** Storage and transition rules still share a large module. Read projections, project rules and reminder scheduling already live in smaller modules. New boundaries should make an invariant easier to review and exercise.

## Roadmap

Breaking changes are acceptable while there are few clients; each still ships
with an in-place upgrade and a backup instruction.

| Step | What | Why now |
|---|---|---|
| K1 | Split record shapes per kind; move reminder delivery state out of the task shape | Every later kernel change is cheaper after it |
| K2 | Move `urgency` into a `pressure` strategy; the kernel returns eligibility and facts | Makes the boundary real |
| K3 | Workspace-level typed fields for plugins | Lets methods keep their own data |
| K4 | Stored data the research found in three or more sources and Daymark lacks: actual duration beside the estimate; a parked state; waiting-for detail (asked on, next nudge, count); a day record (capacity, plan, note) | Assistants and several methods need them |
| K5 | Anchors and lead times: preparation tasks generated backward from a dated event, as proposals | The core of anticipation |
| K6 | Delegation levels and standing instructions per kind of decision, with history | Lets authority widen with trust, never by itself |
| S1 | Capabilities: clock, calendar, a question to the person; then rituals (daily brief, weekly review, rollover) | Strategies that are not pure functions |
| S2 | Named bottleneck, move kinds and graph revisions for conditions | Completes the explorer |

## What stays out

- A language model in the kernel. Strategies may ask a host for one.
- Guessing dates, priorities or a person's state.
- Sending anything to another person without a proposal first.
- Hooks that silently rewrite a write. A plugin may advise; the kernel alone
  changes records, through operations with an actor.
