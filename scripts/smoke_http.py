"""Exercise an installed service over loopback using synthetic, temporary data."""

from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx


def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        token = secrets.token_urlsafe(32)
        auth = root / "auth.json"
        auth.write_text(json.dumps({token: {"workspace": "demo", "principal": "test"}}))
        auth.chmod(0o600)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        command = [
            sys.executable,
            "-m",
            "daymark.cli",
            "--db",
            str(root / "work.db"),
            "serve",
            "--port",
            str(port),
            "--auth-file",
            str(auth),
        ]
        # The console entry point supports editable and ordinary wheel installs.
        command = [str(Path(sys.executable).parent / "daymark"), *command[3:]]
        process = subprocess.Popen(
            command,
            cwd=root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=os.environ.copy(),
        )
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}",
                timeout=2,
                headers={"Authorization": f"Bearer {token}"},
            ) as client:
                for _ in range(100):
                    if process.poll() is not None:
                        raise RuntimeError("service exited before becoming healthy")
                    try:
                        if client.get("/v1/health").status_code == 200:
                            break
                    except httpx.ConnectError:
                        pass
                    time.sleep(0.1)
                else:
                    raise RuntimeError("service did not become healthy")
                assert (
                    client.get(
                        "/v1/health", headers={"Authorization": "Bearer invalid"}
                    ).status_code
                    == 401
                )
                operation = {
                    "operation_id": "new-task",
                    "action": "create",
                    "expected_version": 0,
                    "payload": {"id": "draft", "kind": "task", "title": "Draft a proposal"},
                }
                first = client.post("/v1/operations", json=operation)
                assert first.status_code == 200, first.status_code
                assert client.post("/v1/operations", json=operation).json() == first.json()
                changed = {**operation, "payload": {**operation["payload"], "title": "Changed"}}
                assert client.post("/v1/operations", json=changed).status_code == 409
                completed = client.post(
                    "/v1/operations",
                    json={
                        "operation_id": "complete-task",
                        "action": "complete",
                        "expected_version": 1,
                        "payload": {"id": "draft"},
                    },
                )
                assert completed.status_code == 200
                assert client.get("/v1/entities/draft").json()["status"] == "completed"
                assert (
                    client.post(
                        "/v1/operations",
                        json={
                            "operation_id": "stale",
                            "action": "update",
                            "expected_version": 1,
                            "payload": {"id": "draft", "title": "Stale"},
                        },
                    ).status_code
                    == 409
                )
                assert (
                    client.post(
                        "/v1/operations",
                        json={
                            "operation_id": "unknown",
                            "action": "create",
                            "expected_version": 0,
                            "payload": {"id": "unknown", "kind": "task", "invented": True},
                        },
                    ).status_code
                    == 422
                )
                schema = client.get("/v1/schema")
                assert schema.status_code == 200
                assert client.get("/v1/export").json()["entities"]
            print(
                "Installed HTTP service: health/auth/schema/create/replay/complete/conflicts/export passed"
            )
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


if __name__ == "__main__":
    main()
