# Daymark for agents

Daymark is where an agent keeps a person's tasks, plans and deadlines between
sessions. This page is the whole everyday surface. All examples are synthetic.

Check `daymark --version`. This guide accompanies `0.6.0`.
For the previous tagged CLI, use the [0.5.0 agent guide](https://github.com/lavallee/daymark/blob/v0.5.0/docs/agents.md).

For a first integration, start with the session and reading sections below.
To connect an editor, run `daymark onboard --mcp-config claude` or
`daymark onboard --mcp-config codex` from the project folder after initialization.
Both print a read-only configuration for the current installation and workspace.
[Connect two agents](mcp.md#connect-two-agents-in-five-minutes)

For common follow-up requests, use these entry points.

| The person asks | Read |
| --- | --- |
| “What still matters after a week away?” | [Reading](#reading) and [handing off unfinished work](#hand-off-unfinished-work) |
| “Just give me two options this afternoon.” | [Ways of working](#ways-of-working) and [personal choices with an end date](working-preferences.md#let-a-personal-choice-expire) |
| “Who has the next move?” | [Writing](#writing) and [goals and conditions](#goals-and-what-must-be-true) |
| “Can you recover my archived item?” | [Bring an archived record back](#bring-an-archived-record-back) |
| “Did that write go through?” | [Retrying safely](#retrying-safely) |
| “Can we bring over two tasks from my Todoist export?” | [Review the CSV and verify the original task IDs](todoist-csv.md) |

## Start of a session

Running `daymark` alone prints a short guide without opening a database.
Use `daymark COMMAND --help` for one command or `daymark --help` for the
complete CLI reference.

In an unfamiliar directory, `daymark where --json` reports the selected
database, selection rule and whether the file exists. It opens no database
and makes no network request. If there is no database, use `daymark init`
for a per-user database or `daymark init --here` for the current folder.
Ordinary task views return `no_database` for a missing selected file or an
unrelated SQLite database. An initialized workspace with no matching tasks
returns an empty result. For two agents starting together, [verify their
read-only connections](mcp.md#connect-two-agents-in-five-minutes).

Set `--tz` or `DAYMARK_TZ` to the person's IANA timezone when their local
day differs from the host's. An unrecognized explicit zone gives an error.

Run `daymark prime` to see the workspace, actor, counts, up to five ready
tasks and five remembered facts, followed by the command guide. A returning
worker can use `daymark prime --brief` for the same session view without the
guide. Titles are limited to 160 characters plus an ellipsis. Use an ID with
`show` for the complete record. Local sessions identify the database and
timezone. An HTTP session uses the workspace and actor bound to its credential.
The ready count includes work claimed by the current actor. The full MCP primer gives native tool arguments.
For an exact source inside a named scope, use `find --source REF --scope NAME`.
A direct `show` reads the full record when that is what the person requested.
Memories and settings are shared within the workspace. Keep a personal
or time-limited display choice with the current request or its authorized
host until the person agrees to share it.

For a machine-readable pickup use `daymark prime --brief --json`. On a local
database or current service the counts, ready work, memory previews and
activity checkpoint come from one read. Save `activity_checkpoint` and pass
it to `activity --after CHECKPOINT` to read subsequent changes. The UTC
instant in `as_of` governs dates and leases. Older services compose separate
reads and can omit the checkpoint. The full `prime --json` form lists active
memory titles.

For “which hard deadlines are left tonight in Honolulu?”, use
`daymark --tz Pacific/Honolulu agenda --local-day --days 0`. Timed cutoffs
in `today` run from the evaluation instant to the end of the viewer's day,
ordered by the actual instant. Earlier cutoffs stay in `overdue`. The JSON
rows preserve the recorded clock and zone and include `viewer_deadline` in
the selected zone. A repeated hour includes its offset.

Date-only cutoffs have no clock. Upcoming and current civil days appear
under `civil_dates` when any part of that originating day overlaps the
window. On a Tuesday evening in Honolulu this can include a Wednesday
date in Tokyo. A civil date that has ended in its own zone is overdue.
Use `--days N` to include the next N viewer dates and keep any scope or
context filters. The ordinary `--deadlines` agenda counts days in each
record's own zone.

To give a host the matching skill, use `daymark onboard --skill`. For a
project instruction file, `daymark onboard` prints a short startup snippet
and `daymark onboard --write AGENTS.md` maintains its marked section.
`daymark onboard --check AGENTS.md` exits 1 for missing or outdated
instructions. A malformed section needs inspection. The same commands
accept `CLAUDE.md`. Work that belongs to another tracker can stay there.
Use a source reference for the related Daymark task.

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
| `daymark ready [-n N] [--project ID] [--move person\|answer\|agent] [--scope NAME \| --unscoped]` | What can be acted on now, most pressing first. A project selects its explicitly linked tasks across scopes. |
| `daymark --as WORKER take --project ID` | Claim one unclaimed, ready, explicitly delegated member of this project in one transaction. Scope and context filters can narrow it further. |
| `daymark agenda [--days N] [--deadlines]` | Overdue, today, each coming day, waiting, proposed. With `--deadlines`, show only accepted live tasks whose hard deadline is overdue, today or within the horizon. |
| `daymark show ID` | One entity with its dates, blockers, dependents, children, reminders, notes and history. For tasks it explains each fact keeping the item out of this actor’s ready list. |
| `daymark find --status proposed` | Pending task proposals. Use `ready --move answer` for tasks awaiting a decision. |
| `daymark find [TEXT] [--kind K] [--status S]` | Title and description search. Add `--archived` for archived records only. |
| `daymark workspaces` | The workspaces in this database, and which one you are in. |
| `daymark projects [--scope NAME]` | Projects with done, overdue, date span and milestones. A scope selects projects by their own scope and counts all their members. |
| `daymark plan PROJECT` | One project as a board: each stage with its tasks in the project's order, dates, owners, and what blocks them from outside. |

If a queue looks unexpectedly short, repeat `ready` with `--explain` and
the same filters. The answer counts distinct ready and held tasks, with up
to three example IDs per holding reason. Open work can have several reasons.
Proposals and waiting tasks are counted under the decision or reply they need.
Completed, cancelled and archived work is outside this view.

For a long search, use `daymark find TEXT --kind memory --page --limit 10 --json`.
Each hit gives an ID, title, kind, status, version and any scope. Read a full
record with `show ID`. When the page returns `next`, repeat the search with
the same filters and `--after ID`. Results follow ID order within this
workspace. Newly inserted records before a saved cursor appear on a new search.

Plain and paged searches match title and description using the same Unicode
case folding. For example, `STRASSE` finds `Straße`. Plain text keeps dates,
live claims and other listing details. An ordinary `find TEXT --json`
returns an array of matching records. Add `--page` for compact hits and a
continuation. Python’s `tasks.find(text, limit=3)` returns up to three full
stored records. The current service selects their IDs and reads the result
in one snapshot. A rare substring can still require scanning the candidate
titles and descriptions. An older server can require the client to filter
a larger entity query.

Use `daymark find TEXT --archived` to recover an archived task or add
`--kind memory` for a retired fact. This implies `--page` and marks every
hit as archived alongside its stored status. Keep `--archived` on subsequent
pages. In MCP use `daymark_view` with `view: "find"` and `archived: true`.
Archived search is separate from `--claimed`. Upgrade an older HTTP service
if it refuses archived search.

A task is **ready** when it is open and not archived, its `--start` date has
arrived, every task it comes `--after` is completed, cancelled or archived, it
has no open or waiting child task, and no other actor holds a live claim on
it. The next round of a repeating task also waits for its planned day. Tasks the person
has put in focus come first, in the order they chose. A
repeating task that has only a deadline is ready as soon as the round exists;
give it a `--plan` day if it should not be done early. Order: overdue
deadlines, due today, planned for today or earlier, due within a week, soft
target within a week, then everything else by date, priority and age.

For a repeating task, `show ID --brief` names the round, cadence, time zone
and date that advances. If both a plan and a hard deadline are present, the
next occurrence advances the plan and copies the existing deadline. For a
weekly task with a fresh deadline each week, give it just the recurring due
date. If the person asks for both dates to move independently, clarify their
intended schedule before adding the series. [Recurring work examples](research/recurring-work.md)

After several missed fixed slots, confirm which rounds actually happened.
A skip advances one round. A completion-based series schedules from the
instant the skip or completion takes effect. Use `show ID --forecast skip`
or `show ID --forecast done` locally or with `--url`. In MCP, call
`daymark_view` with `view=show` and `forecast=skip` or `forecast=done`.
Both accept an exact source in place of the ID. The forecast covers one fresh
transition at the displayed UTC instant and task version. It reports the
projected plan and deadline, end of the rule, or a reserved slot. A working
claim is advisory. Confirm the holder's progress before acting. Refresh before
deciding because time and slot occupancy can change independently of the task
version. On a service without forecast support, explain the recorded rule from
ordinary show when available, and confirm what happened in activity or events
after an authorized change. Reminders belong to one occurrence. Schedule a new
notice on the successor when requested. To correct a completed or skipped
round, inspect its record and the following occurrence. Reopening the earlier
record keeps the later occurrence and its evidence.

Adding a waiting reason to an open task with `edit ID --waiting-on WHO` parks
that task immediately. A proposal keeps its waiting reason for acceptance.
Changing or clearing the reason on a waiting task keeps it waiting. Use
`resume ID` when the person says the wait has ended. A delayed wait or resume
on completed, cancelled or archived work returns a conflict. Check the
current handoff before restarting work. Use `unarchive` to restore visibility
or `reopen` to explicitly restart a finished task.

When acting on a reply from an earlier session, carry the version and token
from that session’s `show ID --brief` into `resume ID --if-version N --if-review TOKEN`.
The same flags work on `wait`. If another person changed who owes a reply,
Daymark leaves the newer wait in place. Compare it with the incoming reply
before deciding whether it applies.

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
| `claim ID [--minutes N]` / `release ID [--note TEXT]` | Take or give back a task. A claim lapses on its own (default 60 minutes), so a crashed session cannot hold work forever. |
| `note ID "text"` | Append a progress note with your actor name and the time. Notes cannot be rewritten. |
| `done ID [--note T]` | Complete. A repeating task creates its next round when the rule supplies one. Its event in `activity` gives the actual ID, including when a usual round name was already in use. |
| `skip ID` | Pass over one visible open or waiting round of a recurring task. Use `cancel ID` to retire a one-off task. |
| `wait ID --on WHO [--until D]` / `resume ID` | Park on a person or event. `resume` makes the task actionable now, lifting a start date that is still ahead. |
| `defer ID D` | Hide until a date. The deadline does not move. |
| `edit ID [--title T] [--due D] ...` | Change fields. `--due ""` clears a date. |
| `block ID --on OTHER` / `unblock ID --on OTHER` | Add or remove a dependency. A loop of explicit dependency links is refused. |
| `accept ID` / `dismiss ID` | Decide a proposal. This is the person's decision. |
| `remind ID "tomorrow 08:00" --to PERSON` | Schedule a timed reminder for an existing person or principal. Check its recipient, channel and pending state. The default worker records locally. |
| `remember "fact"` / `forget ID` | Keep or retire a durable fact shown by `prime`. |
| `cancel`, `reopen`, `archive`, `unarchive`, `undo ID` | Lifecycle, status-preserving restoration and one-step undo. Undo restores the previous fields; it never removes a note or another actor's claim. |

Before resuming a waiting task, `show ID --brief` explains the effect on
reminders at the time of the read. If the start is still in the future,
resuming clears it and cancels every active reminder tied to this task.
If there is no future start to clear, those reminders keep their schedule.
The task's hard deadline is preserved in both cases. Full `show ID`
lists the reminders and their current states. A reply arriving elsewhere
needs someone to record it in Daymark.

After scheduling, use `daymark show REMINDER_ID --brief` to see its time,
recipient, channel and worker state. A pending reminder tied to a proposal
waits for acceptance. A delivered reminder on the recording channel was
recorded locally. Relative reminder requests remember their expression and
timezone so the same operation ID can recover the original instant after
midnight or a snapshot transfer.

Set who is acting with `--as NAME` or `DAYMARK_ACTOR`; it labels claims and
notes. Set the timezone for relative dates with `--tz` or `DAYMARK_TZ`.

**Dates** accept `YYYY-MM-DD`, `YYYY-MM-DDTHH:MM` (local, or with an offset),
`today`, `tomorrow`, a weekday name (the next one), `+3d`, `+2w`, `+1m`, `+1y`,
and any relative form followed by `HH:MM`. When a person names a day and
month unambiguously, pass the ISO date. If an external notice says
`07/08/2030` without a date order, ask which day it means before editing.
A stored relative expression is the text the caller passed to Daymark.
An ISO date alone cannot recover the person’s original wording.
A clock time skipped by a timezone change is refused. When a
clock reading occurs twice, ask which occurrence the person intends and use
the matching numeric offset. For example, `2030-04-07T01:45+11:00` and
`2030-04-07T01:45+10:30` identify different instants on Lord Howe Island.
Full `show --json` includes a `temporal` object with stored dates or instants,
the IANA zone and any recorded expression.

**Repeats** accept `day`, `week`, `month`, `year`, `2 weeks`, `weekdays`,
`mon,thu`, or an RRULE body such as `FREQ=MONTHLY;BYMONTHDAY=1`. Without an
explicit first plan or deadline, a new recurring task starts on the first
matching local day on or after today. Editing an undated one-off with
`--every` enrolls its existing ID as the first round. An explicit off-pattern
first date counts toward a finite `COUNT`.

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
  dates. `--milestone M` says a task counts toward it (same project only; a
  milestone does not belong to another milestone). The plan
  reports each milestone's progress, the items that end after its date, and when
  all its items are done. It is reached only when someone completes it.
- **Custom fields.** `--set FIELD=VALUE` (repeatable; `FIELD=` clears) with the
  fields the project declares: numbers, yes/no, an option, comma-separated options,
  a date, a URL, text or a person's name. An unknown field or a wrong type is
  refused with the declared list. If a field is later removed, its values are
  kept and the plan lists them under warnings. Numbers must be finite; whole
  numbers are exact up to 9007199254740991. Through the API, an update's
  `values` merges per field (null clears one), like `--set`.
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

## Ways of working

`daymark ready` lists eligible work in its ordinary order, 20 rows by default. For an agent reading a queue, `ready --brief --json` returns short titles, IDs, versions and ordering reasons. The MCP ready view with `json=true` gives the same rows in `items`. Open a selected task with `show ID --brief` for notes. A
workspace can also have a **profile**, a bundle of strategies that shapes what
you offer the person:

```sh
daymark strategies            # what exists, and how well each idea is supported
daymark advise --profile gentle --set shortlist.size=2    # try it once
daymark profile gentle --set shortlist.size=2             # save it for this workspace
daymark advise                # picks, then what the strategies noticed
```

`advise` never writes. Its notes are things to raise with the person (how many tasks need a decision and which fit this discussion batch, or a deadline
in nine days with no day planned for it), and a note names the tasks it is about. If the profile holds tasks
back it says how many. When `ready_coverage` is present, the profile
considered only `reviewed` of `available` ready tasks. An empty time-fit
result then applies to that subset. A relevant scope or context can narrow
a subsequent read. Choose or change a profile only when the person asks:
it is their way of working, not yours.

An authenticated service uses the active profile in the credential's workspace.
It also accepts a named profile or one-call adjustment. Service advice runs
built-in strategies and lists any saved stages it could not use. A v0.5.0
service cannot return its profile. The current client labels its plain fallback.
Standing preferences are written on the local database.

A capacity warning considers ready tasks due or planned through today and,
by default, other focused ready work. The note identifies the contribution
from focus. Use `overbooked.include_focus=false` for someone who uses focus
across days. The agenda retains plans that are blocked.

A pick can be a decision for the person. Consult its recorded next move before
acting. To select work explicitly delegated to an agent, use `ready --move agent`.

Treat “just show me two today” as a one-call instruction. Try
`daymark advise --set shortlist.size=2` and leave the saved profile alone.
If the person says “always show me two here,” `daymark profile --set shortlist.size=2`
applies the same adjustment to the workspace. To leave a strategy out of a
preview, use `advise --drop STRATEGY`. Through MCP, pass `view="advise"` with
optional `profile`, `set` and `drop` on `daymark_view`. The last two take
lists of strings. [Working with a person's preferences](working-preferences.md)
gives examples of turning feedback into one reversible change.

For a short check-in, present the picks and the count held back. If the person
asks whether anything omitted is due today in their timezone, check `agenda --local-day --days 0`
in the same scope. Name the verified exception separately. A date-only deadline
has no recorded hour.

When someone asks to reuse a configuration occasionally, `daymark profile
--save-as NAME` saves a named copy in the local workspace. Inspect it with
`daymark profile --saved NAME` and use it with `advise --profile NAME`. To
change or remove an existing copy, pass the version you just read using
`--if-version N`. MCP's `daymark_view` accepts `view="profiles"` with an
optional `profile` name for inspection. The catalog lists available adjustments.
For an occasional project review, try `advise --profile plain --set project-review
--set shortlist.size=1`. It identifies open projects without an open or waiting
task and distinguishes proposal-only projects. Review all contexts together.
Use `daymark strategies NAME` or the MCP profiles view with `strategy=NAME`
to inspect a parameter, its default, and its supported range.

Once the person asks for a standing change, pass the active `--if-version`
from `daymark profile --json` and a fresh `--op`. The result identifies the
workspace and new version and includes a guarded undo command. Retrying the
same request returns the recorded result. Re-read the active profile for its
current value. An MCP-only agent can submit the exact `restore_call` from
the preference receipt. Named saves and removals accept replay IDs as well.

## Goals and what must be true

Some planning is about what must be *true*, not what must be done. Record that
as a condition on a goal, say how it stands, and link the work that moves it:

```sh
daymark condition "A buyer has signed" --goal move --id buyer
daymark condition "Keys are in hand" --goal move --requires buyer
daymark add "Chase the solicitor" --advances buyer
daymark assess buyer met --evidence "Contract countersigned"
daymark explore move
daymark explore move --scope studio
```

`explore` marks each condition met `[x]`, partly `[~]`, unmet `[ ]` or unknown
`[?]`, shows which are next, and says where nothing is moving one. A task that
comes `--after` a condition is not ready until the condition is met. Assess a
condition only from evidence, and say what the evidence is; "unknown" is an
honest state.

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

Use `edit ID --assignee PERSON_ID` to identify who is responsible, including on
a task outside a project. MCP capture and edit accept `assignee`. Find the
existing person or principal by kind when exploring. For a name-only assignment,
pass the supplied name as given so both kinds are checked. If multiple records
match, ask which ID the person intends. The assignment persists through later claims and releases.
An empty assignee clears the link.

To check who may decide a pending proposal from this connection, inspect
`show ID` or its brief. It reports whether the workspace policy allows your
current actor to accept or dismiss, and whether the policy restricts decisions
to named deciders. A read-only MCP session also marks its transport restriction.
Other deciders’ identities stay out of these views. Keep the proposal pending
until the person has actually made the decision.

To ask whether a known external source belongs in a particular scope, use
`daymark find --source catalog:proof:Q-41 --scope studio --json`. The same
filter works in MCP `view=find`. An empty page means no record matched all the
filters. Use `--kind memory` for a remembered fact and `--archived` when the
question concerns a retired record. `show --source` gives full detail when
that is within the requested scope of work.

## Retrying safely

Each write verb takes `--op NAME`. Repeating a command with the same `--op`
and the same underlying change returns the first result. A reused key on a
different entity, action or kernel payload returns `conflict`. Daymark checks
the original actor where event history records one. Use a fresh key for each
new intent and a stable entity ID for assignees you expect to retry. If a remote write’s outcome is uncertain, the CLI provides the operation ID in its error details and in JSON `error.operation_id`. Repeat the same command and flags against the same workspace and credential with that ID as `--op`. A new invocation without the key starts a new write. Supply your own key before the call when the caller could lose the entire CLI output.
`--source system:kind:id` gives a second protection for captured items: a
source can belong to only one entity, so capturing the same message twice is
refused with `conflict`. Recover that record with
`daymark show --source system:kind:id --brief`. The exact source identifies
the original capture. Read the stored proposal before deciding whether a new
message expresses the same intent. A source can still belong to an archived
or dismissed record. Lookup uses only the current workspace.

Use `tasks.show(source="catalog:drawing:page:42")`, `tasks.brief(source=...)`
or `tasks.read_note(None, note_id, source=...)` in Python. The ID component
can contain colons. For a source with punctuation in the system or kind,
Python also accepts a dictionary with `system`, `kind` and `id`. HTTP accepts
each component separately at `GET /v1/source?system=…&kind=…&ref_id=…`.
Set `detail=brief` for a bounded handoff or `detail=note&note_id=…` for a
specific note. The credential selects the workspace.

An edit based on an earlier read can require that exact version. If `show`
reported version 3, `daymark edit ID --if-version 3 --priority 2` applies
only while version 3 is current. On `conflict`, read the task again before
deciding whether the edit still makes sense. In Python and MCP the argument
is `expected_version`.

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

For a status check or review, `daymark mcp --read-only` advertises only
`daymark_prime` and `daymark_view`. Direct write calls are refused. This opens
a local database read-only and also works with an HTTP-backed MCP session.

For a service connection, prime shows the workspace and actor supplied by the
authenticated service. A capture note and a follow-up note record that actor
regardless of a local display label. An older service can identify the actor in
the create event while leaving a new capture note unattributed.

For a workspace-bound client configuration, a handoff example, write receipts
and retry behavior, see [Use Daymark from an MCP client](mcp.md).

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

For a hard-deadline check, run `daymark agenda --deadlines --days 2` or call
`daymark_view` with `{"view":"agenda","deadlines":true,"days":2}`. The result
includes waiting and blocked work, grouped under overdue, today or the
deadline’s local date. Scope and context filters compose with this filter.
The horizon is inclusive in each deadline’s own time zone. A timed deadline
earlier than the evaluation instant is overdue, even on the same local day.
`--days 0` includes overdue and today.

## Python

Use `tasks.agenda(days=2, deadlines=True)` for the same read in Python.
The HTTP client checks the service schema on first use of the filter and
refuses the read if the service lacks support.

For a local database import `Store` and `Tasks` from `daymark`. For an
existing HTTP service install `daymark-core[client]` and import `Client`.
The credential chooses the workspace and actor. The source archive includes
the Python examples under “Embed in your transaction” in `README.md`.
[Embedding example in 0.5.0](https://github.com/lavallee/daymark/blob/v0.5.0/README.md#embed-in-your-transaction)

`Tasks` wraps these connections with the same verbs:
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
  lies about its name or edits the file. For a local administrative handover,
  read `policy --versioned`, then use `policy --deciders NAME,NAME --if-version N
  --operation-id ID` (or replace `--deciders NAME,NAME` with `--clear` to allow
  any writer). Repeat the same write to recover a lost result and read again
  for current authority. [Local proposal deciders](operations.md#review-and-change-local-proposal-deciders)
- Claims and `next_move` are coordination, not access control. An agent can
  still create an open task directly; proposing is a discipline the policy
  supports but does not force. General lifecycle changes and snapshot restore use
  workspace write authority. A person seeking evidence of approval should
  inspect the recorded decision and its provenance. [Proposal policy scope](operations.md#what-the-proposal-policy-enforces)
- One SQLite file is the authority. There is no sync between machines.
- The default reminder worker records deliveries; it sends nothing. See the
  [operations guide](operations.md).


## Remember what the person actually said

A remembered fact belongs to the whole workspace. Every worker using that
workspace can read it. Use the person’s words and include who or what the
preference concerns when that matters. A scope such as `print` records where
the fact applies and appears beside its startup preview. It grants no privacy.
Use a separate workspace when the audience should differ.

For “Use a short legend on printed proofs from now on,” record the stated
convention. Include a source reference only when you have an actual source.

```sh
daymark remember "Use a short legend on printed proofs" --scope print --source review:request:42
daymark find "short legend" --kind memory --scope print --page
```

Prime previews five active facts in ID order. Follow its cursor or search all
active facts by text and scope. A repeated phrase in a single conversation,
a guess about someone’s personality and a one-time request supply different
evidence from a stated ongoing preference. For “show two choices this time,”
use a one-call view setting. For a request to change how future advice is
ranked, use the workspace profile. A memory provides context in prose.

When the person corrects the convention, inspect the matching fact with
`show ID --brief --json`, then edit its title with the returned
`--if-review TOKEN`. Keeping the same ID preserves its history. If the
person retires the convention, `forget ID --if-review TOKEN` archives that
memory. A concurrent correction causes a conflict so the latest wording can
be reviewed. Archived facts leave startup and ordinary search. Their text,
source references and history remain inspectable by ID or exact source, and
remain in native exports and existing backups. A request for permanent
erasure requires separate handling.

## Bring an archived record back

When a person wants to revisit an idea, recover the exact record. If it has
a source, run `daymark show --source proofs:cover:07 --brief --json`. Use its
returned `id` and `review_token` with `daymark unarchive ID --if-review TOKEN`.
If the record has changed, read it again. If a sparse native restore left
its review token empty, recover the original event history when the exact
revision matters. A shared title alone is insufficient
to identify the person's original request.

Unarchive restores visibility. A proposal returns to the proposal list for
a separate acceptance or dismissal. Accepting a hidden proposal produces a
conflict. A designated person can still dismiss it while archived. A waiting
task retains its reason and remains waiting. Completed and cancelled tasks
keep their status. Use `reopen` when the person intends to restart work.

Restoring an unfinished prerequisite can block its dependents again. Archiving
clears claims and focus and cancels pending reminders about the task. To
resume those arrangements, claim the task, focus it, or schedule a new
reminder as the person requests. Restoring a forgotten memory makes its
recorded fact available to the workspace again. Unarchive preserves notes,
source references and history. Archiving a reminder that already recorded a delivery,
acknowledgment or dismissal preserves that outcome. A failed delivery still
has a pending retry. Archiving it cancels the retry and keeps the recorded
failure, attempt count and delivery history.

Python uses `tasks.unarchive(id, review_token=token, op=operation_id)`. MCP
uses `daymark_act` with action `unarchive`, the exact ID and the same optional
guards as `accept`. Both use a versioned update of the archive flag. A new
client can restore by ID and version on a 0.5.0 service and refuses a hidden
acceptance there. An older service may require an upgrade to verify an
identical retry. Inspect the current record before taking further action.

## Hand off unfinished work

Give concurrent workers distinct actor names. A claim hides the task from other
actors’ ready lists until its holder releases it or the lease expires. Anyone
who knows the ID can read the holder, expiry and progress in `daymark show ID`.
The holder coordinates pickup. Other workspace writers can still edit, park or
complete the task by ID. When a returning worker sees a new holder, read the
handoff and coordinate with that holder before changing the work.

```sh
daymark --as writer claim draft
daymark --as writer release draft --note "Checked the first two sections. Review the index next."
daymark --as reviewer show draft
daymark --as reviewer claim draft
```

The release and its note commit together. If another worker already holds the
lease, the command reports a conflict and writes nothing. Use
`done ID --note TEXT` when the work is complete. It records the note and
completion together. Retry either write with the same `--op` value if its
reply is lost. In MCP, pass the note as `text` to the `release` or `done`
action.

If a tab may act later on the claim it reviewed, keep the task version from
`claim` or `show`. Pass it to `release ID --if-version N` or
`done ID --if-version N`, including when adding a note. Renewing or
retaking the claim changes that version even when both tabs use the same
actor. A conflict leaves the note and action unapplied. Read
`show ID --since-version N` and check the current holder and handoff
before choosing what to do. A lease expiring by the clock alone leaves the
task version unchanged. The check compares a revision of this record and
cannot distinguish a restored copy with the same ID and version.

Retry an already committed release with its original operation ID, note
and version. It returns its original result, even after someone else has
claimed the task. A new operation ID with the old version conflicts. A
fresh release using the current version can release the new claim if this
actor holds it. A current client can send the version check to a 0.5.0 service.
That service can refuse an identical retry because it cannot verify the
original request. Inspect the task before taking another action.


Before an interruption, record the last check someone could verify, where
to find its evidence and the next specific action. For example, “Sheet 6
measures 14 mm with gauge K-17. On sheet 7 align the overlay at F3. The
inner margin is still unmeasured.” Use `note ID TEXT` while the work is
underway so an unexpected stop still leaves a trace.

Returning to one task? Start with `show ID --brief` or
`show --source REF --brief`. Follow the printed older-note and full-note
reads to recover evidence. In service mode, append the printed `show`
arguments to your original service command so the endpoint and bearer
credential still select the same workspace. Check later corrections before
acting. Report who holds the claim separately from what the notes say was
checked. If the record gives no stopping point, say so and ask the worker
or inspect the referenced artifact.

### Pick up changes across sessions

`daymark activity` shows the latest 20 changes in this workspace, oldest
first. Each line identifies the task, change and recorded actor. A note
includes the start of its text. Use `daymark show ID` for the full record.

Save the `activity_checkpoint` from `prime --brief --json`. Pass it to
`daymark activity --after CHECKPOINT --json`, then keep the returned
`checkpoint` for the next page or session. While `cursor` is below `head`,
more events are ready. Set `--limit N` for 1–100 events per page. If the
workspace or the checkpoint's event changed, run `prime --brief` to review
current work and save a new checkpoint.

Python callers pass the string as `tasks.activity(after=checkpoint)`. HTTP
accepts it in `GET /v1/activity?after=CHECKPOINT`. In MCP pass it as the
`after` string to the `activity` view. Existing integer cursors still work.
Use `--after 0` to read from the beginning, or omit `--after` for the latest
page. Save the workspace identity alongside an integer cursor. An older
service may support only the integer form.

After a native restore, `show ID` lists history in workspace event order.
`undo ID` restores the prior state only when the latest event describes the
current record. A conflict can mean the restore supplied the entity without
its matching event. Inspect the record and recover the missing native history
before trying an undo.

To undo the latest change you actually saw, pass `--if-version N` and
`--if-review TOKEN` from the detail view. The corresponding MCP `undo` action
accepts `expected_version` and `review_token`. A concurrent change during
undo produces a conflict. Inspect the new state before issuing a new intent.
If the person asks to restore one older field, compare the relevant revision
and use a guarded edit for just that field. A later change to the same field
may require the person to choose which value to keep.

### Let several workers take from one queue

Mark the tasks an agent may do with `--move agent`. Give each local worker
its own actor name. The following command selects the first unclaimed ready
task in the proofs scope and claims it in one transaction.

```sh
daymark --as proof-worker-2 take --scope proofs --minutes 30 --op pickup-42
```

Add `--context ID` when the worker needs a particular context. Scope and
context filters both have to match. The normal ready order applies, including
the person's focus order. `take`
passes over every live claim, including the worker's own. It also passes
over a person's tasks, decisions and tasks whose next move is unset. Use
`claim ID` when choosing a particular task. Over HTTP, the worker identity
comes from its bearer token.

A returning worker can run `daymark --as proof-worker-2 find --claimed`
to recover every task it still holds, including tasks now waiting or blocked.
Each hit includes its version and lease expiry. Open one with `show ID --brief`
before resuming. Use `--scope`, `--status` or `--after` to narrow or continue.

An empty queue succeeds with JSON `null`. A retry with the same operation ID
returns the original result, even if work has since become available or the
lease has expired. Start a new selection with a new operation ID. An
operation ID reused by another worker or with different selection parameters
gets a conflict. In MCP, call `daymark_act` with
`{"action":"take","scope":"proofs","op":"pickup-42"}`.

### Read a task with a long history

`daymark show ID --brief` returns the current version, readiness, live claim,
next move and dates. It previews the title, description and three latest notes
at up to 240 characters each. Each note carries its original ID, author and
timestamp. The view shows up to five unresolved dependency IDs, five active
child IDs and the first three IDs each for labels and contexts. The
`omitted` counts identify additional entries. Use full `show ID --json`
to verify fields outside the brief before reporting them as absent.

When a task’s outstanding prerequisite or unfinished child leads back to the
task, detail can also show a circular wait. For example, `bind → child collate
→ prerequisite dry → prerequisite bind` means each of those tasks is holding
up the previous one. Check the recorded relationships with the person. If
drying the ink can start before binding, `daymark unblock dry --on bind`
removes just that prerequisite. If the containment is incorrect, change the
parent instead.

The `readiness.cycle` field carries one witnessed path, with each step naming
its relation and destination ID. The search follows visible open or waiting
tasks across scopes in this workspace. It visits at most 20 tasks, follows
at most six links, and samples the first eight references in each direction
at a visited task. When another link needs investigation, open that task’s
detail. Ordinary ready and take use their usual eligibility rules.

```sh
daymark show proof --brief --json
daymark show proof --note NOTE_ID
daymark show proof --before-note NOTE_ID --json
```

Read a full note using its ID. Use the returned `next_before_note` to page
backwards through earlier notes. New notes appended by another session do
not change which notes precede that ID. An unqualified `show` continues to
return full task details.

Python exposes `Tasks.brief(id, before_note=...)` and
`Tasks.read_note(id, note_id)`. HTTP serves these reads at
`GET /v1/brief?entity_id=ID` and `GET /v1/note?entity_id=ID&note_id=NOTE_ID`.
In MCP, use `daymark_view` with `view: show`, the task `id`, and one of
`brief: true`, `note: NOTE_ID` or `before_note: NOTE_ID`.


For an embedding application that needs a bounded ready list,
`tasks.ready_summary(limit=5)` returns the same IDs and order as `ready`,
plus the task's title, version, scope, next move, dates, priority and live
claim. Titles longer than 160 characters carry an ellipsis. Over HTTP use
`GET /v1/ready?brief=true&mine=true`.

Both `show` and `show --brief` name the first five unresolved prerequisites
in dependency order and report their statuses. Use the returned IDs for full
detail. A brief reports the number of additional prerequisites.

### Decide what was actually proposed

Read `daymark show ID --brief` for the proposal's source, notes, version and
any recorded wait. A person who approves version 3 can run
`daymark accept ID --if-version 3`. To decline that version, use
`daymark dismiss ID --if-version 3`. A concurrent edit produces a conflict.
Compare the terms with `daymark show ID --since-version 3`. This shows every
field whose current value differs from the reviewed record, plus attributed
note additions or removals. Long changes carry excerpts around the first
difference. A missing or ambiguous historical snapshot is reported explicitly.
Read the revised proposal with the person before deciding again. The
workspace's decider policy applies to both commands.

For a decision that may outlive the task itself, retain the `review_token`
from that original `show` or `brief`. Pass it as `--if-review TOKEN` on
`accept`, `dismiss` or `edit`. A record that was hard-deleted and recreated
can start again at version 1. A version number alone cannot distinguish those
lifetimes. The token identifies the observed event and its recorded contents
in this workspace. It survives an intact native transfer and unrelated
changes to other records. Restoring different terms under the same operation
ID, event number, version and timestamp requires a new review. If an imported record lacks
the event for its current state, the token is null. A fresh decision on a
replacement task requires a new review.

An accepted proposal with a named wait enters the waiting list. It becomes
actionable when resumed and its other readiness conditions are satisfied.
An accepted proposal without a wait enters the open list.

## Choose work by context

A scope groups a part of life, such as the studio. A context names a place,
tool or situation where a task can be done. A task can carry several contexts.
Use the person's own vocabulary.

```sh
daymark add "Phone calls" --kind context --id phone
daymark add "Confirm delivery window" --scope studio --context phone --id delivery
daymark ready --scope studio --context phone
daymark advise --scope studio --context phone --set time-fit.minutes=20
daymark agenda --context phone
```

The context filter selects tasks explicitly tagged with that ID before applying
the limit. Untagged tasks stay in the ordinary ready list. A blocked phone task
stays blocked even if its prerequisite belongs to another context. Use
`daymark find --kind context --page` to discover existing IDs and
`daymark ready --context phone --explain` to inspect the held work.


In a context-filtered advice request, picks, stale-work reviews and waiting
checks cover tasks carrying that context. The assistant profile can promote
matching tasks that move the next condition for a goal. For goal gaps and
unassessed conditions, use `daymark explore`. Add `--scope NAME` to keep condition and linked-record names in that scope, or `--unscoped` for records with no scope. Work elsewhere still affects the answer. The view counts its statuses and outstanding prerequisites without displaying those records’ IDs or titles. A gap alone supplies no evidence
about where its first task could happen.
