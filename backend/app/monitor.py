from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import cv2
import numpy as np

from .camera import MjpegReader
from .database import Database
from .detector import Detection, HogPersonDetector, HybridPersonDetector, YuNetSeatDetector, YoloPersonDetector
from .notifications import Notifier
from .stats import format_duration, parse_datetime

logger = logging.getLogger(__name__)


def render_message(template: str, values: dict[str, str]) -> str:
    result = template
    for key, value in values.items():
        result = result.replace(f"{{{key}}}", value)
    return result


class Monitor:
    def __init__(self, db: Database, camera: MjpegReader, detector: HybridPersonDetector | HogPersonDetector | YuNetSeatDetector | YoloPersonDetector, notifier: Notifier):
        self.db, self.camera, self.detector, self.notifier = db, camera, detector, notifier
        active = db.active_session()
        self.session_id: int | None = int(active["id"]) if active else None
        self.session_started_at: datetime | None = parse_datetime(active["started_at"]) if active else None
        self.away_seconds = int(active.get("away_seconds", 0)) if active else 0
        self.reminder_count = int(active.get("reminder_count", 0)) if active else 0
        self.last_reminder_elapsed_seconds = int(active.get("last_reminder_elapsed_seconds", 0)) if active else 0
        self.occupied = bool(active)
        self.present_candidate_since: datetime | None = None
        self.absent_since: datetime | None = None
        self.last_check: datetime | None = None
        self.confidence = 0.0
        self.last_boxes: tuple[tuple[int, int, int, int], ...] = ()
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
                    roi = {key: float(settings[key]) for key in ("roi_x", "roi_y", "roi_w", "roi_h", "face_confidence_threshold", "min_face_width_ratio")}
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

    def seated_elapsed(self, at: datetime) -> int:
        if not self.session_started_at:
            return 0
        seconds = int((at - self.session_started_at).total_seconds()) - self.away_seconds
        if self.absent_since and at > self.absent_since:
            seconds -= int((at - self.absent_since).total_seconds())
        return max(0, seconds)

    def _clear_session(self) -> None:
        self.session_id = None
        self.session_started_at = None
        self.away_seconds = 0
        self.occupied = False
        self.absent_since = None
        self.present_candidate_since = None
        self.reminder_count = 0
        self.last_reminder_elapsed_seconds = 0

    def _end_current_session(self, ended_at: datetime, settings: dict) -> None:
        if self.session_id is None:
            return
        elapsed = self.seated_elapsed(ended_at)
        self.db.end_session(self.session_id, ended_at, elapsed >= int(settings["sedentary_minutes"]) * 60)
        self._clear_session()

    async def _update_and_maybe_remind(self, now: datetime, settings: dict, confidence: float) -> None:
        if self.session_id is None:
            return
        elapsed = self.seated_elapsed(now)
        sedentary = elapsed >= int(settings["sedentary_minutes"]) * 60
        self.db.update_active(self.session_id, now, confidence, sedentary)
        channels_enabled = bool(settings["bark_enabled"] or settings["webhook_enabled"])
        first_due = sedentary and self.reminder_count == 0
        repeat_interval = int(settings["repeat_reminder_minutes"]) * 60
        repeat_due = (
            sedentary
            and self.reminder_count > 0
            and bool(settings["repeat_reminder_enabled"])
            and elapsed - self.last_reminder_elapsed_seconds >= repeat_interval
        )
        if channels_enabled and (first_due or repeat_due):
            repeat_count = max(0, self.reminder_count)
            values = {
                "duration": format_duration(elapsed),
                "repeat_count": str(repeat_count),
                "interval": format_duration(repeat_interval),
            }
            title_key = "repeat_reminder_title" if repeat_due else "reminder_title"
            body_key = "repeat_reminder_body" if repeat_due else "reminder_body"
            self.reminder_count += 1
            self.last_reminder_elapsed_seconds = elapsed
            self.db.mark_reminder(self.session_id, now, elapsed)
            result = await self.notifier.send(
                render_message(str(settings[title_key]), values),
                render_message(str(settings[body_key]), values),
                group="久坐提醒",
                use_bark=settings["bark_enabled"],
                use_webhook=settings["webhook_enabled"],
            )
            self.db.log_notification(
                "sedentary",
                f"{self.session_id}:{self.reminder_count}",
                now,
                result.success,
                result.detail,
            )

    async def process(self, detection: Detection, now: datetime, settings: dict) -> None:
        self.last_check = now
        self.confidence = detection.confidence
        self.last_boxes = detection.boxes
        present_confirm = max(2, int(settings["sample_interval_seconds"]) * 2)
        leave_confirm = int(settings["leave_confirm_seconds"])
        merge_gap = int(settings["merge_gap_seconds"])

        if detection.present:
            if self.session_id is not None and self.absent_since is not None:
                gap = int((now - self.absent_since).total_seconds())
                if gap >= merge_gap:
                    old_absence = self.absent_since
                    self._end_current_session(old_absence, settings)
                elif self.occupied:
                    # 少量漏帧，不记为真实离座。
                    self.absent_since = None
                    self.present_candidate_since = None
                    await self._update_and_maybe_remind(now, settings, detection.confidence)
                    return
                else:
                    self.present_candidate_since = self.present_candidate_since or now
                    if (now - self.present_candidate_since).total_seconds() >= present_confirm:
                        break_end = self.present_candidate_since
                        assert self.session_id is not None
                        self.away_seconds += self.db.add_break(self.session_id, self.absent_since, break_end)
                        self.absent_since = None
                        self.present_candidate_since = None
                        self.occupied = True
                        await self._update_and_maybe_remind(now, settings, detection.confidence)
                    return

            if self.session_id is None:
                self.present_candidate_since = self.present_candidate_since or now
                if (now - self.present_candidate_since).total_seconds() < present_confirm:
                    return
                self.session_started_at = self.present_candidate_since
                self.session_id = self.db.start_session(self.session_started_at)
                self.away_seconds = 0
                self.occupied = True
                self.reminder_count = 0
                self.last_reminder_elapsed_seconds = 0
                self.present_candidate_since = None

            self.absent_since = None
            self.occupied = True
            await self._update_and_maybe_remind(now, settings, detection.confidence)
            return

        self.present_candidate_since = None
        if self.session_id is None:
            self.occupied = False
            return

        self.absent_since = self.absent_since or now
        gap = int((now - self.absent_since).total_seconds())
        if self.occupied and gap >= leave_confirm:
            self.occupied = False
            # 计时时长回退到第一次确认无人，离座期间不计入坐姿。
            elapsed = self.seated_elapsed(self.absent_since)
            self.db.update_active(
                self.session_id,
                self.absent_since,
                detection.confidence,
                elapsed >= int(settings["sedentary_minutes"]) * 60,
            )
        if gap >= merge_gap:
            ended_at = self.absent_since
            self._end_current_session(ended_at, settings)

    def status(self, threshold_minutes: int) -> dict:
        now = datetime.now(timezone.utc)
        snapshot = self.camera.snapshot()
        effective_now = now if snapshot.online or not self.last_check else self.last_check
        elapsed = self.seated_elapsed(effective_now)
        settings = self.db.settings()
        if self.session_id is None:
            remaining = None
        elif self.reminder_count > 0 and settings["repeat_reminder_enabled"]:
            remaining = max(0, int(settings["repeat_reminder_minutes"]) * 60 - (elapsed - self.last_reminder_elapsed_seconds))
        elif self.reminder_count > 0:
            remaining = None
        else:
            remaining = max(0, threshold_minutes * 60 - elapsed)
        return {
            "occupied": self.occupied,
            "camera_online": snapshot.online,
            "camera_error": snapshot.error,
            "detector": self.detector.name,
            "session_started_at": self.session_started_at.isoformat() if self.session_started_at else None,
            "session_duration_seconds": elapsed,
            "pending_resume": self.session_id is not None and not self.occupied,
            "confidence": self.confidence,
            "detection_boxes": [list(box) for box in self.last_boxes],
            "last_check": self.last_check.isoformat() if self.last_check else None,
            "next_reminder_seconds": remaining,
        }
