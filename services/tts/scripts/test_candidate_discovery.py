#!/usr/bin/env python3
"""Fast HTTP contract test for candidate TTS discovery metadata."""
from __future__ import annotations

import importlib.util
import json
import os
import threading
import urllib.request
from pathlib import Path


SCRIPT = Path(__file__).with_name("candidate_server.py")


def load_candidate_server(alias: str):
    old = os.environ.get("TTS_MODEL_ALIAS")
    os.environ["TTS_MODEL_ALIAS"] = alias
    try:
        spec = importlib.util.spec_from_file_location("candidate_server_discovery_test", SCRIPT)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"could not load {SCRIPT}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if old is None:
            os.environ.pop("TTS_MODEL_ALIAS", None)
        else:
            os.environ["TTS_MODEL_ALIAS"] = old


def fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as response:
        assert response.status == 200
        assert response.headers.get_content_type() == "application/json"
        return json.load(response)


def main() -> None:
    alias = "chatterbox-flash"
    module = load_candidate_server(alias)
    module.RUNTIME = object()  # Discovery endpoints do not touch the model runtime.

    server = module.ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        models = fetch_json(base + "/v1/models")
        assert models["object"] == "list"
        assert len(models["data"]) == 1
        model = models["data"][0]
        assert model["id"] == alias
        assert model["speakers"] == ["ryan"]
        assert model["default_voice"] == "ryan"

        expected_voice = {
            "id": "ryan",
            "name": "ryan",
            "model": alias,
            "object": "voice",
        }
        for path in ("/v1/audio/voices", "/v1/voices"):
            voices = fetch_json(base + path)
            assert voices["default_voice"] == "ryan"
            assert voices["voices"] == ["ryan"]
            assert voices["data"] == [expected_voice]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    print("candidate discovery HTTP contract: ok")


if __name__ == "__main__":
    main()
