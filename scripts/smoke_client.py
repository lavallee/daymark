"""Read a live service from an environment containing only the installed client extra."""

import json
import sys
from importlib.util import find_spec

from daymark import Client, ClientError, Tasks

assert find_spec("fastapi") is None and find_spec("uvicorn") is None
url, token = json.load(sys.stdin)
with Client(url, token) as client:
    assert client.identity() == {"workspace": "demo", "actor": "test"}
    assert "ready" in Tasks(client).summary()
    try:
        Tasks(client).get("absent-client-smoke-record")
    except ClientError as error:
        assert error.code == "not_found" and error.status_code == 404
    else:
        raise AssertionError("The missing record did not produce a client error")
print("Installed client: authenticated read and typed error passed")
