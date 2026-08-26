from __future__ import annotations

import asyncio
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.camera_alerts import CameraHealthAlerts  # noqa: E402
from app.camera_settings import CameraSettingsStore  # noqa: E402
from app.config import Config  # noqa: E402
from app.database import Database  # noqa: E402
from app.notifications import PushResult  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class FakeCamera:
    def __init__(self):
        self.online = False
        self.error = "连接失败"

    def snapshot(self):
        return type("Snapshot", (), {"online": self.online, "error": self.error})()


class FakeNotifier:
    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    async def send(self, title: str, body: str, **kwargs):
        self.messages.append((title, body))
        return PushResult(True, "ok")


def test_camera_secrets_are_persisted_but_never_returned(tmp_path: Path) -> None:
    path = tmp_path / "camera-settings.json"
    store = CameraSettingsStore(path)
    base = Config(camera_password="old-secret", camera_cookie="old-cookie")
    candidate = store.merge(
        base,
        {"stream_url": "http://camera.local/stream", "username": "viewer", "password": "new-secret", "auth_type": "digest", "cookie": None, "verify_tls": True},
    )
    store.save(candidate)
    loaded = store.load(Config())
    public = store.public(loaded, online=True, error="")

    assert loaded.camera_password == "new-secret"
    assert loaded.camera_cookie == "old-cookie"
    assert public["password_configured"] is True
    assert "password" not in public and "cookie" not in public
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_offline_and_recovery_notifications(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "alerts.db")
        db.initialize()
        db.update_settings({"bark_enabled": True, "camera_offline_alert_enabled": True, "camera_offline_minutes": 1, "camera_recovery_alert_enabled": True})
        camera = FakeCamera()
        notifier = FakeNotifier()
        alerts = CameraHealthAlerts(db, camera, notifier)  # type: ignore[arg-type]
        start = datetime(2026, 8, 26, 1, 0, tzinfo=timezone.utc)

        await alerts.check(start)
        assert notifier.messages == []
        await alerts.check(start + timedelta(seconds=61))
        assert notifier.messages[0][0] == "摄像头离线提醒"
        camera.online = True
        camera.error = ""
        await alerts.check(start + timedelta(seconds=70))
        assert notifier.messages[1][0] == "摄像头已恢复在线"

    asyncio.run(scenario())


def test_camera_api_only_switches_after_validation(tmp_path: Path, monkeypatch) -> None:
    import app.main as main

    class ReconfigurableCamera:
        def __init__(self):
            self.config = Config(camera_stream_url="http://old-camera/stream")
            self.switched_to = None

        def snapshot(self):
            return type("Snapshot", (), {"online": True, "error": ""})()

        def reconfigure(self, config):
            self.config = config
            self.switched_to = config.camera_stream_url

    camera = ReconfigurableCamera()
    main.app.state.db = object()
    main.app.state.monitor = type("Monitor", (), {"camera": camera})()
    main.app.state.camera_store = CameraSettingsStore(tmp_path / "camera.json")
    main.app.state.camera_config_lock = asyncio.Lock()
    client = TestClient(main.app)
    payload = {"stream_url": "http://new-camera/stream", "username": "viewer", "password": "secret", "auth_type": "basic", "cookie": None, "verify_tls": True}

    monkeypatch.setattr(main, "validate_camera_connection", lambda config, timeout: (False, "无有效画面"))
    failed = client.put("/api/camera/settings", json=payload)
    assert failed.status_code == 422
    assert camera.switched_to is None
    assert not (tmp_path / "camera.json").exists()

    monkeypatch.setattr(main, "validate_camera_connection", lambda config, timeout: (True, "ok"))
    success = client.put("/api/camera/settings", json=payload)
    assert success.status_code == 200
    assert camera.switched_to == "http://new-camera/stream"
    assert "password" not in success.json()
