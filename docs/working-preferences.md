# Working with a person's preferences

A project coordinator opens the same workspace twice in one day. In the
morning they want to see the decisions holding up the team. Before a meeting
they want two things they can start. The agent can change its answer for
each situation.

The examples use the development checkout. Check `daymark advise --help`
for `--set` before trying them. On the tagged v0.5.0 CLI,
`daymark advise --profile gentle` previews the built-in short list.
To try the adjustable examples, install the checkout containing this guide.
[Install from source](../README.md#install-from-source)

| What the person says | What to try |
| --- | --- |
| “I can take the decisions now.” | `daymark advise --profile plain --set decisions-first` brings the first three ready items marked `answer` to the front. |
| “Just two things from the studio list today.” | `daymark advise --profile plain --scope studio --set shortlist.size=2` shows at most two ready studio tasks with a count of the rest. |
| “I have my phone and 20 minutes.” | After tagging phone tasks with an existing context ID, `daymark advise --profile plain --context phone --set time-fit.minutes=20` offers tagged tasks with a recorded estimate that fits. |
| “I have 20 minutes before the meeting. What could I finish?” | `daymark advise --profile plain --set time-fit.minutes=20` shows ready tasks individually estimated at 20 minutes or less. It counts longer tasks and tasks missing an estimate separately. |
| “I have an hour for the tasks planned today.” | `daymark advise --set overbooked.capacity_minutes=60` checks the day's estimated work against that hour. The note identifies the assumed duration for items without estimates. |
| “I like the short list. Please use it every time in this workspace.” | `daymark profile --set shortlist.size=2` saves that change on the current profile. |
| “Show the ordinary ready list this time.” | `daymark ready` gives the underlying order. `daymark profile` shows the saved configuration. |

Start with one concrete question when the feedback is ambiguous. “Fewer
things” might mean fewer picks, fewer follow-up notes, or only one project's
work. Show the result using today's tasks. When someone wants to keep the
adjustment, use the same base profile and flags on `daymark profile`. A
workspace profile is shared by callers of that database and workspace.
Use a one-call adjustment for a visitor's preference or a busy afternoon.

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
wants to retire it. [Strategy packages](strategy-plugins.md)

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
that no later active edit has intervened. Named saves and removals also accept
`--op`. Their receipts are local configuration and stay out of task exports.

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
