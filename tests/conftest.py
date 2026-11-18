from contextlib import ExitStack

import pytest

from daymark import Store


@pytest.fixture
def open_store():
    """Close file-backed stores opened by the test, including on assertion failure."""
    with ExitStack() as cleanup:

        def open_path(path):
            store = Store(path)
            cleanup.callback(store.close)
            return store

        yield open_path
