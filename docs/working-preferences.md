# Working with a person's preferences

A project coordinator opens the same workspace twice in one day. In the
morning they want to see the decisions holding up the team. Before a meeting
they want two things they can start. The agent can change its answer for
each situation.

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
for delegated work should use `daymark ready --move agent`.

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
moving a profile between installations.

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
