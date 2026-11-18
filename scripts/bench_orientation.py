"""Measure compact startup against composing the same three reads in a quiet workspace."""

import argparse
import json
import platform
import sqlite3
import tempfile
import time
import tracemalloc
from contextlib import closing
from pathlib import Path
from statistics import median

from daymark import Store, Tasks
from daymark.views import memory_preview

NOW = "2030-04-01T12:00:00+00:00"


def seed(store, tasks, count):
    notes = [
        {"id": f"note-{i}", "at": NOW, "author": "reader", "text": "a" * 1024} for i in range(12)
    ]
    for first in range(0, count, 100):
        operations = []
        for i in range(first, min(first + 100, count)):
            id = f"proof-{i:05}"
            data = {
                "id": id,
                "kind": "task",
                "title": "Check sample",
                "next_move": "agent",
                "priority": i % 5,
                "notes": notes,
                "scope": "print",
            }
            if i and i % 8 == 0:
                data["depends_on"] = ["proof-00000"]
            operations.append(
                {"operation_id": id, "action": "create", "payload": data, "expected_version": 0}
            )
        if not store.batch("sample", operations)["committed"]:
            raise RuntimeError("Synthetic fixture creation failed")
    for i in range(12):
        tasks.add("📖" * 2000, id=f"choice-{i}", kind="memory", scope="print")


def measure(read, runs):
    read()
    elapsed, cpu = [], []
    for _ in range(runs):
        t, c = time.perf_counter_ns(), time.process_time_ns()
        read()
        cpu.append((time.process_time_ns() - c) / 1e6)
        elapsed.append((time.perf_counter_ns() - t) / 1e6)
    tracemalloc.start()
    try:
        read()
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    return {
        "median_wall_ms": round(median(elapsed), 2),
        "median_cpu_ms": round(median(cpu), 2),
        "peak_python_mib": round(peak / 2**20, 3),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=int, default=1000)
    parser.add_argument("--runs", type=int, default=7)
    args = parser.parse_args()
    if args.tasks < 1 or args.runs < 1:
        parser.error("tasks and runs must be positive")
    with tempfile.TemporaryDirectory() as tmp, closing(Store(Path(tmp) / "proofs.db")) as store:
        tasks = Tasks(store, "sample", actor="reader", timezone="UTC", now=NOW)
        seed(store, tasks, args.tasks)

        def separate():
            memories = tasks.search(kind="memory", limit=5)
            return {
                "summary": tasks.summary(),
                "ready": tasks.ready_summary(limit=5),
                "memories": [
                    memory_preview(m["id"], m["title"], m.get("scope")) for m in memories["items"]
                ],
                "next_memory": memories["next"],
            }

        combined = tasks.orientation()
        del combined["activity_cursor"], combined["activity_checkpoint"], combined["as_of"]
        if combined != separate():
            raise RuntimeError("Snapshot and separate reads disagree in a quiet workspace")
        result = {
            "tasks": args.tasks,
            "notes_per_task": 12,
            "note_characters": 1024,
            "runs": args.runs,
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
            "platform": platform.system(),
            "machine": platform.machine(),
            "separate": measure(separate, args.runs),
            "snapshot": measure(tasks.orientation, args.runs),
        }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
