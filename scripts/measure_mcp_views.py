"""Measure the bytes a client receives for one synthetic ready queue.

Run from the source checkout with `uv run python scripts/measure_mcp_views.py`.
Pass --tokens with tiktoken installed to count tokens in the same JSON texts.
"""

from __future__ import annotations

import argparse
import json
from contextlib import closing
from unittest.mock import patch

from daymark import Store, Tasks
from daymark.mcp import handle
from daymark.models import normalize_now


def encode(value) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, sort_keys=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tokens", action="store_true", help="Also count using o200k_base")
    args = parser.parse_args()
    tokenizer = None
    if args.tokens:
        import tiktoken

        tokenizer = tiktoken.get_encoding("o200k_base")
    instant = normalize_now("2030-05-05T12:00:00Z")
    # The store owns commit timestamps. Fix its clock and operation IDs so
    # token boundaries in timestamps and note IDs are reproducible.
    with (
        patch("daymark.store.normalize_now", lambda value=None: normalize_now(value or instant)),
        closing(Store(":memory:")) as store,
    ):
        tasks = Tasks(
            store, "sample", actor="inspector", timezone="UTC", now="2030-05-05T12:00:00Z"
        )
        for number in range(12):
            id = f"sample-{number:02}"
            tasks.add(
                f"Inspect sample {number:02}",
                id=id,
                op=f"capture-{number}",
                scope="workshop",
                move="agent",
                description="Compare the readings against the reference sheet. Record the discrepancy.",
            )
            for reading in range(3):
                tasks.note(
                    id,
                    "\n".join(
                        f"Checkpoint {step:02}, sample {number:02}, reading {reading}: "
                        "the reference mark is legible. Check the next segment before signing."
                        for step in range(8)
                    ),
                    op=f"reading-{number}-{reading}",
                )

        def read(*, structured=False, protocol="2025-11-25"):
            fields = {"view": "ready", "scope": "workshop", "move": "agent", "limit": 12}
            if structured:
                fields["json"] = True
            message = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "daymark_view", "arguments": fields},
            }
            return handle(tasks, message, protocol=protocol)["result"]

        structured = read(structured=True)
        rows = {
            "default_text": read(),
            "structured_queue": structured,
            "legacy_json_text": read(structured=True, protocol="2024-11-05"),
            # Counterfactual for measuring the cost of sending full records in the same envelope.
            "unprojected_queue": {
                **structured,
                "structuredContent": {"items": tasks.ready(limit=12)},
            },
        }
        assert len(structured["structuredContent"]["items"]) == 12
        print("format\tutf8_bytes" + ("\to200k_base_tokens" if tokenizer else ""))
        for name, value in rows.items():
            text = encode(value)
            fields = [name, str(len(text.encode("utf-8")))]
            if tokenizer:
                fields.append(str(len(tokenizer.encode(text))))
            print("\t".join(fields))


if __name__ == "__main__":
    main()
