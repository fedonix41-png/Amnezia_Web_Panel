import os
import sys
import json
import tempfile

import pytest

# Put the project root on sys.path so test modules can import managers.* and app.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Must be set before the app module is imported — triggers ephemeral-key
# synthesis in config.py so tests don't need real secrets.
os.environ.setdefault("AWP_DEV", "1")


@pytest.fixture
def data_file(tmp_path):
    """Point DATA_FILE at an empty, isolated data.json for each test."""
    path = tmp_path / "data.json"
    data = {
        "servers": [],
        "users": [],
        "user_connections": [],
        "api_tokens": [],
        "settings": {},
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    old = os.environ.get("DATA_FILE")
    os.environ["DATA_FILE"] = str(path)
    yield str(path)
    if old is not None:
        os.environ["DATA_FILE"] = old
    else:
        os.environ.pop("DATA_FILE", None)


@pytest.fixture
def app_client(data_file):
    """FastAPI TestClient wired to the isolated data file."""
    from fastapi.testclient import TestClient

    import app as _app  # noqa: F811  — triggers lifespan (startup)

    with TestClient(_app.app, raise_server_exceptions=False) as c:
        yield c
