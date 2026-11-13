"""Rebuild daymark-0.3.0.sql: a synthetic database written by Daymark 0.3.0, dumped as SQL.

Run with the 0.3.0 source tree (``git worktree add /tmp/dm030 v0.3.0``):

    python -I tests/fixtures/make_fixture_0_3_0.py /tmp/dm030/src tests/fixtures/daymark-0.3.0.sql

Not collected by pytest. Every record is synthetic.
"""

import sqlite3
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

source, target = sys.argv[1], sys.argv[2]
sys.path.insert(0, source)

import daymark
from daymark import Store, Tasks

assert daymark.__version__ == "0.3.0", daymark.__version__
assert daymark.__file__.startswith(str(Path(source).resolve())), "wrong daymark imported"
NOW = datetime(2030, 4, 3, 15, 0, tzinfo=UTC)
with tempfile.TemporaryDirectory() as d:
    path = Path(d) / "old.db"
    store = Store(str(path))
    t = Tasks(store, "home", actor="alice", timezone="Europe/London", now=NOW)
    t.add("Sam", kind="person", id="sam")
    t.add("Garden", kind="project", id="garden")
    t.add("Buy seeds", id="seeds", project="garden", due="2030-04-10", priority=2, assignee="sam")
    t.add(
        "Plant seeds",
        id="plant",
        project="garden",
        after="seeds",
        plan="2030-04-12",
        target="2030-04-20",
    )
    t.add("Water the beds", id="water", project="garden", every="3 days", after_completion=True)
    t.done("water")
    t.add("Rake leaves", id="rake", project="garden", note="Use the green rake")
    t.cancel("rake")
    t.propose("Build a compost bin", id="compost", project="garden")
    t.add("Renew passport", id="passport", due="2030-05-03", priority=1)
    t.wait("passport", "the photo booth")
    t.focus("plant")
    t.remember("Prefers mornings")
    t.remind("seeds", "2030-04-09 08:00", recipient="sam")
    s2 = Tasks(store, "studio", actor="bob", timezone="UTC", now=NOW)
    s2.add("Garden", kind="project", id="garden")
    s2.add("Sketch", id="sketch", project="garden")
    store.close()
    db = sqlite3.connect(path)
    Path(target).write_text("\n".join(db.iterdump()) + "\n")
    db.close()
print("ok")
