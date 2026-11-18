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
