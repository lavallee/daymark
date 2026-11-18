# What an agent learns when it opens Daymark

A returning worker should be able to answer three questions immediately. Which
workspace is this? Which tasks can I act on? What changed since I left? A
person deciding what to do may have a different question, such as “what fits
in the next twenty minutes?” Both need the same underlying commitments.

## First session and return

The Beads README leads a reader through creation, dependencies, ready work,
claim and close. It also describes arranging for the agent to discover that
workflow when it starts. We used that sequence as a reading test for Daymark.
Our example gives the person and agent different next moves, holds one approval
behind a draft, and keeps a suggestion pending. We ran the README commands
against a fresh install of the public v0.5.0 tag. The agent saw the draft. After
completion, the person saw the deposit and the approval. The Beads comparison
here comes from its documentation. [Beads README at the revision inspected](https://github.com/gastownhall/beads/blob/a6b7cd76943765a05c5f26074b10e6a3f7b7db14/README.md)

In a one-task workspace on Daymark v0.5.0, a worker who claimed the task and
ran `prime` saw “0 ready” on line 49 and their claimed task on line 51.
The count excluded all claims while the list included the reader's own.
Using the same database, the working version showed one ready task on line 3
and its title on line 5. A returning session can request just this live part
with `prime --brief`. These are CLI observations from a synthetic workspace.

## A small command-selection trial

We gave a local Qwen2.5 7B model a fresh `prime` transcript and one synthetic
request. It returned an array of CLI arguments per command. A runner executed
each allowed command in an isolated database and checked the stored fields
and printed result. The model got one batch of commands, temperature zero
and no feedback from the CLI. We accepted argument arrays with or without
the leading executable name. An initial runner rejected that prefix and
misclassified every response; the table scores the original responses after
correcting the runner.

| Request and observed success condition | v0.5.0 guide | Live state first | Live state plus field-selection table |
| --- | --- | --- | --- |
| Collect badges on an intended day and submit a permit by a hard cutoff. Store each date in its own field and leave priority unset. | Miss | Miss | Miss |
| Keep “Design a poster” as a suggestion. Store one proposed task. | Pass | Pass | Pass |
| Park the existing venue task pending the venue's reply. Keep it waiting on the venue with no invented date. | Pass | Miss | Miss |
| Show at most two ready studio tasks for this answer. Return two of four, omit the household item, preserve the profile. | Miss | Miss | Pass |

One trial for each cell is enough to find a failure to reproduce. It cannot
estimate a success rate for agents or people. The model made a real
semantic error on the two-date request even when the commands were valid.
In another variant, putting literal example commands in the startup
instructions caused it to create three example tasks. We removed those
examples from startup and kept the executable walkthrough in the README.
In the field-table trial it used the literal placeholder `ID` instead of the
venue task's ID. These cases motivate testing with an interactive reader who
can inspect the task, verify a result, and recover from an error.

The trial did not measure human preference, productivity, or sustained use.
A user choosing a different view for one afternoon is a design case, and a
person explicitly choosing to save it is a separate observable action.
[Working with a person's preferences](../working-preferences.md)

## Reading the examples from an installed wheel

On November 18, 2026 (UTC), a clean virtual environment installed a wheel
built from the development checkout. A scripted reader executed the eight
commands in the README's empty-folder invitation example. The agent saw
`draft`. After completion the person saw `deposit` and `approve`. All eleven
commands in the two project examples ran in separate empty workspaces and
matched their printed transcripts line for line.

The first preference walkthrough assumed a populated `studio` scope.
Following it immediately after the README supplied no task with that scope
and a duration. The revised walkthrough creates four examples. Two fit the
20-minute window, one is longer and one lacks an estimate. A fresh run
executed the guide's creation, preview, replacement, export and import
commands. The imported copy preserved the strategy order and parameters.

## A fresh MCP client reads a person's preferences

A human-side CLI session prepared those four synthetic tasks and saved a
2-pick profile. Two separate processes using the official Python MCP SDKs
1.30.0 and 2.3.0 each opened the installed wheel's stdio server. Both
negotiated 2025-11-25. Each inspected the saved profile and requested one
studio pick for one call. Each saw one task, then all four on an ordinary
advice request. Inspection still showed the original two-pick profile at
version 1. The first run revealed that a caller could infer the effect of the
default without identifying its name. The final client could read the active
profile, the built-ins and the saved names in one call.

We also ran each SDK against a stdio server backed by authenticated HTTP.
The local database had an active named profile. The HTTP client correctly
reported its own plain default, identified the local-only setting boundary,
and could preview a built-in with a temporary time limit. The local profile
still had its original selection afterward. These checks exercise the client
protocol and published examples. They supply no measure of human preference
or of how often an autonomous agent chooses the right command.

## Knowing which database an agent will touch

A Beads user reported that memory writes issued outside the project directory
landed in their global database. A second reporter traced a symlinked-store
problem to selection of an ancestor database. Their retest distinguished a
release where the problem persisted from a main-branch revision where it was
fixed. We used both reports to design local tests. They describe particular
Beads versions and configurations. [Global fallback report](https://github.com/gastownhall/beads/issues/4402) · [Symlink report and retest](https://github.com/gastownhall/beads/issues/4627)

Daymark also searches parent directories and supports a per-user database.
We checked an empty folder, an explicit missing file, a per-user file,
a legacy file, a symlinked project directory, an environment override, an
explicit flag and a credential-bearing HTTP URL. The read-only `where`
command uses the same file selection as the everyday CLI. The tests used
non-database sentinel files so an accidental SQLite open would fail.
They checked both the selected location and the bytes after the read.
A host launching an agent from a changing working directory can pin an
absolute path in its configuration and inspect the result before pickup.

## Changing a way of working across sessions

A second installed-wheel exercise created nine synthetic records in an
isolated database. Six were ready. The others were a waiting task, a proposal
and a task blocked on the proof. Each step below launched a new CLI process.
Different actor names shared the workspace, as cooperating agents would.

| Occasion | What the caller did | Observed result |
| --- | --- | --- |
| Before a meeting | Asked for two ready studio tasks estimated at 20 minutes or less | The format decision and the proof appeared. Three other ready studio tasks were held back. A fresh session still used `plain` and saw all six ready tasks across both scopes. |
| A recurring review | Saved a `review` profile that leads with decisions and offers two choices | The saved version was 1. Another actor could use it explicitly. It left the default at `plain`. |
| A standing preference | Adopted `review`, then replaced its named copy with three choices at version 2 | Ordinary advice still offered two. Explicitly selecting the saved version offered three. Both had the name `review`. |
| A competing edit | Tried to replace the named profile with the superseded version 1 | The write returned `conflict`. The saved copy remained at version 2. A different workspace saw no saved copies. |
| Returning to the ordinary view | Removed the saved copy, checked the active two-pick copy, then chose `plain` | All six original IDs appeared in their original ready order. The complete public entity/event export compared equal before and after the exercise. |

The identical name in the third step made two correct answers hard to
explain. In a fresh run, `profile_source` identified the two-pick answer as
the active workspace copy and the three-pick answer as saved version 2. It
also identified the first session's change as a temporary adjustment to the
built-in `plain` profile. Settings live outside entity exports, so the exercise
checked settings separately at each transition. The script supplied explicit
user decisions. It establishes what the interfaces preserved across sessions.
Whether an agent asks at the right moment needs a separate evaluation.

## Previewing and undoing a standing choice over MCP

Two fresh SQLite workspaces each held two phone-tagged tasks estimated at
10 and 15 minutes and one untagged task estimated at 2 minutes. Independent
MCP Python SDK clients (1.30.0 and 2.3.0) started the installed wheel through
stdio and negotiated protocol 2025-11-25. Each client completed these steps.

1. Read active version 0. Preview a 10-minute limit in the phone context.
   Only the 10-minute phone task appeared. Reading the catalog again returned
   the same settings and version.
2. Apply an explicitly requested one-pick standing preference with operation
   ID and expected version 0. Repeating the request returned the original
   workspace, version 1 and recovery instructions. One of the two ready phone
   tasks appeared with a count of one held back.
3. Run the returned CLI command against the same database. The next MCP read
   returned both phone tasks and active version 2. A late replay of the
   original adoption returned the version-1 receipt and left version 2 active.

A repeat of both SDK trials started with tool discovery. The manifest named a
profiles view but carried no time-fit parameter definitions. The client found
`time-fit.minutes` in that view, opened its strategy detail, confirmed that
10 was inside the supported 1–1,440 range, and applied the preview. The
structured catalog payload measured 453 UTF-8 bytes in the test workspace and
the single-strategy detail measured 241 bytes (compact JSON, excluding protocol
wrappers). Saved entries in the catalog carry only their names and revisions.
A separate 32-profile fixture measured 1,523 bytes, against 12,044 bytes when
the earlier catalog embedded every saved configuration.

These exercises test interoperability of the installed interfaces under
scripted choices. The fixture supplies the person's decisions and has no
measured human response.

## Running the same dependency handoff in Beads

We ran the official Linux amd64 Beads v1.3.4 executable after checking the
archive against its published SHA-256. The trial used an empty local Git
repository, noninteractive initialization with agent setup disabled, embedded
Dolt and a synthetic two-task dependency. The test environment had no network
access. This exercises a single repository on a single machine.
[Beads v1.3.4 release](https://github.com/gastownhall/beads/releases/tag/v1.3.4)

| Action in Beads | Observed output |
| --- | --- |
| Create “Draft invitation” and “Approve invitation”. Make approval depend on the draft. | `bd ready --json` returned the draft alone. |
| Read `bd ready --explain --json`. | The result contained both the ready draft and the held approval. The latter included the draft's ID, title and status as evidence. |
| Claim the draft as one worker and read the ready queue as a second worker. | The second worker saw an empty list. |
| Close the draft with a reason and read again. | The approval became ready. |

The useful discovery was that someone who knows no task IDs can still find
out why work is held. In Daymark, dependencies are one of several reasons a
task may be absent. `daymark ready --explain` therefore gives the number ready
and the number held for the same scope, actor and move filter, plus example IDs
for each reason. One task can have several gates. A person can inspect a sample
with `show` and see what must change. The command leaves the workspace alone.

Two MCP hosts also tried a synthetic workshop handoff. The trial records the
events each agent actually wrote, a fresh session’s readback, a host-level
write refusal and measured queue payload sizes. [MCP client trials](mcp-client-trials.md)

## Selecting the right date in a real edit

We gave a local Qwen2.5 7B model a task with a May 3 work day and a May 10
deadline, then asked it to change one date. Each condition got the same five
requests and a fresh SQLite database per request. The runner executed the
returned CLI arguments and compared the entire compact task with the expected
task. Each cell below is one observation at temperature zero.

| User request | Full startup guide at `5eaeb05` | Short date reference | Reference with a prototype `replan` command |
| --- | --- | --- | --- |
| Move the work day to May 6 | Pass | Pass | Invalid flag |
| Record a filing extension until May 15 | Pass | Pass | Pass |
| Material first available May 2 | Pass | Pass | Pass |
| Work on it May 4 instead, keep the due date | Pass | Pass | Changed the earliest start |
| Hope to finish May 9, retain the May 10 deadline | Replaced the deadline | Pass | Pass |

The short reference named four meanings and the four corresponding flags for
`edit`. The prototype replaced its planned-day line with a proposed command
that the runner translated into an ordinary edit. We kept the existing verb
and added the missing soft target to the startup reference. This comparison
cannot measure general model performance.

In a separate six-call interactive trial, the same model fulfilled the
waiting request on both v0.5.0 and the development checkout. Neither run met
the complete rubric for changing a plan, answering a dependency question or
returning a temporary scoped selection. Some turns returned an empty command
instead of a final answer. One checkout session read the correct prerequisite,
then placed it on hold despite the person's explicit request to leave the work
alone. The session transcript distinguishes a tool returning the right fact
from an agent using it correctly.

## Repeating the journeys with your own client

From a development checkout, create a new disposable directory for each run.
The driver prints one request and the command selecting its database,
workspace, actor and timezone. Give those to a person or agent using the
client you want to evaluate. Keep the scorer and rubric out of their prompt.

```sh
uv run python scripts/agent_journey.py start move-plan /tmp/example-plan-trial
# After the reader finishes, score the stored facts.
uv run python scripts/agent_journey.py check /tmp/example-plan-trial
```

The available scenarios cover a plan with a hard deadline, two kinds of
dates, waiting for someone, a suggestion, a one-time scoped choice, and a
blocked task. Run `start --help` for their names. For the last two, supply
the ID the reader actually reported with `check DIRECTORY --answer ID`.
Checking opens SQLite read-only and exits 0 for a passing outcome or 1 for
a miss. Record the client and version, the answer shown to the person, and
any interventions alongside the score. A state-based pass alone cannot tell
whether the reader understood the request or found the experience easy.

## Returning for a project review

The GTD weekly checklist asks people to inspect project outcomes and look for a
current action. Waiting items and calendars have separate review steps. We took
the project question alone and ran it against eleven synthetic projects in a
fresh local wheel. [GTD Weekly Review checklist](https://gettingthingsdone.com/wp-content/uploads/2014/10/Weekly_Review_Checklist.pdf)

Nine open projects belonged to the studio scope, one open project belonged to
the home scope and one studio project was completed. The studio projects
included an empty project, one containing only completed work and one containing
only a proposal. The other six already contained work: a future start, a wait
on the supplier, an external dependency, another worker's claim, an action in
the home scope and an ordinary ready task. The home action also carried the
phone context.

The original project list displayed zero of zero tasks done for both the empty
studio project and the completed project. It displayed zero of one done for a
waiting project, another worker's project and the ordinary ready project.
`projects --json` exposed each project's status, stage counts and proposal
count. Selecting projects by their own scope required the scoped project
search as well. Reading every plan individually also distinguished the cases
but required eleven plan reads. The built-in assistant gave no project review
prompts. The studio-and-phone ready list was empty.

One call to `advise --profile plain --scope studio --set project-review --set
shortlist.size=1` named the three studio projects with no open or waiting
action. It identified the pending proposal. The caller's context-filtered
request named no projects and explained how to run a whole-project review.
A new agent process used an explicitly saved `weekly` copy and got the same
three names. The active setting stayed at `plain`, and the workspace's public
entity and event export compared equal across those reads and configuration
changes.

In a subsequent session the fixture supplied three explicit decisions. The
person added a next action to the empty project, accepted the proposed task,
and marked the project containing only completed work as done. A later use of
the saved routine found an open or waiting action in all eight remaining
studio projects. The study demonstrates how recorded state changes the answer.
It cannot tell whether a person will choose to review every week or whether
an existing task is the right next action.

## Revising a preference across sessions

A scripted run on November 18 used 29 fresh CLI processes against one temporary
database. One worker saved 12 scoped facts in the proofs workspace and a
different convention in another workspace. A returning worker saw five facts
with scope labels during startup and recovered all seven others by following
the ID cursor. It found a fact outside that first page by text and scope,
inspected it, and replaced its wording at the reviewed revision. An attempted
retirement using the earlier review token returned a conflict. Supplying a
task ID returned an invalid-input error and left that task active. Retiring
the reviewed fact and retrying with the same operation produced one archive
at version three. Eleven active facts remained in the workspace and the
separate convention remained in the other workspace.

Before these changes, the dedicated forget verb could archive an unrelated
task. The text reply and brief for an archived memory also omitted its archive
state, and startup omitted recorded scope labels. The scripted trial exercises
recorded state and machine-visible output. It does not measure whether a
person would choose a given convention or whether a model can infer it.
[Memory lifecycle regression](../../tests/test_memory_journey.py)


## What a short time window leaves out

A fixed-clock read used ready tasks with a past hard deadline and no estimate,
a 50-minute report due today, a five-minute decision due today, and an
eight-minute task planned today. Four other tasks were held by a future start,
a wait, a proposal, or an unmet dependency. All eight carried the selected
scope and phone context.

| Available minutes | First day's ready picks |
| --- | --- |
| 3 | None |
| 5 | Five-minute decision |
| 10 | Decision and planned task |
| 60 | Report, decision and planned task |

On the next day the one-minute task whose start had arrived appeared in every
window. The waiting, proposed and blocked tasks appeared in none. The
unestimated task remained outside even the 60-minute window. The earlier
advice only mentioned a missing estimate. In a repeat from a newly installed
wheel the note identified the omitted task with a hard deadline. An upcoming
or held task did not lose its start, dependency or status. The public export
compared equal across eight previews.

A second case used 32 ready tasks in one scope of a non-default workspace.
After a 25-pick shortlist, following the earlier plain-read suggestion
selected the default workspace and found nothing. With the workspace supplied
manually, the ordinary first page returned 20 tasks, all of them already shown.
The revised local command carried the database, workspace, actor, time zone
and scope, and asked for 30 rows. Running it returned five additional IDs.
The MCP suggestion carries ready arguments for its existing connection.
This is a fresh bounded read, so intervening task changes can change the order.

## Six requests in installed agent hosts

We ran each of the six portable journeys above in two fresh MCP host sessions
using the same development tree. The host received only the person's request
and access to its own synthetic Daymark workspace. The two lookup cases used
the server's read-only mode. We inspected the final text and scored the
database after each session.

| Request | Claude Code, Daymark tools enabled | Codex CLI 0.166.0, approval unavailable |
| --- | --- | --- |
| Move the plan, preserve the deadline | Passed | Proposed the correct guarded edit. The host declined the call. |
| Distinguish a planned date and a cutoff | Passed | Selected both fields correctly. The host declined both writes. |
| Wait for the print shop | Passed | The host declined the wait. |
| Save a suggestion | Passed | The host declined the capture. |
| Return one studio option | Passed | Passed |
| Name the blocking prerequisite | Passed | Passed |

The four unsuccessful Codex writes never reached Daymark. Its final answers
acknowledged that the requested state had not been recorded. Both clients
supplied the right ID in the two read-only cases and left the workspace
unchanged. Host authorization is part of the user's experience. We record
those outcomes separately from incorrect field choices and from the people
who may later use the tool.

## A reminder with an actual time and recipient

A fresh session was asked to remind an existing reviewer about a proof at
09:00 London time on May 3, 2030, while keeping the original plan and
deadline. The first Claude Code session could find those tasks and dates.
Its MCP tools offered no scheduling action. It left the records alone and
suggested creating another task.

After exposing the existing scheduling verb, a second fresh session scheduled
one reminder for 08:00 UTC, then checked the task and reminder. The task
stayed at its original version. The answer distinguished a pending local
recording from sending a notification. Its first call supplied review guards
for a task edit. Daymark refused those extra fields, and the client recovered
on the next call. Each trial used one session and a synthetic recipient.


## Returning after an extension changes

An isolated environment installed a synthetic third-party wheel through the
public `daymark.strategies` entry point. Its module wrote an import marker so
the trial could distinguish metadata discovery from Python execution.
Installing and listing the package left that marker absent. A one-call preview
then imported the strategy and returned the expected sample note. A saved
profile used one sample on the next run. The public task and event export
compared equal throughout these package and preference changes.

Removing the package produced a skipped-strategy note and preserved the
stored configuration. An installed replacement with a missing Python
dependency had previously produced a generic CLI failure. After the change,
the rest of the advice remained available and named the unavailable strategy.
A fresh process could list the installed entry-point name before selecting it.

A replacement version then renamed `limit` to `samples` and changed its
default. The saved revision still held `limit=1`, but the earlier active read
showed only `samples=3`. Two available sample IDs appeared with no warning.
The updated active read identifies the saved, unavailable parameter separately
from the current effective value. A regression exercises an unrelated edit,
a named export, explicit migration and version-guarded undo. It verifies the
original value survives until the person explicitly removes it.

The contributor example's Python and TOML blocks were extracted into a wheel.
Its strategy returned the correct unestimated task against both the public
v0.5.0 tag and the development checkout in separate environments.
[Build a strategy package](../strategy-plugins.md)


## Resuming after restore or a competing writer

A synthetic worker saved its compact startup checkpoint, another worker
added a note and changed the title, and we resumed from a SQLite backup and
a separate native JSON installation. The native destination already had
events in another workspace, so its internal SQLite row numbers differed.
Both returned the note and title edit in order with a one-event page limit.

Switching to a workspace with the same numeric head, removing the saved
boundary event, and replacing its recorded title under the same operation
ID each produced a conflict on checked continuation. A native merge that
only added an entity left the event boundary intact. So did an event
backfill earlier than the checkpoint. Reading a fresh prime exposed the
newly imported fact. Tests cover both UTF-16 byte orders on caller-provided
databases and native restore to a UTF-8 installation.

A second file-backed trial held a caller-owned read transaction open. After
another connection edited a task, the first worker still saw its original
snapshot. Its attempted note returned a conflict. The application's own
pending work stayed intact. After the application ended the old transaction,
the worker could submit the note against the revised title. Another
connection saw the note only after the application's commit.


## Two personal views of the same workspace

Two scripted hosts used one four-task workspace and the same Daymark actor.
One host always sent a ten-minute window and a one-pick limit. The other
selected a named decision-first, two-pick review. Five fresh stdio processes
per SDK repeated the trial with Python MCP clients 1.30.0 and 2.3.0.

| Observation | What each host received |
| --- | --- |
| First selections | One eight-minute task for the first host. A decision and the eight-minute task for the second. Each selection took one advice call. |
| Temporary exception | The second host requested plain order and saw all four tasks. Starting new clients and resending their original selections restored one and two picks. |
| Shared change | An explicitly requested local `use_profile` adoption changed the default for both hosts. A caller resending its inline preference continued to see one pick. |
| Later library edit | The named review changed from two to three picks at saved revision 2. A fresh process under a different actor saw the two-pick active copy. Selecting the name explicitly produced three and reported revision 2. |

Both hosts could inspect the same named recipe. The public entity and event
export compared equal at the beginning and end. The trial supplied the host's
personal choices directly. Daymark inferred no relationship between the actor,
session and individual.
