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

A project can be as plain or as structured as the work needs. A bare project is
a list of tasks on To do, Waiting and Done, in an order the person sets. Stages,
item types, milestones, a goal and custom fields are each optional and
independent. **Do not add stages, types, fields or milestones the person did not
ask for.** Read what a project already has (`daymark plan P`, `daymark project
config P`) and work inside it.

### Seeing a project

| Command | Answers |
| --- | --- |
| `daymark projects` | Every project: done, overdue, blocked, date span, milestones, field totals. |
| `daymark plan P` | The board: each stage with its tasks in order, dates, owners, types, values, milestone and parent; then outside blockers, date conflicts and warnings. |
| `daymark plan P --by milestone\|type\|parent\|assignee\|FIELD` | The same tasks grouped another way. |
| `daymark plan P --tree` / `daymark tree ID [--depth N]` | The hierarchy: epics and what is under them. |
| `daymark goals` / `daymark goal show G` | Goals with progress across their projects and tasks, and what is at risk. |
| `daymark changes P --after N` | Whether anything in the project changed after an event cursor (`plan --json` gives it). |

### Working in a project

```sh
daymark add "Draft the sitemap" --project site --stage doing --assignee Ada
daymark move dm-k3f9 --stage done --after dm-y2yj
daymark edit dm-k3f9 --type bug --milestone beta --set points=3 --set risk=high
```

- **Stages and status move together.** Moving a task to a stage sets its status
  to that stage's status, as `done`, `cancel` or `reopen` would; those verbs move
  the card too. A waiting task stays in an open stage when there is no waiting
  stage; a cancelled task leaves the board unless there is a cancelled stage; a
  proposal is on no board until accepted. Readiness never looks at the stage.
- **Order** is the project's own: `move --after X`, `--before X`, `--first`,
  `--last`. New tasks go last. One move writes one task.
- **Types and parents.** `--type` takes one of the project's types; `--parent`
  nests work under an epic. A parent with open children is not ready. The plan
  rolls up each parent: counts, percent done, effort, span and field totals.
- **Milestones.** A milestone is a task marked `--is-milestone`, dated by its own
  dates. `--milestone M` says a task counts toward it (same project only). The plan
  reports each milestone's progress, the items that end after its date, and when
  all its items are done. It is reached only when someone completes it.
- **Custom fields.** `--set FIELD=VALUE` (repeatable; `FIELD=` clears) with the
  fields the project declares: numbers, yes/no, an option, comma-separated options,
  a date, a URL, text or a person's name. An unknown field or a wrong type is
  refused with the declared list. If a field is later removed, its values are
  kept and the plan lists them under warnings.
- **Dates read as a plan:** start is `--plan`, else `--start`; end is `--target`,
  else `--due`. Moving a plan never moves a deadline. A task that starts before
  something it depends on ends is listed as a date conflict, not moved.
- **`--assignee`** takes a person's ID or exact name. An unknown name is refused;
  add the person first (`daymark add "Name" --kind person`).

### Shaping a project (only when asked)

| Command | Effect |
| --- | --- |
| `project add "Title" [--preset P] [--lead WHO] [--start D] [--target D] [--goal G]` | Create a project; bare unless given a preset or parts. |
| `project presets` | The starting configurations: simple, kanban, milestones, epics, event. A preset writes stages, types and fields once; the project does not remember it. |
| `project set P [--title T] [--lead WHO] [--target D] ...` | Change its facts. |
| `project stages P SPEC` / `types P SPEC` / `fields P SPEC` | Replace one part. `""` removes types or fields, or restores the default stages. |
| `project config P [--apply FILE] [--preset NAME]` | Print the configuration as JSON, or replace parts of it from a file in one write. Copy a configuration between projects this way. |
| `goal add "Title" [--target D] [--measure Label:target[:current[:unit]]]` | A goal; link projects with `project set P --goal G` and tasks with `--goal G`. |

Specs: stages `id:label:status:color,...`; types `id:label[:color][:container]`;
fields `id:label:type[:a|b|c or unit]`, type one of text, number, option, options,
date, bool, person, url. Colours are black, grey, light-violet, violet, blue,
light-blue, yellow, orange, green, light-green, light-red, red or white.

**Time boxes.** There is no sprint entity. When a person wants iterations, use a
milestone per iteration (dated, work counts toward it) or an option field
(`iteration:Iteration:option:i1|i2|i3`). Do not invent another mechanism.

**Managed projects.** `project set P --managed-by SYSTEM` marks a project another
system rewrites. Daymark still accepts writes, but applications that edit plans
should treat it as read-only. Do not set or clear it without the person.

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
definitions total about 5.9 kB, so they cost little context to carry. Results
are the same lean text as the command line. `daymark_view` also reads
`projects`, `plan` (with `id` the project and optional `by`), `goals` and `goal`;
`daymark_add` and `daymark_edit` take `type`, `milestone`, `is_milestone` and
`values`; `daymark_act` with
`action: move` takes `stage` and `place` (`after:ID`, `before:ID`, `first` or
`last`).

## Python

`daymark.Tasks` wraps a `Store` or the HTTP `Client` with the same verbs:
`add`, `propose`, `ready`, `agenda`, `show`, `claim`, `release`, `note`,
`done`, `skip`, `wait`, `resume`, `defer`, `edit`, `block`, `unblock`,
`accept`, `dismiss`, `remind`, `remember`, `find`, and for projects `project`,
`configure`, `config`, `presets`, `move`, `schedule`, `plan`, `projects`, `changes`,
`tree`, `goal`, `goal_plan`, `goals`. It reads the current version
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
