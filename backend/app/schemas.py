from __future__ import annotations

from pydantic import BaseModel, Field, HttpUrl, model_validator


class SettingsUpdate(BaseModel):
    sedentary_minutes: int = Field(ge=15, le=360)
    leave_grace_seconds: int = Field(ge=10, le=900)
    leave_confirm_seconds: int = Field(ge=3, le=120)
    merge_gap_seconds: int = Field(ge=15, le=1800)
    sample_interval_seconds: int = Field(ge=1, le=30)
    face_confidence_threshold: float = Field(ge=0.35, le=0.95)
    min_face_width_ratio: float = Field(ge=0.05, le=0.40)
    daily_report_enabled: bool
    daily_report_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    daily_report_title: str = Field(min_length=1, max_length=120)
    daily_report_body: str = Field(min_length=1, max_length=1000)
    weekly_report_enabled: bool
    weekly_report_day: int = Field(ge=1, le=7)
    weekly_report_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    weekly_report_title: str = Field(min_length=1, max_length=120)
    weekly_report_body: str = Field(min_length=1, max_length=1000)
    bark_enabled: bool
    webhook_enabled: bool
    camera_offline_alert_enabled: bool
    camera_offline_minutes: int = Field(ge=1, le=120)
    camera_recovery_alert_enabled: bool
    reminder_title: str = Field(min_length=1, max_length=80)
    reminder_body: str = Field(min_length=1, max_length=300)
    repeat_reminder_enabled: bool
    repeat_reminder_minutes: int = Field(ge=5, le=360)
    repeat_reminder_title: str = Field(min_length=1, max_length=120)
    repeat_reminder_body: str = Field(min_length=1, max_length=1000)
    roi_x: float = Field(ge=0, le=1)
    roi_y: float = Field(ge=0, le=1)
    roi_w: float = Field(gt=0, le=1)
    roi_h: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def validate_roi(self) -> "SettingsUpdate":
        if self.roi_x + self.roi_w > 1 or self.roi_y + self.roi_h > 1:
            raise ValueError("座位区域必须完全位于画面内")
        if self.merge_gap_seconds < self.leave_confirm_seconds:
            raise ValueError("连续会话合并时间不能短于离座确认时间")
        return self


class CameraSettingsUpdate(BaseModel):
    stream_url: HttpUrl
    username: str = Field(default="", max_length=200)
    password: str | None = Field(default=None, max_length=500)
    auth_type: str = Field(pattern=r"^(none|basic|digest)$")
    cookie: str | None = Field(default=None, max_length=4000)
    verify_tls: bool = True


class NotificationSettingsUpdate(BaseModel):
    bark_server: HttpUrl
    bark_device_key: str | None = Field(default=None, max_length=500)
    generic_webhook_url: str | None = Field(default=None, max_length=2000)
