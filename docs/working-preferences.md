# Working with a person's preferences

A project coordinator opens the same workspace twice in one day. In the
morning they want to see all the decisions holding up the team. Before a
meeting they want just two things they can start. Both requests make sense.
The agent can change the next answer without making either request a standing
instruction.

| What the person says | What to try |
| --- | --- |
| “I can take the decisions now.” | `daymark advise --profile plain --set decisions-first` brings the first three ready items marked `answer` to the front. |
| “Just two things from the studio list today.” | `daymark advise --profile plain --scope studio --set shortlist.size=2` shows at most two ready studio tasks with a count of the rest. |
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

An agent using MCP can preview two studio picks with this tool call:

```json
{"name":"daymark_view","arguments":{"view":"advise","profile":"plain","scope":"studio","set":["shortlist.size=2"]}}
```

### Where the ideas come from

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

### Keep more than one way of working

The same workspace can have a quiet writing session and a weekly review. Give
an arrangement a name when the person wants to use it again. For example, a
coordinator asks to keep a short list of tasks that could fit between meetings:

```sh
daymark profile plain --set time-fit.minutes=20 --set shortlist.size=2 --save-as between-meetings
daymark advise --profile between-meetings --scope studio
daymark profile --saved between-meetings
```

`--save-as` stores the strategies, parameters and order. The scope belongs to
this invocation, so the same profile can be used at home. `daymark advise`
continues to use the workspace's active choice. To make the saved configuration
the workspace default, run `daymark profile between-meetings`. That copies the
configuration. Later edits to either copy are independent.

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
