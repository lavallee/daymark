# Capture without committing

“Keep this” can mean a fact to find later, an idea worth considering, or work someone has agreed to do. Use proposals when a decision is wanted, memory for durable reference that belongs in session context, and open tasks for agreed work. An undated someday item with no requested review is the gap. The tested paths either ask for a decision, surface the item as remembered context, hide it from ordinary search, or require an invented date. Say so at capture time. If the person specifically wants Daymark to retain the item, offer a proposal and explain that it will appear under decisions. If an agreed reference destination is available, use it for material that should simply be retrievable.

## What the sources add

Daymark’s [working-methods review](working-methods.md) already maps capture, clarification, someday and waiting, and [working preferences](../working-preferences.md) covers choosing among existing work. The earlier review already shows that a proposal stays outside ready until accepted. This study adds revision, discovery in other views, and the decisions each capture leaves behind.

- Getting Things Done separates collecting something from deciding whether it is actionable. Reference and incubation are distinct outcomes. Delegating and tracking the handoff occur on the actionable branch. [GTD fundamentals](https://gettingthingsdone.com/what-is-gtd/) · [David Allen Company workflow map](https://gettingthingsdone.com/wp-content/uploads/2024/05/GTD_workflow_map.pdf)
- Things lets a person capture incomplete details in Inbox and find undated Someday items later without putting them in Anytime or Upcoming. Todoist sends new tasks without a selected project to Inbox and includes ideas among the things to collect there. Its GTD guide illustrates separate Reference and Someday/Maybe projects and a waiting label. Those are documented workflows, including conventions people can build from ordinary lists, rather than evidence of how users behave. [Things In-Depth Look](https://culturedcode.com/things/support/articles/4001304/) · [Things Scheduling](https://culturedcode.com/things/support/articles/2803579/) · [Todoist Inbox](https://www.todoist.com/help/todoist/get-started/use-the-inbox-in-todoist-HwHvYErS) · [Todoist’s GTD guide](https://www.todoist.com/productivity-methods/getting-things-done)

## Five synthetic captures

A scripted walkthrough exercised the CLI at development commit `5eaeb05` on November 18, 2026 UTC. The review compared exported records to each request, checked what the public views surfaced, and counted decisions the workflow presented. The counts are properties of this fixture, with no estimate of how much effort people experience.

| Utterance | CLI write | Persisted result and discovery | Human decision burden |
| --- | --- | --- | --- |
| “Keep the seed-swap idea as a maybe. Actually, make that a cutting swap.” | `propose "Try a seed swap" --id swap -d 'Tentative idea'`, then `edit swap --if-version 1 --title "Try a cutting swap"` | One proposed task, version 2, with the revised wording. Held in `ready --explain`, visible in agenda. | No extra decision to revise. One pending accept/dismiss choice. |
| “Keep this just for reference. The poster stock was uncoated A3.” | `remember "Poster stock reference: uncoated A3"` | Open memory, searchable with `find 'Poster stock' --kind memory` and shown by `prime --brief`. Outside both task queues. | No task decision. Startup visibility is a cost for reference that only needs to be found on request. |
| “Maybe someday I’ll make a zine. Keep the idea around. No need to decide or schedule it.” | `propose "Someday: make a zine" --id zine` | Undated and held, but agenda lists it as awaiting a decision. Partial fit. | One unwanted pending choice. |
| “Yes, let’s count the spare clips. Please take that on.” | `add "Count the spare clips" --move agent --id clips` | Open with agent next move. The sole result from `ready --move agent`. | No additional approval inferred. The study recorded the delegation without executing it. |
| “We agreed to send the final proof once the print shop confirms. We’re still waiting for that.” | `add "Send the final proof" --waiting-on 'print shop confirmation' --id proof` | Waiting with the named dependency. Visible under Waiting in agenda and held in `ready --explain`. | No proposal decision. The recorded dependency is the remaining trigger. |

The export contained five entities and six write events. All five left date fields unset and priority at zero. Only the explicitly delegated action stored an agent next move. `ready --explain` returned one ready task and three held tasks (two proposals, one wait). By this rubric, four captures fit the requested outcome and the someday capture only partly fits. The agenda presents two decisions, one expected by the proposed-task representation and one the person explicitly did not request.

Separate fresh databases exercised the alternatives below. The IDs are synthetic.

- Plain `add "Try a seed swap" --id swap` and `add "Count the spare clips" --id clips` both created ready work with an unset next move. The idea became actionable, and the clips task disappeared from the agent-filtered queue.
- After `add "Make a zine" --id zine`, `edit zine --plan 2030-05-01` still left the task ready. `defer zine someday` failed date validation. `defer zine 2030-05-01` hid it from ready until that date and left it open with a stored earliest start.
- `add "Poster stock reference: uncoated A3" --kind memory --id paper` was discoverable in the same search and startup reads as `remember`.
- `archive zine` after `propose "Someday: make a zine" --id zine` removed it from agenda and ordinary `find zine`. `show zine --brief` still worked with the known ID and the export retained the archived proposal. `accept swap --if-version 1` after `propose "Try a cutting swap" --id swap` from the same CLI actor changed it to open. The default `policy` returned an empty decider list, so the guide’s human-decision rule matters even where the CLI actor can run the command.

To repeat the captures from an installed CLI, create an empty directory and
run `daymark init --here`. Prefix the table's command arguments with
`daymark`. Read `daymark agenda`, `daymark ready --explain`,
`daymark ready --move agent`, and `daymark prime --brief` before exporting
with `daymark export --output snapshot.json`. The original writes supplied
a distinct operation ID per intent.

## Startup wording to try

> Use `add` for agreed actions. Set `--move agent` when the person expressly delegates the action to you. Use `propose` for an idea they want to decide on and `edit` to revise its wording before the decision. Leave acceptance and dismissal to the person. Use `remember` for durable reference that belongs in session context. A proposal appears under decisions, even if it has no date. If a someday idea needs keeping without a decision, use an agreed reference destination or explain Daymark’s proposal behavior and ask where they want it kept. Leave dates and priority unset unless supplied. When the wording is ambiguous, ask one focused question if the answer determines whether there is a commitment or who acts next.

The current `prime` already names proposals, memory, date defaults and the agent next-move flag. A scripted read against these five examples found that it leaves three choices implicit: how to revise a proposal, when to use the agent flag, and what “just keep it” costs in the agenda or startup context. The suggested block makes those choices explicit. The CLI runs tested the resulting command paths, while the side-by-side wording assessment was author judgment. No person or model performed these five captures. The earlier [command-selection](agent-journeys.md) and [MCP handoff](mcp-client-trials.md) trials used different prompts.

## Keeping an idea out of the working queue

We tried the same “keep this around” request with an existing Daymark
mechanism. The person selected a separate reference workspace. A synthetic
studio workspace already contained a decision about a cover. Saving “Try
a paper zine with foldout maps” as a memory in the reference workspace left
studio counts, ready items, memory previews and activity checkpoint
unchanged. A search for “foldout” in the reference workspace found it.
Opening that workspace intentionally also made its memories available to
the primer.

Later the person asked to consider a mockup. We created a proposal in the
studio workspace with the idea's source reference. Each workspace resolved
that source to its own record. Replaying the capture returned the existing
memory, and a second studio proposal using that source was refused.
Acceptance put the mockup into the ready queue. Retiring the reference
subsequently left the accepted work intact. The studio had three events and
the reference workspace two. A scope within a shared workspace does not
give an unfiltered primer this separation. A local database owner can
access both workspaces. HTTP workspace selection follows the credential.
