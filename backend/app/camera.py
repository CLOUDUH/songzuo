from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from .config import Config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CameraSnapshot:
    jpeg: bytes | None
    captured_at: datetime | None
    online: bool
    error: str


class MjpegReader:
    """持续读取 MJPEG 字节流，只保留内存中的最新 JPEG，不写入磁盘。"""

    def __init__(self, config: Config):
        self.config = config
        self._lock = threading.Lock()
        self._jpeg: bytes | None = None
        self._captured_at: datetime | None = None
        self._online = False
        self._error = "尚未连接"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="mjpeg-reader", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=8)

    def reconfigure(self, config: Config) -> None:
        """在验证成功后安全切换配置；监测任务会自动等待新画面。"""
        self.stop()
        with self._lock:
            self.config = config
            self._jpeg = None
            self._captured_at = None
            self._online = False
            self._error = "正在应用新配置"
        self.start()

    def snapshot(self) -> CameraSnapshot:
        with self._lock:
            return CameraSnapshot(self._jpeg, self._captured_at, self._online, self._error)

    def _set_status(self, online: bool, error: str = "") -> None:
        with self._lock:
            self._online = online
            self._error = error

    def _auth(self) -> httpx.Auth | None:
        if self.config.camera_auth_type == "none" or not self.config.camera_username:
            return None
        if self.config.camera_auth_type == "digest":
            return httpx.DigestAuth(self.config.camera_username, self.config.camera_password)
        return httpx.BasicAuth(self.config.camera_username, self.config.camera_password)

    def _run(self) -> None:
        backoff = 2.0
        while not self._stop.is_set():
            try:
                headers = {"Accept": "multipart/x-mixed-replace,image/jpeg,*/*", "User-Agent": "Songzuo/0.1"}
                if self.config.camera_cookie:
                    headers["Cookie"] = self.config.camera_cookie
                timeout = httpx.Timeout(connect=8, read=20, write=8, pool=8)
                with httpx.Client(auth=self._auth(), headers=headers, verify=self.config.camera_verify_tls, timeout=timeout, follow_redirects=True) as client:
                    with client.stream("GET", self.config.camera_stream_url) as response:
                        response.raise_for_status()
                        backoff = 2.0
                        buffer = bytearray()
                        found_frame = False
                        for chunk in response.iter_bytes(16_384):
                            if self._stop.is_set():
                                return
                            buffer.extend(chunk)
                            while True:
                                start = buffer.find(b"\xff\xd8")
                                if start < 0:
                                    if len(buffer) > 2_000_000:
                                        del buffer[:-2]
                                    break
                                end = buffer.find(b"\xff\xd9", start + 2)
                                if end < 0:
                                    if start > 0:
                                        del buffer[:start]
                                    break
                                jpeg = bytes(buffer[start : end + 2])
                                del buffer[: end + 2]
                                found_frame = True
                                with self._lock:
                                    self._jpeg = jpeg
                                    self._captured_at = datetime.now(timezone.utc)
                                    self._online = True
                                    self._error = ""
                        if not found_frame:
                            self._set_status(False, "已连接但未收到 MJPEG 画面，请检查流地址")
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                message = "摄像头需要登录" if code in (401, 403) else f"摄像头返回 HTTP {code}"
                self._set_status(False, message)
                logger.warning(message)
            except Exception as exc:  # noqa: BLE001 - reader must auto-recover
                self._set_status(False, f"连接失败：{type(exc).__name__}")
                logger.warning("MJPEG connection failed: %s", exc)
            if self._stop.wait(backoff):
                return
            backoff = min(backoff * 1.8, 30)


def validate_camera_connection(config: Config, timeout_seconds: float = 15) -> tuple[bool, str]:
    """建立临时连接并等待一帧；不会影响正在工作的摄像头读取器。"""
    reader = MjpegReader(config)
    reader.start()
    deadline = time.monotonic() + timeout_seconds
    last_error = "等待 MJPEG 画面超时"
    try:
        while time.monotonic() < deadline:
            snapshot = reader.snapshot()
            if snapshot.online and snapshot.jpeg:
                return True, "已收到有效 MJPEG 画面"
            if snapshot.error and snapshot.error != "尚未连接":
                last_error = snapshot.error
                if "需要登录" in last_error or "HTTP 4" in last_error:
                    break
            time.sleep(0.2)
        return False, last_error
    finally:
        reader.stop()
