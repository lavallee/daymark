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
