"""The load harness pins each virtual user to one replica. No sockets are opened here."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "load_test.py"
_SPEC = importlib.util.spec_from_file_location("procureai_load_test", _PATH)
assert _SPEC and _SPEC.loader
load_test = importlib.util.module_from_spec(_SPEC)
sys.modules["procureai_load_test"] = load_test
_SPEC.loader.exec_module(load_test)


def test_replicas_round_robin_and_derive_their_own_websocket():
    urls = load_test.replica_urls("http://localhost:8000", "http://a:8000/, http://b:8000")
    assert urls == ["http://a:8000", "http://b:8000"]
    assert load_test.replica_for(0, urls) == "http://a:8000"
    assert load_test.replica_for(1, urls) == "http://b:8000"
    assert load_test.replica_for(2, urls) == "http://a:8000"
    assert load_test.ws_base_for("https://a:8000") == "wss://a:8000"
    assert load_test.ws_base_for("http://b:8000") == "ws://b:8000"


def test_a_single_base_url_is_the_default():
    assert load_test.replica_urls("http://localhost:8000/", "") == ["http://localhost:8000"]
