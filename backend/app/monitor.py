from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import cv2
import numpy as np

from .camera import MjpegReader
from .database import Database
from .detector import Detection, HogPersonDetector, YoloPersonDetector
from .notifications import Notifier
from .stats import format_duration, parse_datetime

logger = logging.getLogger(__name__)


class Monitor:
    def __init__(self, db: Database, camera: MjpegReader, detector: HogPersonDetector | YoloPersonDetector, notifier: Notifier):
        self.db, self.camera, self.detector, self.notifier = db, camera, detector, notifier
        active = db.active_session()
        self.session_id: int | None = int(active["id"]) if active else None
        self.session_started_at: datetime | None = parse_datetime(active["started_at"]) if active else None
        self.reminder_attempted = bool(active and active["reminder_sent_at"])
        self.occupied = bool(active)
        self.candidate_since: datetime | None = None
        self.absent_since: datetime | None = None
        self.last_check: datetime | None = None
        self.confidence = 0.0
        self._last_frame_at: datetime | None = None
        self._stop = asyncio.Event()

    async def run(self) -> None:
        while not self._stop.is_set():
            settings = self.db.settings()
            snapshot = self.camera.snapshot()
            if snapshot.jpeg and snapshot.captured_at != self._last_frame_at:
                self._last_frame_at = snapshot.captured_at
                frame = cv2.imdecode(np.frombuffer(snapshot.jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
                if frame is not None:
                    roi = {key: float(settings[key]) for key in ("roi_x", "roi_y", "roi_w", "roi_h")}
                    try:
                        detection = await asyncio.to_thread(self.detector.detect, frame, roi)
                        await self.process(detection, datetime.now(timezone.utc), settings)
                    except Exception:  # noqa: BLE001
                        logger.exception("Person detection failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=max(1, int(settings["sample_interval_seconds"])))
            except TimeoutError:
                pass

    async def stop(self) -> None:
        self._stop.set()

    async def process(self, detection: Detection, now: datetime, settings: dict) -> None:
        self.last_check = now
        self.confidence = detection.confidence
        confirmation_seconds = max(2, int(settings["sample_interval_seconds"]) * 2)
        if detection.present:
            self.absent_since = None
            if not self.occupied:
                self.candidate_since = self.candidate_since or now
                if (now - self.candidate_since).total_seconds() >= confirmation_seconds:
                    self.session_started_at = self.candidate_since
                    self.session_id = self.db.start_session(self.session_started_at)
                    self.occupied = True
                    self.reminder_attempted = False
            if self.occupied and self.session_id and self.session_started_at:
                elapsed = int((now - self.session_started_at).total_seconds())
                sedentary = elapsed >= int(settings["sedentary_minutes"]) * 60
                self.db.update_active(self.session_id, now, detection.confidence, sedentary)
                if sedentary and not self.reminder_attempted:
                    self.reminder_attempted = True
                    self.db.mark_reminder(self.session_id, now)
                    if settings["bark_enabled"] or settings["webhook_enabled"]:
                        body = str(settings["reminder_body"]).replace("{duration}", format_duration(elapsed))
                        result = await self.notifier.send(str(settings["reminder_title"]), body, group="久坐提醒", use_bark=settings["bark_enabled"], use_webhook=settings["webhook_enabled"])
                        self.db.log_notification("sedentary", str(self.session_id), now, result.success, result.detail)
        else:
            self.candidate_since = None
            if self.occupied:
                self.absent_since = self.absent_since or now
                if (now - self.absent_since).total_seconds() >= int(settings["leave_grace_seconds"]):
                    assert self.session_id is not None and self.session_started_at is not None
                    elapsed = int((self.absent_since - self.session_started_at).total_seconds())
                    self.db.end_session(self.session_id, self.absent_since, elapsed >= int(settings["sedentary_minutes"]) * 60)
                    self.session_id = None
                    self.session_started_at = None
                    self.occupied = False
                    self.absent_since = None
                    self.reminder_attempted = False

    def status(self, threshold_minutes: int) -> dict:
        now = datetime.now(timezone.utc)
        elapsed = int((now - self.session_started_at).total_seconds()) if self.occupied and self.session_started_at else 0
        remaining = max(0, threshold_minutes * 60 - elapsed) if self.occupied else None
        snapshot = self.camera.snapshot()
        return {
            "occupied": self.occupied,
            "camera_online": snapshot.online,
            "camera_error": snapshot.error,
            "detector": self.detector.name,
            "session_started_at": self.session_started_at.isoformat() if self.session_started_at else None,
            "session_duration_seconds": elapsed,
            "confidence": self.confidence,
            "last_check": self.last_check.isoformat() if self.last_check else None,
            "next_reminder_seconds": remaining,
        }
