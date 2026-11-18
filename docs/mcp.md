# Use Daymark from an MCP client

Daymark gives a client six tools over stdio. Start with `daymark_prime` to see the
database, workspace, actor, local date and current work. Use `{"brief":true}` when
the client already knows the commands.

## Connect a workspace

Choose an absolute path to the database. Create it once with
`daymark --db /path/to/tasks.sqlite3 init`. Supply that same path in your
client configuration so its working directory cannot change which tasks it
opens. Give each cooperating agent its own actor name so claims distinguish
them.

```json
{
  "mcpServers": {
    "daymark": {
      "command": "daymark",
      "args": [
        "--db", "/path/to/tasks.sqlite3",
        "--workspace", "sample-project",
        "--as", "sample-reviewer",
        "--tz", "UTC",
        "mcp"
      ]
    }
  }
}
```

If the host cannot find `daymark`, set `command` to the installed executable's
absolute path. Keep the arguments as separate list entries. A client can also
start `daymark --url URL --token-env DAYMARK_TOKEN mcp` with the optional
service dependencies installed. The HTTP credential selects the workspace and
the server records its authenticated principal.

## Give a reviewer a read-only connection

For questions such as “which task is blocking the shipment?”, configure the
MCP process with `--read-only` after `mcp`. Keep the database and workspace
arguments before the subcommand. For example:

```json
{"command":"daymark","args":["--db","/path/to/tasks.sqlite3","--workspace","sample-project","mcp","--read-only"]}
```

The client discovers `daymark_prime` and `daymark_view`. It can inspect tasks,
history, search pages and advice. Direct calls to a write name, including a
retry with an existing operation ID, receive a refusal. Initialization and
prime identify the session as read-only.

Locally, this opens an existing database with SQLite `mode=ro` and requires
the current schema. Separate read calls see committed changes from other
workers. An HTTP-backed session accepts the same switch. Its Daymark client
permits GETs and the read-only search/query POST routes. The HTTP credential’s
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
| `daymark_act` | `{"action":"release","id":"ID","text":"First sample checked. Compare the second next.","op":"handoff-readings-01"}` | Return unfinished work with context for the next session. |
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

## Read only the detail you need

By default, views return the same short text an operator sees in the CLI.
Use `json:true` on any `daymark_view` to get named fields. Modern clients read
`structuredContent`. The text block identifies where to find it so the payload
appears once on the wire. A host that only displays text can omit `json`.
Clients using the 2024-11-05 revision receive the JSON in `content[0].text`.

| Question | `daymark_view` arguments |
| --- | --- |
| What can this agent pick up? | `{"view":"ready","move":"agent","scope":"workshop","json":true}` |
| Why is this queue empty? | `{"view":"ready","scope":"workshop","explain":true,"json":true}` |
| Which tasks am I still holding? | `{"view":"find","claimed":true,"json":true}` |
| What changed since the person approved version 1? | `{"view":"show","id":"sample","since_version":1,"json":true}` |
| What did the last worker record? | `{"view":"show","id":"sample","brief":true,"json":true}` |
| Which task came from this external reference? | `{"view":"show","source":"samples:sheet:Q-41","brief":true,"json":true}` |
| Can I read the whole note? | `{"view":"show","id":"sample","note":"NOTE_ID","json":true}` |
| Where is the saved fact? | `{"view":"find","kind":"memory","text":"metric","limit":5,"json":true}` |
| Which tasks changed after my cursor? | `{"view":"activity","after":10,"limit":5,"json":true}` |

A ready row carries its ID, title, current version and the reason for its place.
Supply `context` with a context entity ID to filter ready work, its explanation, advice or the agenda.
Capture and edit accept `contexts`, an array of context IDs. Tagged ready rows
show up to three context IDs and a count of any additional ones.
A search page returns `items` and `next`. To continue, supply `next` as the
string `after` and keep the original filters. Activity uses a numeric cursor.
Show accepts exactly one of `id` or `source`. Its brief supplies individual note
IDs and a cursor to older previews. The full show includes note IDs, authors,
timestamps and text.

## Change a local working preference

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
text includes the exact CLI command for use with the same database. Named
profile administration remains available through `daymark profile` commands.

## Mix client and service versions

The stdio MCP process and the HTTP service can run different versions of
Daymark. Against the 0.5.0 HTTP service, the current client can capture new
work, search, and project ready rows to a compact result even when the service
returns full records. The earlier service can return a previous operation's
result but lacks the original request evidence needed to verify a retry. The
current client asks for a service upgrade if that operation already exists.
Keep the same operation ID for the retry after the upgrade. For guarded retries
on both sides, upgrade the stdio process as well as the service.

## Handle refusals

An invalid date, a live claim held by another actor, or a missing task produces
a tool result with `isError: true`. Modern clients can branch on
`structuredContent.error.code` and display its `message`, optional `details`
and `recovery`. All revisions carry the code in the text. Correct
an invalid argument, re-read a conflicted task, or ask the person for a
decision when a policy refuses the action. Input types matter. JSON
`false` is a boolean, and `"false"` is a string. A parameter that belongs to a
different view or action is refused before the operation runs.

An unknown tool or a malformed request produces a JSON-RPC error. Refresh
`tools/list` to discover tool names and argument schemas. The server writes one
JSON-RPC message per stdout line. Diagnostics use stderr.

The server negotiates the dated tools/stdio revisions 2024-11-05, 2025-06-18
and 2025-11-25. An unrecognized revision receives the newest one Daymark
supports, allowing the client to continue if it supports that revision.
Protocol 2024-11-05 clients receive text results.
[MCP lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle),
[MCP tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools),
[MCP transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports).
