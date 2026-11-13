---
name: daymark
description: Track a person's tasks, plans, deadlines, recurring chores and waiting-on items with the daymark CLI. Use when asked to remember, schedule, plan or follow up on something across sessions, when asked what is due, overdue or ready to do, or when a project's AGENTS.md says it uses Daymark. Not for code issue tracking inside a repository with its own tracker.
---

# Daymark

Run `daymark prime` first. It prints the rules, the commands and the current
state. If it reports no database, run `daymark init`.

Then work in this loop:

1. `daymark ready` shows what can be done now. `daymark agenda` shows the days
   ahead, what is waiting and what is proposed.
2. `daymark show ID` before acting: read the notes left by earlier sessions.
3. `daymark claim ID` if other agents may be active.
4. Do the work. `daymark note ID "what happened"` whenever you learn something
   or stop.
5. `daymark done ID`, or `daymark wait ID --on WHO` if it now depends on
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
mix, and `daymark prime` says which one you are in. Inside a workspace,
`--scope NAME` filters to one part of life. `daymark focus ID` puts a task on
the person's own short list; do it only when they ask.

Add `--json` for structured output and `--op NAME` to make a write safe to
retry. Exit status 3 is not found, 4 is a conflict (re-read, then decide),
5 is an invalid request with the broken rule on standard error.
