# Operating Daymark with synthetic data

The engine works in-process without a service or credentials. Install optional
HTTP dependencies only when running a server or Python HTTP client:

```sh
uv sync --dev
daymark --help
daymark --db example.sqlite3 --workspace example create --help
```

This guide covers the engine commands, the service and the worker. The everyday
verbs (`add`, `ready`, `done` and the rest) are in the [agent guide](agents.md).
A path-owned database runs in SQLite WAL mode, so `-wal` and `-shm` files
appear beside it; back up with `daymark backup`, not by copying the main file.

The engine write commands require a stable `--operation-id`; updates and actions require
`--expected-version` from the last read. Creation uses expected version zero.
Reuse an operation ID only for an identical retry. Keep JSON payloads in protected
files rather than shell command arguments. Unknown entity fields are rejected.

```sh
printf '%s\n' '{"id":"task-example","kind":"task","title":"Synthetic check"}' > task.json
daymark --db example.sqlite3 --workspace example create --operation-id create-example --file task.json
daymark --db example.sqlite3 --workspace example get task-example
daymark --db example.sqlite3 --workspace example export --output snapshot.json
```

## Optional API

Use `daymark-core[service]` for a standalone installation. The default bind address
is `127.0.0.1`. Provide a protected JSON authentication file mapping high-entropy
bearer tokens to workspace and principal IDs. Generate tokens locally; do not
commit them. This example shows the format, not a usable credential:

```json
{"REPLACE_WITH_A_RANDOM_TOKEN":{"workspace":"example","principal":"operator"}}
```

```sh
chmod 600 auth.json
daymark --db example.sqlite3 serve --auth-file auth.json
```

Every route, including `/v1/schema` and `/v1/health`, requires bearer authentication.
HTTP outbox health is scoped to the token's workspace; shared scheduler health
contains only infrastructure timestamps/counts. Trusted embedded callers and
local CLI may request global operational health.
`POST /v1/operations` accepts `operation_id`, `action`, `payload`, and
`expected_version`. `POST /v1/query` accepts `filters`. Reads are
`GET /v1/entities`, `/v1/entities/{id}`, `/v1/events?after=0&limit=100`,
`/v1/export`, and the derived views `/v1/ready`, `/v1/agenda`, `/v1/summary`
and `/v1/context/{id}`. Projects read as plans: `GET /v1/projects` (each with its
roll-up), `/v1/projects/{id}/plan` (stages, tasks in manual order, roll-up and the
head cursor, in one read), and `/v1/projects/{id}/changes?after=N`
(`{"cursor", "changed"}`, cheap enough to poll). `GET /v1/events/head` gives the
latest cursor and `/v1/events?project=ID` keeps the events that touch one project.
`POST /v1/operations/batch` takes `operations` (up to 100, each shaped like a
single operation) and `atomic` (default true: all or nothing); it answers 200 with
`committed` and one result per operation, so a refusal is a result, not an HTTP
error. `POST /v1/import` accepts `snapshot`, `operation_id`, and
`expected_version` (use zero as the validated merge precondition). Import must
reject conflicting existing identities rather than rewind newer state. Schema
operation IDs remain stable within v1. Each write is recorded with the token's
principal as its actor; for `claim`, `release` and `note` the principal also
replaces any owner or author in the request. If the workspace names deciders
(`daymark --db example.sqlite3 --workspace example policy --deciders operator`),
`accept` and `dismiss` from any other principal return 403.
The server resolves the workspace from its token configuration. Do not expose
this bearer-only service beyond a trusted local boundary without TLS termination
and your own access policy. CORS is disabled. Request bodies are limited to 1 MiB;
embedded apps may set a smaller limit with `create_app`.

Integration uses the same engine: `create_app(store, tokens)` accepts an existing
Store. `create_router(store)` is available for an application's own authentication
and bounded-body middleware: set `request.state.daymark_identity` to a trusted
`Identity(workspace, principal)`. Never derive that identity from request payloads.
The caller retains Store lifetime/transaction ownership. Synchronous HTTP routes
run in a thread pool: use `Store(path)` for a standalone server. When passing a
caller-owned SQLite connection, configure and serialize it for that execution
context (including SQLite's `check_same_thread` policy); do not share an active
application transaction across unrelated requests. The synchronous Python
`daymark.client.Client` accepts a base URL and bearer token, and supports context
manager cleanup. The CLI uses `--url` and a credential from `DAYMARK_TOKEN` (or
`--token-env`), avoiding credentials in command arguments.

## Recorder scheduler

```sh
daymark --db example.sqlite3 worker --once
daymark --db example.sqlite3 worker --interval 10 --lease-seconds 60
daymark --db example.sqlite3 health
```

The standalone worker uses a path-owned `Store`, which commits its claim before
calling the sink. A caller-owned embedded SQLite connection never commits inside
Daymark. Passing that embedded Store to the convenience worker does not establish
a committed delivery boundary: an application transaction may still roll back
after the sink acts. Applications with custom external sinks must own the
transaction sequence: commit the claim/outbox intent, invoke the sink with a
durable receiver deduplication key, then persist the fenced result in another
committed transaction. The convenience worker has no application-commit hook.
A custom sink alone is not production qualification; these examples prove only
recording behavior.

Each pass persists tick and heartbeat, claims due reminders under a lease, calls
the recording sink with a stable delivery key, and persists success or retry.
Each claim batch uses a fresh lease-owner token, fencing late outcomes from an
older batch even when the same worker process recovers its expired work.
Immediately before each sink call, the worker re-reads the reminder and checks
its current version, owner, delivery key, lease expiry, and associated task state.
Cancellation committed before that final check suppresses the call. That check
is the dispatch authorization point: cancellation after it may race with the sink
and cannot undo an external effect. Lease fencing still prevents a cancelled or
stale claim from being recorded as a successful delivery. The boundary tests use
barriers for both sides of this race rather than timing-dependent sleeps.

The default sink accepts only the `recording` channel and sends no notifications.
An unsupported channel records a failed attempt, never a delivery success. Its in-memory key receipts are diagnostic;
the database contains durable delivery outcomes. A crash after a sink call but
before recording success can cause retry. The crash regression terminates a
worker subprocess after a separate synthetic receiver commits a delivery-key
receipt, then starts a new process after lease expiry. It verifies two attempts
with one stable key and one receiver effect; it does not demonstrate arbitrary
provider deduplication or production delivery. Custom sinks must persistently
deduplicate delivery keys. Keep batch execution below the lease duration;
leases are not renewed while a sink blocks. A successful recording pass does
not demonstrate production notification delivery.

`deploy/daymark-recorder.service` is a generic user-systemd example. Review paths
and the service's installed executable location for your environment, copy it
into your user unit directory, then enable it using `systemctl --user`.
The service runs the recorder only and exposes no HTTP listener.

## Backup and restore drill

```sh
daymark --db example.sqlite3 backup --file example-backup.sqlite3
daymark --db restored.sqlite3 restore --file example-backup.sqlite3
daymark --db restored.sqlite3 --workspace example list
daymark --db restored.sqlite3 --workspace example export --output restored.json
```

Backup uses SQLite's online backup API, including committed WAL data, and checks
source integrity. Restore writes a new database and refuses to overwrite an
existing path. Stop workers and writers before switching the active database;
restoring delivered records can otherwise replay already performed external
side effects. Keep backups and exports private (new files use mode 0600).
Logical snapshot import also validates entity references and preserves history;
including prior operation hashes/results so identical write retries remain
replays after restore. Conflicting duplicate event/operation identities must be
rejected atomically. This is distinct from a complete SQLite backup. Compare synthetic snapshot readback
before considering a restore verified. Do not use real household data in examples.

## Boundary review rationale

The [implementation review](implementation-review.md) records pinned upstream
source observations and independently derived regression designs. Daymark's own
barrier, subprocess, transaction, and restore tests establish its behavior; static
upstream inspection and upstream test assertions are not Daymark pass results.
No additional scheduler or task engine is required for the core.
