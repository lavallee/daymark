# Daymark

Daymark gives applications a shared vocabulary for tasks, plans, and time. Keep a hard deadline separate from an intention, track structured work in SQLite, and retain control of your application's transactions.

The Python import is `daymark`; the distribution is `daymark-core`. Version 0.1.0 is the initial release. Package registry publication is not yet promised.

## Install from source

Requires Python 3.11 or newer.

```sh
git clone https://github.com/lavallee/daymark.git
cd daymark
python -m venv .venv
. .venv/bin/activate
python -m pip install .
```

The core needs no HTTP framework, credentials, notification provider, or network service. Install optional HTTP dependencies with `python -m pip install '.[service]'`.

## Quickstart

This example uses synthetic records and an in-memory database:

```python
from daymark import Store

store = Store(":memory:")
payload = {
    "id": "draft-proposal",
    "kind": "task",
    "title": "Draft a proposal",
    "planned": {"date": "2030-04-03", "timezone": "Europe/London"},
}
task = store.execute("demo", "capture-proposal", "create", payload, expected_version=0)

# Identical retries return the original result without another write.
assert store.execute(
    "demo", "capture-proposal", "create", payload, expected_version=0
) == task

updated = store.execute(
    "demo", "rename-proposal", "update",
    {"id": task["id"], "title": "Draft the revised proposal"},
    expected_version=task["version"],
)
assert store.get("demo", task["id"])["version"] == updated["version"]
assert updated["deadline"] is None  # A planned day is not a hard deadline.
store.close()
```

Use a database path instead of `":memory:"` for persistence. A path-owned Store commits its successful operations; call `close()` when finished. Reads return dictionaries with JSON-compatible values.

Each write needs an operation ID and an expected version. Use version `0` for creation and the last read version for updates. Reuse an operation ID only for an identical retry; a changed payload or expected version conflicts. Catch `daymark.DomainError` for domain failures; model validation can also raise `ValueError`.

## Embed in your transaction

Pass an existing `sqlite3.Connection` when task changes must share an application transaction:

```python
import sqlite3
from daymark import Store

connection = sqlite3.connect(":memory:")
store = Store(connection)
connection.commit()  # Caller explicitly persists initial schema setup.

with connection:  # Caller commits on success and rolls back on exception.
    store.execute(
        "demo", "capture-review", "create",
        {"id": "review", "kind": "task", "title": "Review the proposal"},
        expected_version=0,
    )

store.close()  # A caller-owned connection stays open.
connection.close()
```

Store initialization and writes participate in the supplied connection's transaction. The library uses savepoints and does not commit that connection, even when there was no active transaction. Persist schema initialization before starting a separate unit of work; rolling back initial setup also rolls back the new tables. Keep the connection's threading rules under your application's control. Only `daymark_` tables belong to the library.

## Timing and structured work

Entities have explicit kinds, lifecycle state, version, links, source references, and extensible JSON metadata. `deadline`, `planned`, `earliest_start`, and `latest_target` express different constraints. A temporal value contains either a local `date` or an offset-aware `at`, together with an IANA `timezone`; floating instants are rejected.

Recurrence models distinguish fixed schedules from completion-based intervals. Skipping a recurring occurrence advances its series without recording an actual completion: that occurrence is cancelled with no completion timestamp. Deleting a task cancels its reminders and detaches their live task references; event history retains the original links. Other inbound entity references block deletion atomically and must be resolved explicitly first. Read the [v1 decisions](https://github.com/lavallee/daymark/blob/main/docs/decisions.md) and [product comparison](https://github.com/lavallee/daymark/blob/main/docs/comparison.md) for chosen scope, deferred behavior, and design context.

## Standalone surfaces and status

The optional API, Python HTTP client, CLI, and recording worker use the same Store engine. The default recording sink sends no notifications. External sinks must durably deduplicate delivery keys; an external side effect cannot be guaranteed exactly once.

External delivery requires a committed claim or outbox record before the side effect. A worker using a caller-owned Store cannot commit that transaction; the embedding application must design and own those transaction boundaries. Supplying a custom sink alone does not qualify production delivery.

Read the [operations guide](https://github.com/lavallee/daymark/blob/main/docs/operations.md) for configuration and synthetic command examples. Check [CHANGELOG.md](CHANGELOG.md) before upgrading; stored schema and public behavior may change during 0.x.

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and the maintainer [release checklist](RELEASING.md).

## License

[MIT](LICENSE).
