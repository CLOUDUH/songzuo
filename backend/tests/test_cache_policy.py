from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
import app.main as main  # noqa: E402


def test_api_responses_are_never_cached() -> None:
    main.app.state.db = type("DB", (), {"path": Path("/tmp/test.db")})()
    snapshot = type("Snapshot", (), {"online": True, "error": ""})()
    camera = type("Camera", (), {"snapshot": lambda self: snapshot})()
    detector = type("Detector", (), {"name": "test"})()
    main.app.state.monitor = type("Monitor", (), {"camera": camera, "detector": detector})()
    response = TestClient(main.app).get("/api/health")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    html_response = TestClient(main.app).get("/")
    assert html_response.headers["cache-control"].startswith("no-store")
