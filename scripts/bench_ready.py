"""Benchmark ready pages or brief advice on synthetic data in a temporary SQLite file."""

import argparse
import hashlib
import json
import platform
import sqlite3
import tempfile
import time
import tracemalloc
from datetime import UTC, datetime, timedelta
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


def timings(read, runs):
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
    return expected, {
        "median_wall_ms": round(median(wall), 2),
        "median_process_cpu_ms": round(median(cpu), 2),
        "peak_python_mib": round(peak / 1024**2, 2),
    }


def measure(store, options, limit, runs):
    expected, metrics = timings(lambda: store.ready("queue", limit=limit, **options), runs)
    return {
        "filter": options,
        "limit": limit,
        "rows": len(expected),
        "ordered_ids_sha256": hashlib.sha256(
            json.dumps([item["id"] for item in expected]).encode()
        ).hexdigest(),
        **metrics,
    }


def advice_cases(store, count, runs):
    from daymark import Tasks
    from daymark.render import list_record

    now = datetime(2030, 5, 1, tzinfo=UTC)
    # Native imports preserve these synthetic creation and update times. Age
    # checks and date ordering can then be compared across independent runs.
    description, note = "Historical details. " * 3000, "A past observation. " * 400
    for first in range(0, count, 40):
        entities = []
        for i in range(first, min(first + 40, count)):
            stamp = (now - timedelta(days=45) + timedelta(microseconds=i)).isoformat()
            entities.append(
                {
                    "workspace": "press",
                    "id": f"proof-{i:03}",
                    "kind": "task",
                    "title": f"Check proof sheet {i}",
                    "description": description,
                    "notes": [{"id": "note", "text": note, "at": stamp, "author": "reader"}],
                    "created_at": stamp,
                    "updated_at": stamp,
                    "estimate_minutes": 5 + i % 13 * 5 if i % 10 else None,
                    "next_move": "answer" if i % 3 == 0 else None,
                    "deadline": {"date": "2030-05-01", "timezone": "UTC"} if i % 17 == 0 else None,
                }
            )
        store.import_snapshot(
            "press",
            {"schema_version": 1, "entities": entities},
            operation_id=f"load-{first}",
            expected_version=0,
        )
    tasks = Tasks(store, "press", now=now, timezone="UTC")
    results = []
    for name, settings in (
        ("plain", []),
        ("plain", ["shortlist.size=2"]),
        ("plain", ["time-fit.minutes=15", "shortlist.size=2"]),
        ("gentle", []),
        ("dense", []),
        ("assistant", []),
    ):

        def read_full(name=name, settings=settings):
            advice = tasks.advise(profile=name, settings=settings)
            advice.picks = [list_record(item, now, line_fields=True) for item in advice.picks]
            return advice.to_dict()

        full, baseline = timings(read_full, runs)
        brief, projection = timings(
            lambda name=name, settings=settings: tasks.advise(
                profile=name, settings=settings, brief=True
            ).to_dict(),
            runs,
        )
        if full != brief:
            raise RuntimeError(f"Brief advice changed for {name} with {settings}")
        body = json.dumps(brief, sort_keys=True).encode()
        results.append(
            {
                "profile": name,
                "settings": settings,
                "picks": len(brief["picks"]),
                "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
                "full_then_compact": baseline,
                "brief": projection,
            }
        )
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--advice",
        action="store_true",
        help="Compare brief advice with full advice compacted after evaluation",
    )
    parser.add_argument(
        "--tasks", type=int, help="Number of synthetic tasks (400 for advice, otherwise 10000)"
    )
    parser.add_argument("--runs", type=int, default=5, help="Timed reads per query")
    args = parser.parse_args()
    if args.tasks is None:
        args.tasks = 400 if args.advice else 10000
    if args.tasks < 1 or args.runs < 1:
        parser.error("--tasks and --runs must be positive")
    with tempfile.TemporaryDirectory() as directory:
        store = Store(Path(directory) / "synthetic.sqlite3")
        try:
            if args.advice:
                results = advice_cases(store, args.tasks, args.runs)
            else:
                seed(store, args.tasks)
                results = [
                    measure(store, options, limit, args.runs)
                    for options in (
                        {},
                        {"next_move": "agent", "scope": "proofs"},
                        {"scope": "missing"},
                    )
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
