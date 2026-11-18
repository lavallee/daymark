---
name: daymark
description: Track a person's tasks, plans, deadlines, recurring chores and waiting-on items with the daymark CLI. Use when asked to remember, schedule, plan or follow up on something across sessions, when asked what is due, overdue or ready to do, or when a project's AGENTS.md says it uses Daymark. Not for code issue tracking inside a repository with its own tracker.
---

# Daymark

In an unfamiliar folder, `daymark where` explains which database and workspace you will use. Run `daymark prime` for the session. Check the workspace and actor, then read current
work and remembered facts. In a returning session, `daymark prime --brief`
returns five ready and five memory previews. Use `daymark find TEXT --kind memory --page` to retrieve a relevant remembered fact. If it reports no database, choose `daymark init`
for a per-user database or `daymark init --here` for this folder.

For project onboarding, `daymark onboard --check AGENTS.md` checks whether the startup instructions are current.

Then work in this loop:

1. `daymark ready` shows what can be done now. `daymark agenda` shows the days
   ahead, what is waiting and what is proposed. Use `ready --explain` with the same filters to understand a short or empty queue.
2. `daymark show ID` before acting. Check its readiness reasons and the notes left by earlier sessions.
3. Use a distinct `DAYMARK_ACTOR` for each concurrent worker, then `daymark claim ID`.
4. Do the work. `daymark note ID "what happened"` whenever you learn something
   that affects the task. When stopping mid-task, use
   `daymark release ID --note "what is verified and what remains"` so another worker can pick it up.
5. `daymark done ID [--note TEXT]`, or `daymark wait ID --on WHO` if it now depends on
   someone else.

When capturing:

- Use `--due` only for a real cutoff. Use `--plan` for an intention and
  `--start` for "not before".
- Never invent a date or priority. If the person did not give one, leave it out.
- If the person did not ask for the task, use `daymark propose`. Do not run
  `accept` on their behalf.
- For anything that repeats, use `--every`; add `--after-completion` when the
  interval counts from when it was last done.

For a project: `daymark plan PROJECT` shows it by stage, in its order
(`--by milestone|type|parent|assignee|FIELD`, `--tree`). `daymark move ID --stage S
[--after X]` moves a card; a stage sets the task's status, and `done`, `cancel`
and `reopen` move the card too. Add work with `--project P` and, only where the
project already uses them, `--stage`, `--type`, `--parent`, `--milestone M` and
`--set FIELD=VALUE`. Never add stages, types, fields or milestones the person did
not ask for. A project with `managed by` set belongs to another system: leave it
alone.

Every command works in one workspace (`--workspace NAME`); workspaces never
mix, and `daymark prime` says which one you are in. Use `ready --context ID` when the person wants tasks tagged for a particular tool or situation. Discover the IDs with `find --kind context --page`. Inside a workspace,
`--scope NAME` filters to one part of life. `daymark focus ID` puts a task on
the person's own short list; do it only when they ask.

If the workspace has a profile, prefer `daymark advise` to `daymark ready`: it
applies the person's chosen way of working and lists what to raise with them.
For a temporary request such as “show two today,” use `daymark advise --set
shortlist.size=2`. Save it with `daymark profile --set shortlist.size=2` only
when the person asks for that workspace preference.
For a recurring situation, `daymark profile --save-as NAME` keeps a named
copy. Use `daymark advise --profile NAME` for that situation. Saved names and
the active profile are shared across callers of this local workspace.
For a requested standing change, read `daymark profile --json`, supply its
version with `--if-version`, and reuse the same `--op` on a retry. The
returned undo command restores the previous settings while that change is current.
For a goal, `daymark explore GOAL` shows what must be true and what is stuck.

Add `--json` for structured output and `--op NAME` to make a write safe to
retry. Exit status 3 is not found, 4 is a conflict (re-read, then decide),
5 is an invalid request with the broken rule on standard error.


At session pickup, `daymark activity` shows recent changes, including handoff
notes. To continue from a saved workspace cursor, run
`daymark activity --after CURSOR`. Read more pages while the JSON `cursor`
is below `head`.

With MCP, call `daymark_prime` first. Each write tool takes `op`: keep it
for retries of that same write. Read `structuredContent.id` from a successful
write on protocol revision 2025-06-18 or newer.

To select work from an agent queue, use `daymark --as WORKER take`, optionally
with `--scope NAME`, `--context ID` and `--minutes N`. It claims one ready, unclaimed task
explicitly marked `--move agent`. Each worker needs a distinct name or HTTP
principal. JSON `null` means the queue is empty. Use a fresh `--op` for each
new selection and reuse it only to retry that selection.

For an edit based on an earlier `show`, pass `--if-version N` (MCP
`expected_version`). Re-read on conflict before deciding whether to edit.

For a long-running task, start with `daymark show ID --brief`. It includes the
latest three notes with authors and IDs. Expand one with
`daymark show ID --note NOTE_ID`, or follow `next_before_note` with
`daymark show ID --before-note NOTE_ID` to read older notes.

When presenting a proposal for a person's decision, show the source, note and
version from `show ID --brief`. The person can accept or dismiss that exact
version with `--if-version N`. If it changed, present the revision again.


When capture reports that a source already belongs to an entity, use
`daymark show --source SYSTEM:KIND:ID --brief`. Compare the new request with
the original task before treating it as a duplicate.
