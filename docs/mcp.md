# Use Daymark from an MCP client

Daymark gives a client six tools over stdio. Start with `daymark_prime` to see the
database, workspace, actor, local date and current work. Use `{"brief":true}` when
the client already knows the commands. The full startup guide uses
the tools this client can call. Pass a named scope on the first read. For an exact source inside that scope, use `find` with both `source` and
`scope`. A direct `show` reads the full record.

For timed deadlines on the person's own calendar day, use
`{"view":"agenda","local_day":true,"days":0,"scope":"press","json":true}`.
The session timezone determines day boundaries. Each timed row has its
original `deadline_exact` and a `viewer_deadline`. Overlapping date-only
cutoffs are grouped under `civil_dates` by their originating date and
retain their timezone. The current service takes `day_zone` on
`GET /v1/agenda`. A client verifies the service supports that filter.

## Connect two agents in five minutes

The walkthrough uses the development CLI (`0.6.0.dev0`). The tagged
`0.5.0` serves six tools and prints its onboarding snippet. The read-only
MCP switch, `where`, and `onboard --check` require the development version.
Start in the checkout or unpacked source archive containing this guide.
The steps below expect `daymark --version` to report `0.6.0.dev0`.

```sh
uv sync
. .venv/bin/activate
command -v daymark
daymark --version
daymark mcp --help
```

Use the reported absolute executable path for both editors. Change to
the project directory in this shell for the following steps.

1. **Review before setup.** Run `daymark where --json` to see how a database
   would be selected. It leaves the database unopened. Run `daymark onboard` to preview the
   project instructions. Read existing `AGENTS.md` and `CLAUDE.md` files before
   installing the snippet. If Beads owns code issues and a board owns approvals,
   keep those assignments and specify which commitments belong in Daymark.
   `daymark onboard --check AGENTS.md` checks only that file and returns 1
   when Daymark instructions are missing or outdated.
2. **Choose one database.** For a new project, run `daymark init --here`
   once. It creates `.daymark/daymark.sqlite3`. For an existing database,
   use its absolute path. From the project directory, verify the first
   actor’s selection with the following command. Repeat it with
   `--as sample-planner` for the second actor.

   ```sh
   db="$(pwd -P)/.daymark/daymark.sqlite3"
   daymark --db "$db" --workspace sample-project --as sample-reviewer where --json
   ```

   Both reports should have `file_exists: true`, the same database and
   workspace, and distinct actors. Compare `resolved_database` if a symlink is
   involved. `file_exists` checks presence; the primer checks that Daymark
   can read the database. If the selected file is missing, correct the path. To intentionally
   create a new file at a chosen path, use `daymark --db "$db" init`.
3. **Open two read-only sessions.** From the project folder, print a launch
   configuration for each editor. Use the same database and workspace in both.

   ```sh
   daymark --db "$db" --workspace sample-project --as sample-reviewer onboard --mcp-config claude
   daymark --db "$db" --workspace sample-project --as sample-planner onboard --mcp-config codex
   ```

   The first command prints a `daymark` entry under `mcpServers` for Claude
   Code's `.mcp.json`. The second prints a `[mcp_servers.daymark]` table for
   Codex's user `config.toml`. A project-local Codex configuration is loaded
   only for a trusted project. Review and merge the entry into the editor's existing
   configuration. For two agents using the same editor, print the appropriate
   format twice with distinct actor names. The commands print configuration
   to standard output. For a local file they check that a read-only Daymark
   session can open its schema. They leave the editor files and task data alone.

   Both launches use paths on the machine where you print the configuration.
   They use the Python installation that ran Daymark, the absolute
   database path, the selected timezone and `mcp --read-only`. Keep that
   installation available. For another stdio client, copy the executable and
   individual arguments from the JSON. Each host configures stdio servers in
   its own settings. [Claude Code MCP configuration](https://code.claude.com/docs/en/mcp) · [Codex MCP configuration](https://developers.openai.com/codex/mcp/)

4. **Verify from inside each agent.** List the tools: both should see
   `daymark_prime` and `daymark_view`. Call `daymark_prime` with
   `{"brief":true}` in each session. Compare the reported database, workspace
   and actor again. This confirms the launched processes actually received
   the intended arguments, even if there are zero tasks. Paths are local to a host.
   For agents on different machines, use the same authenticated service. When a person wants
   an agent to change tasks, remove `--read-only` for that agent and reconnect.
   A writable MCP launch can initialize a previously missing file.
   A host may also ask for approval before sending a mutation. If the host
   refuses, the change has not been saved. Report the refusal and use a
   read to verify the current record.
   To adopt the snippet, use `daymark onboard --write AGENTS.md` or
   `daymark onboard --write CLAUDE.md` for each file that needs it.

### Connecting to an HTTP workspace

Install the `client` extra in the environment running the MCP executable.
For a checkout, `uv sync --extra client` includes it. Configure the launch
arguments with `daymark --url URL --token-env DAYMARK_TOKEN onboard
--mcp-config claude` or choose `codex`. The output names the credential's
environment variable. Provide its value in the editor process's environment.
Include any service mount path in the URL. Give cooperating agents credentials
for distinct principals in the same workspace.

`daymark --url URL where --json` reports the endpoint without contacting
it. A successful handshake or tool list also proves only that the local
MCP process started. Call `daymark_prime {"brief":true}` to verify that
a current HTTP service accepts the credential and confirms the workspace
and actor. Local `--workspace` and `--as` labels do not select HTTP identity.
The primer reports the endpoint origin and authenticated workspace. If a proxy
serves several base paths, compare the full base URLs in the two host configurations.
Only the service operator can confirm the underlying database file. If prime reports
`unauthenticated`, correct the host’s credential and restart the process.
A rejected `ready` limit can be corrected on the same connection by passing
a value between 1 and 1,000.

An initial note on an ordinary authenticated capture records the principal
handling that write. Separate note operations follow the same rule. Raw API
creates and native imports can preserve a historical note's supplied author.
Each write event separately identifies the actor that submitted the change.
When a 0.5.0 service cannot confirm the principal for a fresh embedded note,
the current client leaves that note’s author empty. Its create event still
records the submitting principal.

When the person names a scope, `daymark_prime {"scope":"studio"}` gives
counts, up to five ready tasks and up to five remembered facts from that
scope in one snapshot. Use `{"scope":""}` for records with no scope. Memory
continuations keep the filter. The scoped response omits the workspace-wide
activity checkpoint and guide. A task can remain blocked by a prerequisite
outside the scope; the primer includes only matching task and memory titles.
A scope filters a read inside the selected workspace. It confers no separate
authority. Older HTTP services must confirm support before a filtered primer
can run. If the service lacks it, keep the same scope on the ready and
memory-find views.

For a portfolio, call `daymark_view` with `{"view":"projects","scope":"studio"}`.
The project itself must belong to the selected scope. Its rollup still
counts all member tasks, including ones blocked by a prerequisite elsewhere.
When a linked lead or goal belongs elsewhere the structured result identifies
its role under `outside_scope` and omits its identity.

## Give a reviewer a read-only connection

Place `--read-only` after the `mcp` subcommand as in the setup above.

The client discovers `daymark_prime` and `daymark_view`. It can inspect tasks,
history, search pages and advice. Direct calls to a write name, including a
retry with an existing operation ID, receive a refusal. Initialization and
prime identify the session as read-only.

Locally, this opens an existing database with SQLite `mode=ro` and requires
the current schema. Separate read calls see committed changes from other
workers. SQLite may create `-wal` and `-shm` sidecar files in a writable
directory during a read-only session. To inspect the selected path without
opening SQLite, use `where`. [SQLite read-only WAL behavior](https://www.sqlite.org/wal.html#read_only_databases). An HTTP-backed session accepts the same switch. Its Daymark client
permits GETs and the read-only search, query and advice POST routes. The HTTP credential’s
authority in other processes and the host’s other configured tools are
configured separately.

## Take one task through a session

| Tool | Arguments | What to look for |
| --- | --- | --- |
| `daymark_prime` | `{}` | Check the workspace and actor before changing anything. |
| `daymark_add` | `{"title":"Compare sample readings","move":"agent","op":"capture-readings-01"}` | Capture requested work. For inferred work add `"proposed":true` and leave acceptance to the person. |
| `daymark_view` | `{"view":"ready","move":"agent","explain":true}` | See eligible work, how many matching tasks are held, and example IDs for each reason. |
| `daymark_act` | `{"action":"claim","id":"ID","minutes":30,"op":"claim-readings-01"}` | Take the task before work starts. Substitute the ID returned by capture. |
| `daymark_edit` | `{"id":"ID","plan":"fri","op":"plan-readings-01"}` | Record the intended day. A hard cutoff belongs in `due`. |
| `daymark_act` | `{"action":"release","id":"ID","text":"First sample checked. Compare the second next.","expected_version":2,"op":"handoff-readings-01"}` | Return unfinished work reviewed at version 2. Use the task's actual version. The same guard applies to `done`. |
| `daymark_view` | `{"view":"show","id":"ID"}` | Read the handoff note and current task state. |
| `daymark_remember` | `{"text":"Readings use metric units","op":"remember-units-01"}` | Keep a fact across sessions. A task-specific update belongs in a note. |

Every write accepts `op`. Generate a distinct operation ID for each new
intent. Reuse that ID on the *same* request after a timeout, disconnection or
server restart. A successful replay returns the original operation's result,
including its original version. Read `show` for the latest state. For an edit
based on that read, pass its `version` as `expected_version` to `daymark_edit`.
The same guard is available for `accept` and `dismiss`.
If a teammate has changed the task, Daymark returns `conflict` and the newer
state survives. For a decision or edit based on a specific read, retain its
`review_token` from show or brief and pass that string in the write. It detects
a hard delete followed by recreation at the same ID and version. The token
comes from existing event evidence and can be null after a partial import.
Changes to other tasks leave it usable.

With protocol revision 2025-06-18 or 2025-11-25, a successful write returns the
entity's ID, kind, title, status and version in `structuredContent`. Machine clients
can use the ID directly. Human-readable output stays in `content[0].text`.

```json
{
  "isError": false,
  "content": [{"type":"text","text":"dm-7q2t  Compare sample readings  [move:agent]"}],
  "structuredContent": {"id":"dm-7q2t","kind":"task","title":"Compare sample readings","status":"open","version":1}
}
```

A successful `take`, `claim` or `release` also includes `claimed_by` and
`claim_until`. After release both fields are null. To claim the first eligible
task without picking an ID first, call
`daymark_act {"action":"take","scope":"workshop","op":"shift-01"}`.
An empty take returns `{"task":null}` in structured output.
For an assignment bound to one project, supply `"project":"press"` on the
take itself. The service checks membership while claiming, and the receipt
identifies the selected project. If a coordinator moves a task before the
claim, it is no longer a candidate for the old project.

To assign responsibility, send `assignee` on `daymark_add` or `daymark_edit`.
It accepts an existing person or principal ID, or an exact display name
ignoring case. For example, `{"id":"sample","assignee":"custodian","op":"assign-sample"}`
sets the assignee on a standalone task. An empty string on edit clears it.
For a name-only request, pass the supplied name as given to check across both
kinds. Several matching names produce candidate IDs for clarification.
To explore records, search with `view=find, kind=person` or `kind=principal`.
A search in just one kind can miss another match. The write
receipt includes `assignee_id`. A claim concerns temporary work pickup and
can be held independently. Keep the ID for retries if the display name changes.

To check whether an external source belongs to a particular working scope,
use `{"view":"find","source":"catalog:proof:Q-41","scope":"studio","json":true}`.
The reply contains at most one compact hit. A matching source in another
scope produces an empty page. The normal kind, status, archive and claim
filters still apply. A title search does not search source references.
Use `show` when you need the matched record’s detail and relationships.

## Read only the detail you need

By default, views return the same short text an operator sees in the CLI.
Use `json:true` on any `daymark_view` to get named fields. Modern clients read
`structuredContent`. The text block identifies where to find it so the payload
appears once on the wire. A host that only displays text can omit `json`.
Clients using the 2024-11-05 or 2025-03-26 revision receive the JSON in `content[0].text`.

| Question | `daymark_view` arguments |
| --- | --- |
| What can this agent pick up? | `{"view":"ready","move":"agent","scope":"workshop","json":true}` |
| Why is this queue empty? | `{"view":"ready","scope":"workshop","explain":true,"json":true}` |
| Which tasks am I still holding? | `{"view":"find","claimed":true,"json":true}` |
| What changed since the person approved version 1? | `{"view":"show","id":"sample","since_version":1,"json":true}` |
| What did the last worker record? | `{"view":"show","id":"sample","brief":true,"json":true}` |
| Which task came from this external reference? | `{"view":"show","source":"samples:sheet:Q-41","brief":true,"json":true}` |
| Can I read the whole note? | `{"view":"show","id":"sample","note":"NOTE_ID","json":true}` |
| Which goal conditions can move in this scope? | `{"view":"explore","scope":"workshop","json":true}` |
| How do I record a reply to a wait I reviewed? | Call `daymark_act` with `action:"resume"`, the task ID, and the `expected_version` and `review_token` from that review. A changed wait returns a conflict. `wait` accepts the same guards. |
| Which tasks await acceptance? | `{"view":"find","status":"proposed","json":true}` |
| Where is the saved fact? | `{"view":"find","kind":"memory","text":"metric","limit":5,"json":true}` |
| Which tasks changed after my cursor? | `{"view":"activity","after":10,"limit":5,"json":true}` |

For `explore`, scope selects conditions. Linked work in other scopes still counts toward gaps and blocked conditions. A condition’s optional `outside_scope` reports aggregate links without identifying those other records.

A ready row carries its ID, title, current version and the reason for its place.
For “What can I do on the print project?”, use
`{"view":"ready","project":"press","json":true}`. Add `"explain":true` for
exact counts and examples of project members being held. Scope and context
can narrow the selection further. A task linked only as a child of a project
member has its own project membership.
Supply `context` with a context entity ID to filter ready work, its explanation, advice or the agenda.
Capture and edit accept `contexts`, an array of context IDs. When advice holds
ready work back, the text includes a ready-view call for the same connection.
Structured advice includes `ready_query`. Merge its filters into
`{"view":"ready","json":true}` to inspect the ordinary order. Tagged ready rows
show up to three context IDs and a count of any additional ones.
A search page returns `items` and `next`. To continue, supply `next` as the
string `after` and keep the original filters. Activity uses a numeric cursor.
Show accepts exactly one of `id` or `source`. Its brief supplies individual note
IDs and a cursor to older previews. The full show includes note IDs, authors,
timestamps and text. Both full show and brief name up to five unresolved
prerequisites with their titles and current statuses in `blockers`. Their
`blocked_by` arrays retain the IDs. For a circular wait witnessed in task detail,
`readiness.cycle` lists the child or prerequisite steps that lead back to
the requested task. Confirm the intended relationship before changing it.

## Use a workspace preference

Use `daymark_view {"view":"profiles","json":true}` to discover available
profiles, the active version and adjustment names with their defaults. For
parameter limits and the basis of a strategy, use
`{"view":"profiles","strategy":"time-fit","json":true}`. That read describes
minutes per individual task, its default of 30, and its range of 1 to 1,440.
Preview a different working style with
`{"view":"advise","profile":"plain"}`. To adopt it for this workspace on the
local database, send `daymark_act` an action such as the following.

```json
{"action":"use_profile","id":"plain","expected_version":0,"op":"choose-plain-01"}
```

Use the version actually returned by the read. An unconfigured workspace
starts at version zero. Supply `set` or `drop` without `id` to adjust the
active profile. The receipt identifies the workspace, new version, and the
operation and expected version needed to restore the earlier settings. Its
text includes the exact CLI command for use with the same database. For an
MCP-only correction, pass the receipt's `restore_call` directly to
`tools/call`. It names the profile operation, its resulting revision and a
retry ID. A later preference change produces a conflict. For example, the
restore call from the first change above has these arguments:

```json
{"action":"restore_profile","id":"choose-plain-01","expected_version":1,"op":"choose-plain-01:restore-profile"}
```

The `undo` action addresses an entity ID. Profile restoration addresses an
operation ID through `restore_profile`. Named profile administration remains
available through local `daymark profile` commands.

An HTTP-backed MCP session reads active and named profiles from its credential's
workspace. The service evaluates built-in stages. The advice result identifies
the workspace, source revision and `execution="service"`. Unavailable saved
stages are listed in `profile_source.skipped`. To run an extension installed
on the MCP client, explicitly use a built-in base and name the extension in
`set`. This one-call preview reports `execution="client-preview"`.
On a v0.5.0 service the workspace profile is unavailable. The current client
uses a labeled plain default and still accepts built-in previews. Upgrade both
the stdio process and the HTTP service for remote workspace preferences.

Use matching client and service versions for memory retirement. The current
service verifies the memory’s kind inside the archive transaction and accepts
the review token returned by its detail view.

## Mix client and service versions

The stdio MCP process and the HTTP service can run different versions of
Daymark. Against the 0.5.0 HTTP service, the current client can capture new
work, search, and project ready rows to a compact result even when the service
returns full records. The earlier service can return a previous operation's
result but lacks the original request evidence needed to verify a retry. The
current client asks for a service upgrade if that operation already exists.
Keep the same operation ID for the retry after the upgrade. For guarded retries
on both sides, upgrade the stdio process as well as the service.

A full `show` with `json:true` also returns `temporal` when a task has dates.
Its keys are `deadline`, `planned`, `earliest_start` and `latest_target` when
set. Each carries the stored `date` or offset-aware `at`, IANA `timezone`
and the original `expression` when available. Human labels include the
offset during a repeated hour and any sub-minute precision. For a local
clock reading that occurs twice, ask which of the two offsets the person
means. A skipped local time needs a different clock reading.

To reverse the latest reviewed change, call `daymark_act` with `action:"undo"`,
the task ID, its observed `expected_version` and `review_token`. A concurrent
edit causes a conflict. To restore one earlier field while preserving later
work, inspect event history and a version comparison, then submit a guarded
edit for that field.

When inspecting a proposal, full or brief detail includes `decision_access`.
`actor` is the authenticated principal on the HTTP connection or the named
local actor. `policy_allows` reports whether the workspace permits this
actor to accept or dismiss. `rule` identifies the policy as
`named_deciders` or `any_writer`. MCP marks `session_read_only:true`
on read-only connections. A missing policy signal from an older service
appears as `policy_allows:null`. The person's decision still determines
whether to act. The write checks the current policy again.

## Handle refusals

An invalid date, a live claim held by another actor, or a missing task produces
a tool result with `isError: true`. Modern clients can branch on
`structuredContent.error.code` and display its `message`, optional `details`
and `recovery`. All revisions carry the code in the text. Correct
an invalid argument, re-read a conflicted task, or ask the person for a
decision when a policy refuses the action. Input types matter. JSON
`false` is a boolean, and `"false"` is a string. A parameter that belongs to a
different view or action is refused before the operation runs.

A service that rejects the configured credential returns `unauthenticated`
(HTTP 401). Check the bearer credential for that endpoint. A valid identity
whose action is prohibited receives `forbidden` (HTTP 403). The bridge
reports these codes without copying credentials or HTTP response bodies.

When the HTTP service loses a write response, it may already have committed
the change. The error carries the operation ID in its text and in
`structuredContent.error.operation_id`. Repeat the original arguments with
that value as `op`. If the caller omitted `op`, the HTTP bridge generated one
before sending the write. Choose your own key before submission when the
entire stdio process or its response could be lost.

An unknown tool or a malformed request produces a JSON-RPC error. Refresh
`tools/list` to discover tool names and argument schemas. The server writes one
JSON-RPC message per stdout line. Diagnostics use stderr.

Daymark negotiates the tools/stdio revisions 2024-11-05, 2025-03-26,
2025-06-18 and 2025-11-25. An unrecognized revision receives the newest one
Daymark supports. The two earliest revisions return text results. March 2025
also accepts JSON-RPC batches after initialization. The current TypeScript v2
client connects through its default legacy handshake or its automatic fallback
to 2025-11-25. A client pinned to 2026-07-28 requires a different revision.
[March 2025 base protocol](https://modelcontextprotocol.io/specification/2025-03-26/basic),
[TypeScript client guide](https://ts.sdk.modelcontextprotocol.io/v2/clients/connect),
[MCP lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle),
[MCP tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools),
[MCP transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports).


## Set a reminder for an existing task

Use `daymark_act` with the task ID, an explicit date and time, and the
recipient's existing person or principal ID. The session timezone governs
a local wall time.

```json
{"action":"remind","id":"draft","when":"2030-05-03T09:00","recipient":"reviewer","op":"draft-review-reminder-01"}
```

A Europe/London session stores 08:00 UTC on that date. The response gives
the reminder ID, recipient, scheduled instant, channel and state. At capture
the state is `pending`. The built-in worker records a delivery locally when
it runs and the reminder is eligible. An external notification requires a
configured delivery integration. The task keeps its original plan and
deadline. A proposal's reminder stays dormant pending acceptance.


To revisit an archived task, proposal or remembered fact, read its exact
record with a brief show, then call `daymark_act` with action `unarchive`,
`id`, `review_token` and a stable `op`. Restoration retains its recorded
status. An archived proposal requires restoration before a fresh acceptance.

For a read-only preview of one recurring transition, pass `forecast:"skip"`
or `forecast:"done"` to the show view with the exact ID or source. Its result
names the successor and its dates or explains why a new round would not be
created. Read it again before acting if time or related records have changed.
[Recurring-work trials](research/recurring-work.md)
