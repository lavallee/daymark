# Bring one task over from a Todoist CSV

These commands use Daymark 0.8.2. Keep the original CSV in its exported order. Decide with the person which app will own future edits to each selected task. Daymark’s `import` command merges **Daymark workspace JSON**. A Todoist project CSV is a worksheet for reviewing selected tasks. You can do the review locally without connecting either account. Keep Todoist as the reference for omitted history, unresolved schedules and anything you leave there.

## Read the rows together

Suppose a project contains two tasks called “Review cover.” One is under the *Press* section. The other is a subtask under *Binding*, followed by a comment. A third task repeats every year. The following review keeps all three accounted for.

| What the CSV tells you | What to carry into Daymark |
| --- | --- |
| `TYPE=section` marks a section. `INDENT` shows task nesting. A `note` row can carry a task comment. | Use the original order to identify each task and its context. Keep presentation metadata, sections and comments separate from tasks. Agree on any Daymark parent relationship: unfinished children can hold their parent out of ready. |
| `CONTENT` may contain a label such as `@proof`. `AUTHOR` and `RESPONSIBLE` may identify Todoist users. | Check the intended local label and person IDs before assigning them. A person's ID in the export cannot identify the task row. If a comment must travel, identify its original source in the text. A new Daymark note records the caller as its author. |
| `DATE` can be a schedule or recurring expression. `DEADLINE` has a separate column. | Confirm which day is a planned work day and which is a firm cutoff. Preserve any supplied time and zone. Confirm the next occurrence and cadence before carrying over a series. Daymark accepts `--every year` with a confirmed first plan or deadline. Todoist says the project CSV omits the start date. |
| The documented template has no task-ID column. Completed tasks are excluded from this export. | Keep same-title rows distinct. Ask for the specific original task link or ID when repeat exports must match it. A missing row, renamed title or changed row position gives no authority to complete, delete or duplicate a task. |

Count task records with a CSV reader, even when their titles repeat. Review an unfamiliar column with the person who supplied the export before assigning its meaning. Todoist also documents an optional `IS_COLLAPSED` section-display column beyond its downloadable 14-column sample. For an export from another tool or a different template, revisit that tool’s format. [Todoist’s CSV specification, sample and export limits](https://www.todoist.com/help/account-and-billing/security/import-or-export-a-project-as-a-csv-file-in-todoist-YC8YvN)

<details>
<summary>Check the CSV locally with Python (read only)</summary>

This check counts logical CSV records, including quoted multiline cells. The record numbers help locate repeated task content in **this file only**. It stops on an unfamiliar header, row type or width so a changed export format is visible.

```sh
python - project.csv <<'PY'
import csv
import sys
from collections import Counter, defaultdict

known = set("TYPE CONTENT DESCRIPTION PRIORITY INDENT AUTHOR RESPONSIBLE DATE DATE_LANG "
            "TIMEZONE DURATION DURATION_UNIT DEADLINE DEADLINE_LANG IS_COLLAPSED".split())
counts, places = Counter(), defaultdict(list)
with open(sys.argv[1], encoding="utf-8-sig", newline="") as file:
    rows = csv.reader(file, strict=True)
    header = next(rows, [])
    if len(header) != len(set(header)) or not {"TYPE", "CONTENT"} <= set(header):
        sys.exit("Review the header: expected unique columns including TYPE and CONTENT.")
    if extra := set(header) - known:
        sys.exit("Review unfamiliar columns before transfer: " + ", ".join(map(repr, sorted(extra))))
    for record, row in enumerate(rows, 2):
        if not any(row):
            continue
        if len(row) != len(header):
            sys.exit(f"Review record {record}: unexpected number of cells.")
        cells = dict(zip(header, row))
        kind = cells["TYPE"]
        if kind not in {"meta", "section", "task", "note"}:
            sys.exit(f"Review record {record}: unfamiliar row type.")
        counts[kind] += 1
        if kind == "task":
            places[cells["CONTENT"]].append(record)
print("Records:", dict(counts))
print("Repeated task content at records:", [p for p in places.values() if len(p) > 1])
PY
```

The check does not resolve task identity or validate every field value. Open the original to examine the rows it identifies.

</details>

## Record the selected task

The person selects the *Press* task, supplies its original ID, confirms May 8 is the work day and May 10 is the cutoff, and asks to review a proposal. They leave the *Binding* task and recurring schedule in Todoist. Set the shell variable `source_ref` to the verified reference in `system:kind:id` form. Reuse the same system name across exports. An absent ID in the CSV is a reason to ask for the original task before continuing. Todoist documents an ID for each task. [Todoist developer documentation](https://developer.todoist.com/api/v1/)

```sh
daymark where
: "${source_ref:?Set source_ref to the verified original task reference}"
daymark show --source "$source_ref" --brief --json
```

If `where` reports no database, choose the destination first. For a new project-local database, run `daymark init --here` in its folder, then repeat the lookup. If the source already exists, review the record that came back. Exact source lookup also finds an archived record. Exit status 3 means that exact source is absent. A previous manual capture or a different source namespace can still describe the same work. If a local copy is plausible, use `daymark find "Review cover" --scope studio`, also check `--archived`, and ask the person about any uncertain match. A title match alone cannot settle it. Resolve other errors before writing. Once the person confirms a new proposal belongs here, choose an operation ID for this single capture and keep it for a retry.

```sh
daymark propose "Review cover" --scope studio \
  --source "$source_ref" \
  --plan 2030-05-08 --due 2030-05-10 --op press-cover-capture
daymark show --source "$source_ref" --brief --json
```

Verify one proposed record with both dates. Keep its returned ID and version. Retry an interrupted identical capture with the same operation ID. For a later rename, confirm the original identity, review the current record, then use `edit ID --if-version N --title "New title"` with a fresh operation ID. Leave the other same-title task and any missing rows alone. If the person wants Todoist to remain authoritative, agree which future changes, if any, will be copied over.

### If the person confirms an existing Daymark task is the same one

Keep the existing record, notes and status. Read `daymark get ID` for its current version and complete `source_refs` list. A source reference is stable once added, so verify both identities before linking. In this separate example, the person confirmed that `local-proof` at version 2, already linked to an inbox ticket, is the original Todoist task `SYN-805`. A local file named `link.json` contains the existing reference plus the verified one.

```json
{"id":"local-proof","source_refs":[
  {"system":"inbox","kind":"ticket","id":"fixture-17"},
  {"system":"west-press","kind":"task","id":"SYN-805"}
]}
```

```sh
daymark update --file link.json --expected-version 2 --operation-id link-proof
daymark show --source west-press:task:SYN-805 --brief --json
```

Read back the same local ID. A version conflict calls for a fresh review. An existing source on another record calls for resolving which record owns it.
