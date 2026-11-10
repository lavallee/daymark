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

Add `--json` for structured output and `--op NAME` to make a write safe to
retry. Exit status 3 is not found, 4 is a conflict (re-read, then decide),
5 is an invalid request with the broken rule on standard error.
