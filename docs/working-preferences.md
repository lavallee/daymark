# Working with a person's preferences

A project coordinator opens the same workspace twice in one day. In the
morning they want to see the decisions holding up the team. Before a meeting
they want two things they can start. The agent can change its answer for
each situation.

The examples use Daymark 0.7.0. Check `daymark advise --help`
for `--set` before trying them. On v0.5.0,
`daymark advise --profile gentle` previews the built-in short list.
[Set up Daymark 0.7.0](mcp.md#connect-two-agents-in-five-minutes)

| What the person says | What to try |
| --- | --- |
| “I can take the decisions now.” | `daymark advise --profile plain --set decisions-first` brings the first three ready items marked `answer` to the front. |
| “Just two things from the studio list today.” | `daymark advise --profile plain --scope studio --set shortlist.size=2` shows at most two ready studio tasks with a count of the rest. |
| “I have my phone and 20 minutes.” | After tagging phone tasks with an existing context ID, `daymark advise --profile plain --context phone --set time-fit.minutes=20` offers tagged tasks with a recorded estimate that fits. |
| “I have 20 minutes before the meeting. What could I finish?” | `daymark advise --profile plain --set time-fit.minutes=20` shows ready tasks individually estimated at 20 minutes or less. It counts longer tasks and tasks missing an estimate separately. |
| “I have one hour for ready work that is due or planned by today.” | `daymark advise --set overbooked.capacity_minutes=60 --set overbooked.include_focus=false` warns if that work exceeds the hour. It identifies any assumed duration. |
| “I like the short list. Please use it every time in this workspace.” | `daymark profile --set shortlist.size=2` saves that change on the current profile. |
| “Show the ordinary ready list this time.” | `daymark ready` gives the underlying order. `daymark profile` shows the saved configuration. |

“Show me two options through Friday” has a person and an end date. Apply
the limit to this person's current request. A host that identifies the
person and keeps their preferences privately can reapply it through Friday,
then stop. With only the shared workspace available, explain that a future
session needs the preference supplied again. A shared memory shows up for
other callers and its text cannot enforce an expiry.

Start with one concrete question when the feedback is ambiguous. “Fewer
things” might mean fewer picks, fewer follow-up notes, or only one project's
work. Show the result using today's tasks. When someone wants to keep the
adjustment, use the same base profile and flags on `daymark profile`. A
workspace profile is shared by callers of that database and workspace.
Use a one-call adjustment for a visitor's preference or a busy afternoon.

The usual capacity check also counts focused ready tasks. A warning states
how many minutes came from focus alone. Use `include_focus=false` if the
person uses focus as a priority list across several days. For all tasks
planned today, including ones blocked on someone else, inspect the agenda.
A capacity warning measures ready work and leaves each recorded date intact.

Before a deadline review, try `daymark advise --profile plain --set lookahead`.
For open tasks with a hard deadline in the next 14 local days, it points out
a missing plan or work planned after the cutoff. An open task with a future
start can appear in the notes before it becomes ready. A date-only plan can
fit anywhere on its recorded day. When the day overlaps the cutoff, the
strategy leaves that task out of the warning. For the deadlines of waiting
tasks as well, read `daymark agenda --deadlines --days 14`.

When reviewing a goal, `frontier-first` can also explain an empty ready list.
A next condition may have a proposed move awaiting a decision. The note
names the condition. Use `daymark explore GOAL --scope NAME` to see linked
work in the same scope and counts of work outside it. Use `daymark explore
GOAL` for a workspace-wide review. The proposal stays pending until someone authorized
to decide accepts or dismisses it.

A decision batch reports how many reviewed ready tasks need the person's
answer. The IDs it names are limited to visible choices and the configured
`decisions-first.limit`. For one question at a time, try `advise --profile
plain --set decisions-first.limit=1 --set shortlist.size=1`.

Advice is a list of options to discuss. A five-minute approval can qualify
for a short list while its `next_move` still says `answer`. An agent looking
for delegated work should use `daymark ready --move agent`. When a time window
omits a ready task with a hard deadline today or earlier, the note gives its
ID (up to five) so the person can decide whether to revisit it. A planned
day or a soft target alone does not trigger that note.

When advice holds back ready work, follow the plain-read command in the
output. Local CLI commands carry the same database, workspace and actor.
In MCP, use the suggested ready view on the same connection. JSON callers can
pass `ready_query` to `Tasks.ready` or add its fields to `daymark_view` with
`view="ready"`. The limit requests up to five additional rows beyond a wide
shortlist. The result uses the ordinary ready order and reflects the time
and workspace state when that read runs.

Advice reviews up to the first 1,000 ready tasks in the selected scope and
context. If more qualify, the coverage note gives both counts. In JSON,
`ready_coverage.reviewed` is the number inspected for picks and
`ready_coverage.available` is the full ready count for that selection.
Daymark includes this object only when the review is truncated. A time-fit
result with zero picks can mean that the reviewed candidates were too long
or lacked an estimate. In a larger queue, shorter tasks may sit farther
down. Selecting a relevant scope or context changes the candidates reviewed.
Advice computed on a client against a service reads its facts in separate
requests. If it reaches the limit, it reports the number inspected and leaves
the total unconfirmed. A v0.5.0 service also caps its ready count at 1,000.

A context records an explicit relationship between a task and a place, tool or
situation. Find context IDs with `daymark find --kind context --page`. A task
can have several. An untagged task might also suit the situation, but Daymark
has no recorded basis to include it. To review such possibilities with the
person, read the broader ready list. A context applies to this request and
persists only on tasks the person chooses to tag.

From MCP, call `daymark_view` with `{"view":"profiles"}` to inspect the active
configuration, named copies and available adjustments. Use
`{"view":"profiles","strategy":"time-fit","json":true}` to read the purpose,
default minutes and supported range. On the CLI use `daymark strategies time-fit`.
Add `"profile":"gentle"` to inspect the
strategies and parameters of that built-in profile. To preview two studio
picks, send this tool call:

```json
{"name":"daymark_view","arguments":{"view":"advise","profile":"plain","scope":"studio","set":["shortlist.size=2"]}}
```

When the person asks you to configure a shared preference, inspect the
profile, make the reviewed change, then confirm the rule and its audience.
They may want to know that the new limit is two without seeing any tasks
right now. In a separate request for two options, give those options, the
count held back and any hard-deadline warning. A person who asks which
tasks were omitted can see their names and the reason for each omission.

## Keep more than one way of working

The same workspace can have a quiet writing session and a weekly review. Give
an arrangement a name when the person wants to use it again. For example, a
coordinator asks to keep a short list of tasks that could fit between meetings.

To try this in an empty directory, create an example workspace and give
three studio tasks estimates. The fourth is waiting for an estimate.

```sh
daymark init --here
daymark add "Prepare the proof" --id proof --scope studio --minutes 15
daymark add "Check the figures" --id figures --scope studio --minutes 20
daymark add "Rewrite the appendix" --id rewrite --scope studio --minutes 45
daymark add "Price the print run" --id price --scope studio
```

Now save and try the profile:

```sh
daymark profile plain --set time-fit.minutes=20 --set shortlist.size=2 --save-as between-meetings
daymark advise --profile between-meetings --scope studio
daymark profile --saved between-meetings
```

The picks are `proof` and `figures`. The note counts one longer task and one
without an estimate. These are two alternatives for the same 20-minute gap.

`--save-as` stores the strategies, parameters and order. The scope belongs to
this invocation, so the same profile can be used at home. `daymark advise`
continues to use the workspace's active choice. To make the saved configuration
the workspace default, run `daymark profile between-meetings`. That copies the
configuration. Later edits to either copy are independent.

When you select a named copy, the advice identifies the saved version it used.
In JSON, `profile_source` identifies the base, whether it comes from the
workspace, and whether this call adjusted it. This distinguishes an active
copy from a newer saved version that happens to share its name.

The stages run in the order shown by `daymark profile`. For “two things I
can finish before the meeting,” start from `plain`, apply `time-fit` first,
then `shortlist`. If the shortlist runs first, two long tasks can take both
places before the time check sees a shorter task. Changing the size of a
stage already in a profile leaves it in place. Adding a stage puts it last.
To try a different order, use `--drop shortlist --set time-fit.minutes=20
--set shortlist.size=2`. Removals run before additions.

To replace a saved profile, inspect its version first. If `--saved` reports
version 1, the update below asks for three choices. A stale version produces a
conflict so an agent can read the person's latest changes before deciding how
to proceed.

```sh
daymark profile between-meetings --set shortlist.size=3 --save-as between-meetings --if-version 1
daymark profile --saved between-meetings --json > between-meetings.json
daymark profile --from-file between-meetings.json --save-as short-break
```

`daymark profile --saved` lists names and versions. `daymark profile --remove
NAME --if-version N` removes a named copy. The active profile and saved copies
are shared configuration in the local workspace, accessible to callers who can
read that database. An agent can use a one-call adjustment for a personal
preference in a shared workspace. Personal workspaces can keep their own saved
profiles. Entity exports omit settings, so use `--saved NAME --json` when
moving a profile between installations. A full database backup includes settings
and their replay and undo receipts. A portable profile JSON file records a
configuration and imports as a new named copy in the destination workspace.
When an extension is missing locally, the named export still contains its
settings and stage order. Other stages remain usable and editable.
If an installed extension no longer accepts a saved parameter, advice names
it and the active profile shows its original value. Other preference edits
preserve that value. Use `--drop STRATEGY.OLD_PARAMETER` only when the person
wants to retire it. The tagged v0.5.0 CLI can discard an unfamiliar option
when editing a newer active profile. Use a current editor for those changes.
A retained named copy or its profile JSON can identify the missing value.
[Strategy packages](strategy-plugins.md) · [Mixed-version recovery](operations.md#setting-revisions-on-schema-6)

## Two people who share the same projects

If someone says “only show two decisions for me,” their mediation software
can keep that instruction in its own user-specific settings with their consent.
Send the preference on each advice request for that person.

```json
{"view":"advise","profile":"plain","set":["decisions-first","shortlist.size=2"]}
```

Another person can send `{"view":"advise","profile":"plain","set":["time-fit.minutes=10","shortlist.size=1"]}`
to the same workspace and receive a different list. If either person wants
the ordinary order once, send `{"view":"advise","profile":"plain"}` that time.
The next request can resume the host's selected recipe. An actor name identifies
a Daymark caller for work claims and attribution. Two people can use a host
with the same actor name.

For a recipe the team can see and reuse, create a named copy. Each host can
then send `{"view":"advise","profile":"decision-review"}`. The workspace
library is shared. A later edit to that name changes the next explicit
selection. Its `profile_source.version` tells the host which revision ran.
If a person's instruction applied to one particular revision, compare that
version before presenting the result or send the agreed settings inline.

Use `daymark_act` with `action="use_profile"` only for a requested change
to the workspace default. That action copies the selected configuration, so
a subsequent edit to the named copy leaves the active version intact.
`remember` also writes to shared workspace state. A person's private preference
belongs in a host setting with the intended audience.

### When someone changes their mind

Two shift notes saying “one decision today” explain those check-ins. They
give a returning assistant no standing instruction, even when both notes
carry the same Daymark actor. For “show me everything this morning,” use
the full ready list once and resume the signed-in person's saved view on
their next visit.

For “give me two choices from now on, just for me,” check which personal
setting the host can read and whether it can update that setting. Confirm a
save from the host's receipt or a fresh read. With read access alone, preview
the new choice for this conversation and tell the person which preference
will still be in effect next time. If the new request covers work of any
kind, use `shortlist.size=2` on the ordinary queue. Adding a decisions filter
would carry part of the previous instruction forward.

For a two-choice check-in, name two tasks and give the count still ready.
If the person also asks about a hard deadline on omitted work, check the
deadline agenda in the same scope and name the verified exception. Give
a clock time only when the record includes one.

An agreed team default applies to calls that use the workspace profile. A
colleague with their own full-list preference can keep seeing every ready
task. A shared Daymark actor by itself tells you nothing about which host
account the colleague will use.

### Let a personal choice expire

A host can translate “two choices through Friday” into ordinary advice
arguments. It looks up a private setting for the person who signed in. The
end date belongs to the timezone agreed with that person. On the next local
day, the workspace's current advice takes effect again.

```python
from datetime import date
from zoneinfo import ZoneInfo

def advice_for(tasks, window):
    settings = []
    if window:
        today = tasks.now().astimezone(ZoneInfo(window["timezone"])).date()
        if today <= date.fromisoformat(window["through"]):
            settings.append(f"shortlist.size={window['choices']}")
    return tasks.advise(settings=settings)
```

In a synthetic run, the proofreader chose two through November 1, 2030 in
New York. The host supplied that setting in one process at 23:59:59 and a
fresh process at midnight. The proofreader saw two tasks, then five. A second
person using the same Daymark actor saw five both times. The workspace kept
its original five events and revision-zero default throughout. A host with
no private preference store can still narrow the current answer.

## Use the same preference through a service

If the team sets a two-item workspace default on its database, a current
authenticated service returns those two picks too. `advise --profile NAME`
uses a saved copy and `advise --profile plain` previews the ordinary queue.
Inspect the active version with `profile --json`. Profile changes are made
on the local database.

The service runs built-in stages. A saved extension stays in its recorded
position for local installations. Service advice names any skipped stages.
To use a strategy package installed on the client, choose an explicit
built-in base, for example `advise --profile plain --set estimate-review`.
The client runs only that requested preview on the service's task reads.
A v0.5.0 service cannot report its workspace profiles. A current client
identifies its plain fallback when using that older service.

## Review projects that have gone quiet

When someone asks “Which studio projects have no next action written down?”,
inspect the projects in that scope. This example shows one ready pick followed
by the review prompts.

```sh
daymark advise --profile plain --scope studio --set project-review --set shortlist.size=1
```

The review names open projects that have no open or waiting task anywhere
in the project. A proposal awaiting a decision appears as such. An action
that starts next week, depends on someone else, or is claimed by another
worker already supplies a next action. `daymark plan PROJECT` shows the
details. The scoped review uses the project's own scope. Use
`daymark edit PROJECT --scope studio` if the person wants to assign one.
For the underlying counts, use `daymark projects --scope studio`. A
project's blocked, waiting and proposed members count even when their tasks
belong to another scope. The scoped response omits the names of a lead or
goal outside that scope. An older service that cannot apply this portfolio
filter reports that it needs upgrading.

To choose the next action within a project, use `daymark ready --project
PROJECT`. It can return a task assigned to that project even if the task
belongs to another scope. Add `--scope studio` when the person asks for
the intersection. `--explain` gives the eligible count and the reasons other
members are held. A high-priority task in a different project cannot take a
place on this list.

An empty project can mean the next action needs capturing or the outcome is
already complete. Ask which applies. `daymark add "Check the paper samples"
--project PROJECT` records a requested action. Use `propose` for a suggested
one. A later review counts an accepted or waiting task as recorded work.
A task context such as `phone` describes where someone can act. Omit
`--context` for a whole-project review.

To keep this routine available for another day, save a named copy and request
it when needed:

```sh
daymark profile plain --set project-review --set shortlist.size=1 --save-as weekly
daymark advise --profile weekly --scope studio
```

The routine covers one part of a GTD weekly review. The publisher also suggests
checking calendars, waiting items and other lists. The cadence and scope are
the person's choices. [GTD Weekly Review checklist](https://gettingthingsdone.com/wp-content/uploads/2014/10/Weekly_Review_Checklist.pdf)

For a review of outstanding replies, add `--set waiting-check.days=5`.
It asks about waiting tasks after five days without a task update. A dated
`wait --until` postpones that follow-up until the recorded day or time.
An earlier hard deadline still appears in the agenda. A note or an
edit can reset the task's update time, so check the history before
describing when someone last contacted the other party.

## Retry or reverse a standing choice

Read `daymark profile --json` for this workspace's active version. A fresh
workspace begins at version 0. If someone asks to set a standing preference
for two picks, an agent can bind the change to the version it saw:

```sh
daymark profile --set shortlist.size=2 --if-version 0 --op choose-two
```

After a lost response, repeat the same command and operation ID. It returns
the original result, even if the workspace has moved on. Read `daymark profile`
to check its current configuration. A successful change prints the exact
command that restores the previous settings in the same database. Undo checks
that no later active edit has intervened. In a local MCP session, submit the
receipt’s `restore_call` as a tool call. It contains the expected version and
a stable retry ID. The restore action targets only the profile. Named saves and removals also accept
`--op`. Their receipts are local configuration and stay out of task exports.

To recover an earlier named recipe, inspect the active copy with `daymark
profile --json`. If it still has the settings the person wants, read the
destination with `daymark profile --saved NAME`, then copy the active settings
back using `daymark profile --save-as NAME --if-version N`, where N is the
destination's current version. Another recovery path is a retained save
result. Repeating its original save command with the same operation ID and
`--json` returns the recipe as it was saved, even after a removal. Write
that result to a file and use `daymark profile --from-file FILE --save-as
NAME --if-version N`. Use version 0 to recreate an absent name.

## Where the ideas come from

Todoist's filter guide describes saving a task query for repeated use.
Things' guide shows how to narrow a list by tag and clear the selection.
Together they illustrate a useful distinction between the view a person
wants now and a view they expect to reuse. [Todoist filters](https://www.todoist.com/help/todoist/features/introduction-to-filters-V98wIH) · [Things tags](https://culturedcode.com/things/support/articles/2803581/)

Getting Things Done separates clarifying a next action, reviewing commitments
and choosing what to engage with. An agent can use those as questions in a
conversation. Someone who wants a short list can borrow that idea without
adopting a review ritual. These are documented practices and product examples.
Which adjustment helps a particular person is a question for that person.
[Getting Things Done fundamentals](https://gettingthingsdone.com/what-is-gtd/)

GTD's account of choosing an action considers time available alongside
context, energy and priorities. Daymark's `time-fit` borrows that single
question. Each option can fit the window according to its recorded estimate.
An agent can ask which option the person wants, or help estimate a task left
out of the list. [Choosing what to do](https://gettingthingsdone.com/2023/01/choosing-what-to-do/)
