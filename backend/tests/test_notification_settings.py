from __future__ import annotations

import asyncio
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.config import Config  # noqa: E402
from app.notification_settings import NotificationSettingsStore  # noqa: E402
from app.notifications import Notifier  # noqa: E402
from app.database import Database  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def test_notification_secrets_are_saved_and_redacted(tmp_path: Path) -> None:
    store = NotificationSettingsStore(tmp_path / "notifications.json")
    candidate = store.merge(
        Config(),
        {
            "bark_server": "https://api.day.app",
            "bark_device_key": "https://api.day.app/device-key/Test",
            "generic_webhook_url": "https://hooks.example.test/songzuo",
        },
    )
    store.save(candidate)
    loaded = store.load(Config())
    public = store.public(loaded)
    assert loaded.bark_device_key == "device-key"
    assert public["bark_device_key_configured"] is True
    assert public["generic_webhook_configured"] is True
    assert "bark_device_key" not in public
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def test_bark_v2_payload(monkeypatch) -> None:
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 200, "message": "success"}

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json):
            captured.update({"url": url, "json": json})
            return Response()

    monkeypatch.setattr("app.notifications.httpx.AsyncClient", Client)
    notifier = Notifier(Config(bark_server="https://api.day.app", bark_device_key="abc123"))
    result = asyncio.run(notifier.send("标题", "内容", use_bark=True, use_webhook=False))
    assert result.success is True
    assert captured["url"] == "https://api.day.app/push"
    assert captured["json"]["device_key"] == "abc123"


def test_notification_settings_api_reconfigures_notifier(tmp_path: Path) -> None:
    import app.main as main

    db = Database(tmp_path / "api.db")
    db.initialize()
    notifier = Notifier(Config())
    main.app.state.db = db
    main.app.state.monitor = type("Monitor", (), {"notifier": notifier})()
    main.app.state.notification_store = NotificationSettingsStore(tmp_path / "notification-api.json")
    response = TestClient(main.app).put(
        "/api/notifications/settings",
        json={"bark_server": "https://api.day.app", "bark_device_key": "web-key", "generic_webhook_url": None},
    )
    assert response.status_code == 200
    assert response.json()["bark_device_key_configured"] is True
    assert notifier.config.bark_device_key == "web-key"
