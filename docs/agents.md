# Daymark for agents

Daymark is where an agent keeps a person's tasks, plans and deadlines between
sessions. This page is the whole everyday surface. All examples are synthetic.

## Start of a session

```sh
daymark prime
```

`prime` prints the working rules, the command list, counts of what is ready,
overdue, due today, waiting and proposed, every remembered fact, and the five
most pressing tasks. Read it before anything else. If no database exists it
says so and names the fix: `daymark init` for a per-user database, or
`daymark init --here` for one that belongs to the current folder.

`daymark onboard` prints a short section for an `AGENTS.md` or `CLAUDE.md`
file; `daymark onboard --write AGENTS.md` inserts it, and refreshes it in place
on later runs.

## The rules Daymark holds you to

1. **Never invent a date, a priority or a completion.** Record what is known.
   An unrecognised date is refused; it is not guessed.
2. **A deadline is not a plan.** `--due` is a hard cutoff and the only thing
   that makes a task overdue. `--plan` is an intention that can move without
   anyone having failed. `--start` hides work until it can begin. `--target`
   is a soft finish.
3. **Propose what was not asked for.** `daymark propose` records a suggestion.
   It is not ready work, cannot be completed, and its reminders stay dormant
   until a person accepts it.
4. **Claim, note, finish.** Claim a task before working on it when other
   agents may be active. Leave a note when you stop. The next session reads
   the notes instead of rediscovering the work.

## Reading

| Command | Answers |
| --- | --- |
| `daymark ready [-n N] [--move person\|answer\|agent] [--scope NAME \| --unscoped]` | What can be acted on now, most pressing first. |
| `daymark agenda [--days N]` | Overdue, today, each coming day, waiting, proposed. Each task appears once. |
| `daymark show ID` | One entity: the four dates with the phrase originally used, blockers, what it unblocks, children, reminders, notes, history. |
| `daymark find [TEXT] [--kind K] [--status S]` | Title and description search. |
| `daymark workspaces` | The workspaces in this database, and which one you are in. |
| `daymark projects` | Every project with its roll-up: done, overdue, date span, milestones. |
| `daymark plan PROJECT` | One project as a board: each stage with its tasks in the project's order, dates, owners, and what blocks them from outside. |

A task is **ready** when it is open and not archived, its `--start` date has
arrived, every task it comes `--after` is completed, cancelled or archived, it
has no open or waiting child task, and no other actor holds a live claim on
it. The next round of a repeating task also waits for its planned day. Tasks the person
has put in focus come first, in the order they chose. A
repeating task that has only a deadline is ready as soon as the round exists;
give it a `--plan` day if it should not be done early. Order: overdue
deadlines, due today, planned for today or earlier, due within a week, soft
target within a week, then everything else by date, priority and age.

## Writing

```sh
daymark add "Renew passport" --due 2030-05-03 -p 1 --note "Photos are in the hall drawer"
daymark add "Book flights" --plan fri --minutes 30 --move person --id flights
daymark add "Book hotel" --after flights --parent trip
daymark add "Put the bins out" --every thu --due thu
daymark add "Change the filter" --every "3 months" --after-completion
daymark add "Fix the leak" --waiting-on "the plumber's quote"
daymark propose "Check the smoke alarms" --plan +2w --source mail:message:42
```

| Command | Effect |
| --- | --- |
| `focus ID [--rank N]` / `unfocus ID` | Put a task on, or take it off, the person's own short list. Focus is their ordering, set at their request. It is not a date or a priority, and finishing a task clears it. |
| `claim ID [--minutes N]` / `release ID` | Take or give back a task. A claim lapses on its own (default 60 minutes), so a crashed session cannot hold work forever. |
| `note ID "text"` | Append a progress note with your actor name and the time. Notes cannot be rewritten. |
| `done ID [--note T]` | Complete. A repeating task creates its next round, named `ID.1`, `ID.2`, and so on. |
| `skip ID` | Pass over one round of a repeating task. Nothing is recorded as completed. |
| `wait ID --on WHO [--until D]` / `resume ID` | Park on a person or event. `resume` makes the task actionable now, lifting a start date that is still ahead. |
| `defer ID D` | Hide until a date. The deadline does not move. |
| `edit ID [--title T] [--due D] ...` | Change fields. `--due ""` clears a date. |
| `block ID --on OTHER` / `unblock ID --on OTHER` | Add or remove a dependency. Cycles are refused. |
| `accept ID` / `dismiss ID` | Decide a proposal. This is the person's decision. |
| `remind ID "tomorrow 08:00" --to PERSON` | Schedule a reminder for a person entity. |
| `remember "fact"` / `forget ID` | Keep or retire a durable fact shown by `prime`. |
| `cancel`, `reopen`, `archive`, `undo ID` | Lifecycle and one-step undo. Undo restores the previous fields; it never removes a note or another actor's claim. |

Set who is acting with `--as NAME` or `DAYMARK_ACTOR`; it labels claims and
notes. Set the timezone for relative dates with `--tz` or `DAYMARK_TZ`.

**Dates** accept `YYYY-MM-DD`, `YYYY-MM-DDTHH:MM` (local, or with an offset),
`today`, `tomorrow`, a weekday name (the next one), `+3d`, `+2w`, `+1m`, `+1y`,
and any relative form followed by `HH:MM`. A relative phrase is stored beside
the date it resolved to, so `show` can report what was actually said.

**Repeats** accept `day`, `week`, `month`, `year`, `2 weeks`, `weekdays`,
`mon,thu`, or an RRULE body such as `FREQ=MONTHLY;BYMONTHDAY=1`.

**Priority** runs from 1 (most important) to 4; 0 means not set.

## Projects

A project is a board: an ordered list of stages, each tied to a lifecycle
status. Tasks join it with `--project`.

```sh
daymark project "Website relaunch" --id site \
  --stages "todo:To do:open:grey,doing:Doing:open:blue,done:Done:completed:green"
daymark add "Ada" --kind person --id ada
daymark add "Draft the sitemap" --project site --stage doing --assignee Ada --plan +1w
daymark add "Launch" --project site --milestone --due 2030-05-10
daymark move dm-k3f9 --stage done --after dm-y2yj
daymark plan site
```

| Command | Effect |
| --- | --- |
| `project "Title" [--stages SPEC] [--managed-by SYSTEM]` | Create a project. Without stages it reads as To do, Waiting and Done. |
| `stages PROJECT SPEC` | Replace the stages; `""` restores the defaults. Tasks in a stage that is gone move to their status's first stage; no status changes. |
| `move ID [--stage S] [--after X \| --before X \| --first \| --last]` | Put a task in a stage, at a place in the project's order, or both, in one write. |
| `add`/`edit ... --stage S --milestone` | The same at capture; `--no-milestone` unmarks. |
| `changes PROJECT --after N` | Whether anything in the project changed after an event cursor (`plan --json` gives the cursor). |

**Stages and status move together.** Moving a task to a stage sets its status to
that stage's status, as `done`, `cancel` or `reopen` would. Completing,
cancelling or reopening a task by the usual verbs moves it to the first stage
for its new status. A waiting task stays in an open stage when the board has no
waiting stage. A cancelled task leaves the board unless there is a cancelled
stage. A proposal is on no board: `plan` lists it apart until a person accepts it.
Readiness never looks at the stage, only at the status.

**Stage specs** are comma-separated `id:label:status:color`. Status is `open`,
`waiting`, `completed` or `cancelled` (default open), and a set needs one open and
one completed stage. Colours are black, grey, light-violet, violet, blue,
light-blue, yellow, orange, green, light-green, light-red, red or white.

**Order** is the project's own: `move --after`, `--before`, `--first`, `--last`.
New tasks go last. **Dates** read as a plan: start is `--plan`, else `--start`;
end is `--target`, else `--due`. Moving a plan never moves a deadline.

**`--assignee`** takes a person's ID or exact name. An unknown name is refused;
add the person first (`daymark add "Name" --kind person`).

**Managed projects.** `--managed-by SYSTEM` marks a project another system
rewrites. Daymark still accepts writes, but plan editors such as the toucan
adapter treat it as read-only. Do not set or clear it without the person.

Other tools can show and edit a project through `toucan-source-daymark`; see
[plans for toucan](toucan.md).

## Workspaces and scopes

A **workspace** is a hard boundary. Every command works in exactly one
(`--workspace NAME` or `DAYMARK_WORKSPACE`; `default` otherwise). Tasks,
people, remembered facts, history, exports and the decider policy belong to
one workspace. Nothing in another workspace is listed, counted, searched or
referenced, even when both live in the same database file, and a task cannot
depend on, or be assigned to, something in another workspace. Use separate
workspaces for things that must not mix: a household and a studio, or one
person and another. `daymark prime` names the database and workspace it is
showing, and `daymark workspaces` lists the others.

Applications that embed Daymark usually keep it in their own database, which
separates them further: a household application's tasks are not in the
per-user database at all, so reach them through that application.

A **scope** is a label for filtering inside one workspace (`--scope household`
on `add`, `ready` and `agenda`; `--unscoped` for tasks with none). It keeps one
person's work and home tasks apart in a view while still letting a single
`ready` show both. A scope hides nothing from anyone: use a workspace when
separation matters.

## Retrying safely

Each write verb takes `--op NAME`. Repeating a command with the same `--op`
returns the first result and writes nothing, so a timeout can be retried
without creating a duplicate. Without `--op`, each invocation is a new write.
`--source system:kind:id` gives a second protection for captured items: a
source can belong to only one entity, so capturing the same message twice is
refused with `conflict`.

## Output and errors

Text output is one line per task:

```text
passport  P1  Renew passport  [overdue 2d; due 2030-04-01]
```

`--json` prints the same entities with defaults, nulls and bookkeeping removed.
For twenty ready tasks in a synthetic workspace the full records are 22.9 kB,
the compact JSON 3.1 kB and the text 1.3 kB. `daymark get ID` returns the full
record when you need every field.

Failures go to standard error as `error: CODE: message`, followed by the rule
that was broken. With `--json` the same content is a JSON object.

| Exit | Code | Meaning |
| --- | --- | --- |
| 3 | `not_found` | No such entity. |
| 4 | `conflict` | Someone else changed or claimed it, or the source is already captured. Re-read, then decide. |
| 5 | `invalid` | The request broke a rule; the details name the field. |
| 6 | `no_database` | Run `daymark init`. |
| 7 | `forbidden` | Only a designated decider may accept or dismiss. Leave it for the person. |
| 1 | | Anything else. The message is deliberately generic so stored content cannot leak into logs. |

## MCP

```json
{"mcpServers": {"daymark": {"command": "daymark", "args": ["mcp"]}}}
```

`daymark mcp` speaks the Model Context Protocol over stdio with no dependency
beyond Daymark itself. It offers six tools (`daymark_prime`, `daymark_view`,
`daymark_add`, `daymark_edit`, `daymark_act`, `daymark_remember`) whose
definitions total about 5.5 kB, so they cost little context to carry. Results
are the same lean text as the command line. `daymark_view` also reads
`projects` and `plan` (with `id` the project); `daymark_act` with
`action: move` takes `stage` and `place` (`after:ID`, `before:ID`, `first` or
`last`).

## Python

`daymark.Tasks` wraps a `Store` or the HTTP `Client` with the same verbs:
`add`, `propose`, `ready`, `agenda`, `show`, `claim`, `release`, `note`,
`done`, `skip`, `wait`, `resume`, `defer`, `edit`, `block`, `unblock`,
`accept`, `dismiss`, `remind`, `remember`, `find`, and for projects `project`,
`set_stages`, `move`, `plan`, `projects`, `changes`. It reads the current version
itself and retries when another writer got there first. A refusal that is not
a stale read, such as a live claim held by someone else, is raised unchanged.

## Limits worth knowing

- Every event records the actor that caused it; `show` prints it in the
  history. On the command line the actor is whatever `--as` or
  `DAYMARK_ACTOR` says, so it is a label, not proof. Over HTTP it is the
  principal of the bearer token, which the caller cannot choose.
- `daymark policy --deciders NAME,NAME` restricts `accept` and `dismiss` to the
  named actors; others get `forbidden` (exit 7). Over HTTP this is enforced
  against the authenticated principal. Locally it stops a well-behaved agent
  from deciding on a person's behalf by accident; it cannot stop a process that
  lies about its name or edits the file. `daymark policy --clear` lifts it.
- Claims and `next_move` are coordination, not access control. An agent can
  still create an open task directly; proposing is a discipline the policy
  supports but does not force.
- One SQLite file is the authority. There is no sync between machines.
- The default reminder worker records deliveries; it sends nothing. See the
  [operations guide](operations.md).
