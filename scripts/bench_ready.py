"""Benchmark bounded ready pages on synthetic data in a temporary SQLite file."""

import argparse
import hashlib
import json
import platform
import sqlite3
import tempfile
import time
import tracemalloc
from pathlib import Path
from statistics import median

from daymark import Store


def seed(store, count):
    for first in range(0, count, 100):
        writes = []
        for i in range(first, min(count, first + 100)):
            payload = {
                "id": f"q-{i}",
                "kind": "task",
                "title": f"Proof sample {i}",
                "scope": "proofs",
                "next_move": "person" if i % 4 == 0 else "agent",
            }
            if i and i % 5 == 0:
                payload["depends_on"] = ["q-0"]
            if i % 20 == 0:
                payload["description"] = "Synthetic proof notes. " * 700
            writes.append(
                {
                    "operation_id": f"create-{i}",
                    "action": "create",
                    "payload": payload,
                    "expected_version": 0,
                }
            )
        if not store.batch("queue", writes)["committed"]:
            raise RuntimeError("Synthetic dataset could not be created")


def measure(store, options, limit, runs):
    def read():
        return store.ready("queue", limit=limit, **options)

    expected = read()
    wall, cpu = [], []
    for _ in range(runs):
        start, process_start = time.perf_counter(), time.process_time()
        page = read()
        wall.append((time.perf_counter() - start) * 1000)
        cpu.append((time.process_time() - process_start) * 1000)
        if page != expected:
            raise RuntimeError("Result changed between warm reads")
    tracemalloc.start()
    try:
        read()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return {
        "filter": options,
        "limit": limit,
        "rows": len(expected),
        "ordered_ids_sha256": hashlib.sha256(
            json.dumps([item["id"] for item in expected]).encode()
        ).hexdigest(),
        "median_wall_ms": round(median(wall), 2),
        "median_process_cpu_ms": round(median(cpu), 2),
        "peak_python_mib": round(peak / 1024**2, 2),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=int, default=10000)
    parser.add_argument("--runs", type=int, default=5, help="Timed reads per query")
    args = parser.parse_args()
    if args.tasks < 1 or args.runs < 1:
        parser.error("--tasks and --runs must be positive")
    with tempfile.TemporaryDirectory() as directory:
        store = Store(Path(directory) / "synthetic.sqlite3")
        try:
            seed(store, args.tasks)
            results = [
                measure(store, options, limit, args.runs)
                for options in ({}, {"next_move": "agent", "scope": "proofs"}, {"scope": "missing"})
                for limit in (1, 20, 1000)
            ]
        finally:
            store.close()
    print(
        json.dumps(
            {
                "python": platform.python_version(),
                "sqlite": sqlite3.sqlite_version,
                "platform": platform.system(),
                "machine": platform.machine(),
                "tasks": args.tasks,
                "runs": args.runs,
                "results": results,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
