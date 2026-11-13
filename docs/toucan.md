# A Daymark project as a plan: `toucan-source-daymark`

Daymark can be the system of record for a project that people manage visually
somewhere else: a board, a gantt chart, a timeline, a dependency graph or a table.
The visual tool holds a *view*; Daymark holds the work. What a person drags in the
view is written back to Daymark as a named field change, and what changes in
Daymark shows up in the view.

`toucan-source-daymark` is that seam. It ships in the core package (no HTTP
framework needed) and is written for [toucan](https://github.com/lavallee/toucan),
a browser canvas, but nothing in it depends on toucan: any program that can run a
command and read JSON can use it. The protocol is called `toucan.plan/1`.

## What it is for

- Show a project's tasks on a board grouped by stage, on a gantt by date, as a
  dependency graph, or as a table, without copying them anywhere.
- Let a person move a card to another stage, drag a bar to new dates, reorder a
  column or rename a card, and have that become an ordinary Daymark write, with
  the same version check, idempotency and history as any other.
- Notice cheaply when something changed in Daymark (another agent completed a
  task, someone edited it on the command line) so the view can redraw.

It does not delete work: there is no delete. Removing work is a decision made in
Daymark (`daymark cancel`, `daymark archive`).

## Commands

```sh
toucan-source-daymark list
toucan-source-daymark read WORKSPACE/PROJECT_ID
toucan-source-daymark changes WORKSPACE/PROJECT_ID --after CURSOR
toucan-source-daymark write WORKSPACE/PROJECT_ID < changes.json
```

Each prints one JSON object on standard output and exits 0. A failure exits
non-zero and prints one JSON object on standard error,
`{"error": CODE, "message": TEXT}`, where `CODE` is `not_found` (exit 3),
`conflict` (4), `invalid` (5), `forbidden` (7) or `unavailable` (1).

It opens `--db PATH`, else `$DAYMARK_DB`, else the per-user database that
`daymark init` creates (`$XDG_DATA_HOME/daymark/daymark.sqlite3`). Unlike the
`daymark` command it does not look for a `.daymark/` folder above the working
directory: the program calling it usually runs somewhere unrelated. It never
creates a database; `list` on a missing one returns no sources. Opening a
database written by an older Daymark upgrades it in place, as `daymark` does.

A reference is `WORKSPACE/PROJECT_ID`, split at the first `/`. Workspaces stay
separate: a reference reads and writes only its own workspace.

### `list`

```json
{"sources": [{"ref": "home/site", "title": "Website relaunch",
              "description": "The new site", "readonly": false}]}
```

Every unarchived project in every workspace. `description` is the first line of
the project's description, or names the workspace. `readonly` is an addition to
the protocol: true for a project another system manages (below).

### `read`

A plan: the project as `source`, the fields it offers, its items in manual order,
and `cursor`, the workspace's latest event cursor at the moment of the read. All of
it comes from one read transaction, so following `changes` from that cursor misses
nothing.

```json
{
  "plan": 1,
  "source": {"ref": "home/site", "title": "Website relaunch", "description": "",
             "readonly": false},
  "cursor": "184",
  "fields": [
    {"id": "stage", "label": "Stage", "type": "option", "writable": true,
     "options": [{"id": "todo", "label": "To do", "color": "grey"},
                 {"id": "doing", "label": "Doing", "color": "blue"},
                 {"id": "done", "label": "Done", "color": "green", "done": true}]}
  ],
  "items": [
    {"id": "map", "version": 3, "title": "Draft the sitemap", "detail": null,
     "stage": "doing", "assignee": "ada", "start": "2030-04-10", "end": "2030-04-20",
     "deadline": null, "priority": "2", "labels": [], "dependsOn": [], "parent": null,
     "milestone": false, "done": false, "estimate": 240, "url": null, "status": "open"}
  ]
}
```

(`fields` is abbreviated here; the full list is in the mapping table.)

Items are the project's unarchived tasks that sit on its board. A proposed task is
not an item: it is a suggestion awaiting a person's decision in Daymark, not work
yet. A cancelled task is an item only when the project has a cancelled stage.

### `changes`

```json
{"cursor": "187", "changed": true}
```

Whether anything the plan shows changed after `--after`: the project itself, a
task in it, a task that entered or left it, or a person or label it can name. The
answer is one indexed query from the cursor, cheap enough to ask every few seconds.
A cursor from the future (for example after a restore) answers `changed: true`.

### `write`

Standard input:

```json
{"changes": [
  {"op": "update", "id": "map", "version": 3, "mutation": "c0ffee",
   "set": {"stage": "done", "end": "2030-04-22"}, "place": {"after": "copy"}},
  {"op": "create", "mutation": "f00d", "set": {"title": "New card", "stage": "todo"},
   "place": {"after": null}}
]}
```

Standard output:

```json
{"cursor": "189", "results": [
  {"mutation": "c0ffee", "ok": true, "item": {"id": "map", "version": 5, "...": "..."}},
  {"mutation": "f00d", "ok": true, "item": {"id": "dm-k3p9x2ab", "version": 2, "...": "..."}}
]}
```

- `set` carries only changed fields; `null` clears one.
- `version` must be the item's current version. A stale one answers
  `"error": "conflict"` with the item as it now stands, to go back to.
- `mutation` becomes the operation ID (`toucan:MUTATION`). Sending the same change
  again writes nothing and answers with the item as it now stands.
- `place: {"after": ID}` puts the item directly after another item; `{"after":
  null}` puts it first. `{"before": ID}` and `{"before": null}` (last) are also
  accepted.
- A change's field update and its placement are one transaction: both happen or
  neither does. Changes are independent of each other: one failing does not stop
  the rest.
- A create's new ID is derived from the mutation, so a replayed create cannot
  create twice. Without `place`, a new item goes last.
- A project another system manages refuses the whole write with `forbidden`.

## Field mapping

| Plan field | Type | Daymark | Read | Write |
| --- | --- | --- | --- | --- |
| `id` | | `id` | | |
| `version` | | `version` | changes on every write | quoted by an update |
| `title` | text | `title` | | |
| `detail` | text | `description` | empty reads as `null` | `null` clears |
| `stage` | option | `stage` | the stored stage, else the first stage for the task's status | sets the status to the stage's status |
| `assignee` | option | `assignee_id` | options: the workspace's people and principals | must be one of them |
| `start` | date | `planned`, else `earliest_start` | civil date in the value's own zone | sets `planned` |
| `end` | date | `latest_target`, else `deadline` | civil date in the value's own zone | sets `latest_target` |
| `deadline` | date | `deadline` | | sets `deadline`; only an explicit write moves it |
| `priority` | option | `priority` | `"1"` (highest) to `"4"`; 0 reads as `null` | `null` sets 0 |
| `labels` | options | `label_ids` | options: the workspace's labels | |
| `dependsOn` | options (`items: true`) | `depends_on` | only dependencies inside the project | replaces those; dependencies outside the project are kept |
| `parent` | option (`items: true`) | `parent_id` | `null` when the parent is not an item | |
| `milestone` | bool | `milestone` | | |
| `done` | bool | `status` | `status == completed` | true completes; false reopens a completed or cancelled task |
| `estimate` | number | `estimate_minutes` | minutes | |
| `url` | | | always `null` | not writable |
| `status` | option, read-only | `status` | open, waiting, completed, cancelled | not writable |

Two declarations go beyond the base protocol and are safe for a reader to ignore:
`items: true` on `dependsOn` and `parent` says the options are the plan's own
items, and `source.managedBy` names the system that owns a read-only project.

### Dates

A task has four separate times: a hard `deadline`, a movable `planned` intention,
an `earliest_start` ("not before") and a soft `latest_target`. A plan has a start
and an end. They read as:

- **start** = `planned`, else `earliest_start`
- **end** = `latest_target`, else `deadline`
- **deadline** is also given on its own, so a view can mark it.

Writing `start` sets `planned`; writing `end` sets `latest_target`. Dragging a bar
therefore never moves a hard deadline. A written date keeps the zone of the value
it replaces (or another date on the task, or the local zone). Clearing `start`
clears `planned`, so a "not before" date shows through; clearing `end` likewise
lets a deadline show through.

A write is refused with `invalid`, never adjusted, when the resulting start falls
after the end, or when the new `latest_target` falls before the task's
`earliest_start` (Daymark's own rule).

### Stages

A project defines its own stages (`daymark project TITLE --stages SPEC`); one that
defines none reads as **To do** (open), **Waiting** (waiting) and **Done**
(completed). Each stage carries the lifecycle status a task takes there, and the two
stay consistent: moving a card to a stage sets the status, and completing,
cancelling or reopening a task in Daymark moves its card. See
[decisions](decisions.md) P01 to P04.

## Read-only projects

A project whose `managed_by` is set belongs to another system that rewrites it
(for example a commitments tracker that projects into its own workspace every few
minutes). Edits made in a view would be overwritten, so the adapter reads such a
project as `readonly`, marks every field not writable, and refuses writes with
`forbidden`. Set it when creating the project (`daymark project TITLE --managed-by
SYSTEM`) or later (`daymark edit PROJECT --managed-by SYSTEM`; `""` clears it).
Daymark itself still accepts writes to such a project, so the owning system keeps
working unchanged.

## Trying it by hand

```sh
export DAYMARK_DB=/tmp/plan-demo.sqlite3
daymark init
daymark --workspace demo project "Website relaunch" --id site \
  --stages "todo:To do:open:grey,doing:Doing:open:blue,done:Done:completed:green"
daymark --workspace demo add "Draft the sitemap" --project site --id map --plan 2030-04-10
toucan-source-daymark read demo/site
echo '{"changes":[{"op":"update","id":"map","version":1,"mutation":"m1",
  "set":{"stage":"doing"}}]}' | toucan-source-daymark write demo/site
toucan-source-daymark changes demo/site --after 0
```
