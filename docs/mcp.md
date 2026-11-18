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

## Take one task through a session

| Tool | Arguments | What to look for |
| --- | --- | --- |
| `daymark_prime` | `{}` | Check the workspace and actor before changing anything. |
| `daymark_add` | `{"title":"Compare sample readings","move":"agent","op":"capture-readings-01"}` | Capture requested work. For inferred work add `"proposed":true` and leave acceptance to the person. |
| `daymark_view` | `{"view":"ready","move":"agent"}` | Only tasks explicitly marked for agent action. |
| `daymark_act` | `{"action":"claim","id":"ID","minutes":30,"op":"claim-readings-01"}` | Take the task before work starts. Substitute the ID returned by capture. |
| `daymark_edit` | `{"id":"ID","plan":"fri","op":"plan-readings-01"}` | Record the intended day. A hard cutoff belongs in `due`. |
| `daymark_act` | `{"action":"release","id":"ID","text":"First sample checked. Compare the second next.","op":"handoff-readings-01"}` | Return unfinished work with context for the next session. |
| `daymark_view` | `{"view":"show","id":"ID"}` | Read the handoff note and current task state. |
| `daymark_remember` | `{"text":"Readings use metric units","op":"remember-units-01"}` | Keep a fact across sessions. A task-specific update belongs in a note. |

Every write accepts `op`. Generate a distinct operation ID for each new
intent. Reuse that ID on the *same* request after a timeout, disconnection or
server restart. A successful replay returns the original operation's result,
including its original version. Read `show` for the latest state.

With protocol revision 2025-06-18 or 2025-11-25, a successful write returns the
entity's ID, kind, status and version in `structuredContent`. Machine clients
can use the ID directly. Human-readable output stays in `content[0].text`.

```json
{
  "isError": false,
  "content": [{"type":"text","text":"dm-7q2t  Compare sample readings  [move:agent]"}],
  "structuredContent": {"id":"dm-7q2t","kind":"task","status":"open","version":1}
}
```

## Handle refusals

An invalid date, a live claim held by another actor, or a missing task produces
a tool result with `isError: true`. Read the stable code in its text. Correct
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
