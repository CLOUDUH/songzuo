from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import cv2
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .camera import MjpegReader
from .camera import validate_camera_connection
from .camera_alerts import CameraHealthAlerts
from .camera_settings import CameraSettingsStore
from .config import config
from .database import Database
from .detector import create_detector
from .monitor import Monitor
from .maintenance import apply_requested_history_repairs
from .notifications import Notifier
from .notification_settings import NotificationSettingsStore
from .reports import ReportScheduler
from .schemas import CameraSettingsUpdate, NotificationSettingsUpdate, SettingsUpdate
from .stats import boundaries, day_detail, period_report, same_time_comparison, series, summarize

logging.basicConfig(level=config.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    cv2.setNumThreads(max(1, config.opencv_threads))
    db = Database(config.database_path)
    db.initialize()
    if apply_requested_history_repairs(db, config.timezone):
        logger.info("Applied requested history repair for 2026-08-28 through 2026-08-30")
    camera_store = CameraSettingsStore(config.camera_settings_path)
    runtime_config = camera_store.load(config)
    notification_store = NotificationSettingsStore(config.notification_settings_path)
    notification_config = notification_store.load(config)
    detector = create_detector(config.detector_mode, config.model_path, config.face_model_path, config.detector_input_size)
    camera = MjpegReader(runtime_config)
    notifier = Notifier(notification_config)
    monitor = Monitor(db, camera, detector, notifier)
    reports = ReportScheduler(db, notifier, config.timezone)
    camera_alerts = CameraHealthAlerts(db, camera, notifier)
    app.state.db, app.state.camera, app.state.monitor = db, camera, monitor
    app.state.camera_store = camera_store
    app.state.notification_store = notification_store
    app.state.camera_config_lock = asyncio.Lock()
    camera.start()
    tasks = [
        asyncio.create_task(monitor.run(), name="monitor"),
        asyncio.create_task(reports.run(), name="reports"),
        asyncio.create_task(camera_alerts.run(), name="camera-alerts"),
    ]
    logger.info("Service started with %s detector; frames stay in memory only", detector.name)
    try:
        yield
    finally:
        await monitor.stop()
        await reports.stop()
        await camera_alerts.stop()
        camera.stop()
        await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="松坐 API", version="0.1.0", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")


@app.middleware("http")
async def cache_policy(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/assets/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    else:
        # SPA 入口永不复用旧 HTML，避免 Chrome 在镜像更新后继续引用旧 JS。
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


def services(request: Request) -> tuple[Database, Monitor]:
    return request.app.state.db, request.app.state.monitor


@app.get("/api/health")
def health(request: Request) -> dict:
    db, monitor = services(request)
    snapshot = monitor.camera.snapshot()
    return {"status": "ok", "camera_online": snapshot.online, "camera_error": snapshot.error, "detector": monitor.detector.name, "database": str(db.path)}


@app.get("/api/overview")
def overview(request: Request, period: str = Query("week", pattern="^(day|week|month)$")) -> dict:
    db, monitor = services(request)
    now = datetime.now(timezone.utc)
    tz = ZoneInfo(config.timezone)
    spans = boundaries(now, tz)
    day_start, day_end = spans["day"]
    settings = db.settings()
    today = summarize(db, day_start, min(day_end, now), now)
    recent = db.recent_sessions(day_start, day_end, now)
    status = monitor.status(settings["sedentary_minutes"])
    break_map = db.breaks_for_sessions([int(row["id"]) for row in recent])
    for row in recent:
        if row["ended_at"] is None:
            row["duration_seconds"] = status["session_duration_seconds"]
        row["is_sedentary"] = bool(row["is_sedentary"])
        row["breaks"] = break_map.get(int(row["id"]), [])
    return {
        "status": status,
        "today": today,
        "comparison": same_time_comparison(db, now, tz),
        "threshold_minutes": settings["sedentary_minutes"],
        "sessions": recent,
        "series": series(db, period, now, tz),
    }


@app.get("/api/report")
def report(request: Request, period: str = Query("week", pattern="^(week|month)$")) -> dict:
    db, _ = services(request)
    now = datetime.now(timezone.utc)
    return period_report(db, period, now, ZoneInfo(config.timezone))


@app.get("/api/day")
def day(request: Request, date_value: str = Query(alias="date", pattern=r"^\d{4}-\d{2}-\d{2}$")) -> dict:
    db, monitor = services(request)
    try:
        target = date.fromisoformat(date_value)
    except ValueError as exc:
        raise HTTPException(422, "日期格式必须为 YYYY-MM-DD") from exc
    now = datetime.now(timezone.utc)
    result = day_detail(db, target, now, ZoneInfo(config.timezone))
    if target == now.astimezone(ZoneInfo(config.timezone)).date():
        status = monitor.status(db.settings()["sedentary_minutes"])
        for row in result["sessions"]:
            if row["ended_at"] is None:
                row["duration_seconds"] = status["session_duration_seconds"]
    return result


@app.get("/api/stats")
def stats(request: Request) -> dict:
    db, _ = services(request)
    now = datetime.now(timezone.utc)
    tz = ZoneInfo(config.timezone)
    spans = boundaries(now, tz)
    return {name: summarize(db, start, min(end, now), now) for name, (start, end) in spans.items()}


@app.get("/api/sessions")
def sessions(request: Request, period: str = Query("month", pattern="^(day|week|month)$"), limit: int = Query(200, ge=1, le=1000)) -> list[dict]:
    db, _ = services(request)
    now = datetime.now(timezone.utc)
    start, end = boundaries(now, ZoneInfo(config.timezone))[period]
    rows = db.recent_sessions(start, end, now, limit=limit)
    for row in rows:
        row["is_sedentary"] = bool(row["is_sedentary"])
    return rows


@app.get("/api/settings")
def get_settings(request: Request) -> dict:
    db, _ = services(request)
    return db.settings()


@app.put("/api/settings")
def put_settings(payload: SettingsUpdate, request: Request) -> dict:
    db, _ = services(request)
    return db.update_settings(payload.model_dump())


@app.get("/api/camera/settings")
def get_camera_settings(request: Request) -> dict:
    _, monitor = services(request)
    snapshot = monitor.camera.snapshot()
    return CameraSettingsStore.public(monitor.camera.config, online=snapshot.online, error=snapshot.error)


@app.put("/api/camera/settings")
async def put_camera_settings(payload: CameraSettingsUpdate, request: Request) -> dict:
    _, monitor = services(request)
    store: CameraSettingsStore = request.app.state.camera_store
    lock: asyncio.Lock = request.app.state.camera_config_lock
    async with lock:
        candidate = store.merge(monitor.camera.config, payload.model_dump(mode="json"))
        valid, detail = await asyncio.to_thread(validate_camera_connection, candidate, 15)
        if not valid:
            raise HTTPException(422, f"新配置未生效，已保留原连接：{detail}")
        try:
            store.save(candidate)
        except OSError as exc:
            logger.error("Unable to persist camera settings: %s", exc)
            raise HTTPException(500, "摄像头已验证，但配置文件无法写入；原连接未改变") from exc
        await asyncio.to_thread(monitor.camera.reconfigure, candidate)
        result = CameraSettingsStore.public(candidate, online=True, error="")
        result.update({"validated": True, "message": "摄像头连接验证成功，监测服务已切换到新配置"})
        return result


@app.get("/api/notifications/settings")
def get_notification_settings(request: Request) -> dict:
    _, monitor = services(request)
    return NotificationSettingsStore.public(monitor.notifier.config)


@app.put("/api/notifications/settings")
def put_notification_settings(payload: NotificationSettingsUpdate, request: Request) -> dict:
    _, monitor = services(request)
    store: NotificationSettingsStore = request.app.state.notification_store
    candidate = store.merge(monitor.notifier.config, payload.model_dump(mode="json"))
    try:
        store.save(candidate)
    except OSError as exc:
        logger.error("Unable to persist notification settings: %s", exc)
        raise HTTPException(500, "推送配置无法写入数据目录") from exc
    monitor.notifier.reconfigure(candidate)
    return NotificationSettingsStore.public(candidate)


@app.post("/api/notifications/test")
async def test_notification(request: Request) -> dict:
    db, monitor = services(request)
    settings = db.settings()
    if not settings["bark_enabled"] and not settings["webhook_enabled"]:
        raise HTTPException(400, "请先启用一种推送方式")
    result = await monitor.notifier.send("松坐连接成功", "这是一条测试消息。之后的久坐提醒会从这里送达。", group="测试", use_bark=settings["bark_enabled"], use_webhook=settings["webhook_enabled"])
    if not result.success:
        raise HTTPException(502, result.detail)
    return {"success": True, "detail": result.detail}


static_dir = Path(config.static_dir)
if static_dir.is_dir():
    assets = static_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        target = static_dir / path
        if path and target.is_file() and static_dir in target.resolve().parents:
            return FileResponse(target)
        return FileResponse(static_dir / "index.html")
