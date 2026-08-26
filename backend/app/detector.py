from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np


@dataclass(frozen=True)
class Detection:
    present: bool
    confidence: float
    boxes: tuple[tuple[int, int, int, int], ...] = ()


def _roi_intersects(box: tuple[int, int, int, int], shape: tuple[int, ...], roi: dict[str, float]) -> bool:
    height, width = shape[:2]
    x, y, w, h = box
    center_x, center_y = x + w / 2, y + h / 2
    rx, ry = roi["roi_x"] * width, roi["roi_y"] * height
    rw, rh = roi["roi_w"] * width, roi["roi_h"] * height
    return rx <= center_x <= rx + rw and ry <= center_y <= ry + rh


def _roi_bounds(shape: tuple[int, ...], roi: dict[str, float]) -> tuple[int, int, int, int]:
    height, width = shape[:2]
    x0 = max(0, min(width - 1, int(roi["roi_x"] * width)))
    y0 = max(0, min(height - 1, int(roi["roi_y"] * height)))
    x1 = max(x0 + 1, min(width, int((roi["roi_x"] + roi["roi_w"]) * width)))
    y1 = max(y0 + 1, min(height, int((roi["roi_y"] + roi["roi_h"]) * height)))
    return x0, y0, x1, y1


class FacePersonDetector:
    """在座位 ROI 内检测正脸和左右侧脸，适合上半身被桌面遮挡的固定工位。"""

    name = "face"

    def __init__(self, input_width: int = 360):
        self.input_width = input_width
        self.frontal = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_alt2.xml")
        self.profile = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_profileface.xml")
        if self.frontal.empty() or self.profile.empty():
            raise RuntimeError("OpenCV face cascades are unavailable")

    def detect(self, frame: np.ndarray[Any, Any], roi: dict[str, float]) -> Detection:
        x0, y0, x1, y1 = _roi_bounds(frame.shape, roi)
        crop = frame[y0:y1, x0:x1]
        if crop.size == 0:
            return Detection(False, 0.0)
        scale = min(1.0, self.input_width / max(crop.shape[1], 1))
        resized = cv2.resize(crop, (max(1, int(crop.shape[1] * scale)), max(1, int(crop.shape[0] * scale)))) if scale < 1 else crop
        gray = cv2.equalizeHist(cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY))
        min_face = max(24, int(min(gray.shape[:2]) * 0.07))
        max_face = max(min_face + 1, int(min(gray.shape[:2]) * 0.62))
        found: list[tuple[int, int, int, int]] = []

        frontal = self.frontal.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=4, minSize=(min_face, min_face), maxSize=(max_face, max_face))
        profile_left = self.profile.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=3, minSize=(min_face, min_face), maxSize=(max_face, max_face))
        flipped = cv2.flip(gray, 1)
        profile_right = self.profile.detectMultiScale(flipped, scaleFactor=1.08, minNeighbors=3, minSize=(min_face, min_face), maxSize=(max_face, max_face))

        for boxes, mirrored in ((frontal, False), (profile_left, False), (profile_right, True)):
            for x, y, width, height in boxes:
                if mirrored:
                    x = gray.shape[1] - (x + width)
                box = (
                    int(x0 + x / scale),
                    int(y0 + y / scale),
                    int(width / scale),
                    int(height / scale),
                )
                if _roi_intersects(box, frame.shape, roi):
                    found.append(box)
        return Detection(bool(found), 0.78 if found else 0.0, tuple(found))


class HogPersonDetector:
    """零模型文件的保底方案；适合先验证流程，坐姿准确率低于 YOLO。"""

    name = "hog"

    def __init__(self, input_size: int = 320):
        self.input_size = input_size
        self.hog = cv2.HOGDescriptor()
        self.hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    def detect(self, frame: np.ndarray[Any, Any], roi: dict[str, float]) -> Detection:
        height, width = frame.shape[:2]
        scale = min(1.0, self.input_size / max(width, 1))
        resized = cv2.resize(frame, (int(width * scale), int(height * scale))) if scale < 1 else frame
        boxes, weights = self.hog.detectMultiScale(resized, winStride=(8, 8), padding=(8, 8), scale=1.08)
        restored: list[tuple[int, int, int, int]] = []
        confidences: list[float] = []
        for (x, y, w, h), weight in zip(boxes, weights):
            box = tuple(int(value / scale) for value in (x, y, w, h))
            if _roi_intersects(box, frame.shape, roi):
                restored.append(box)
                confidences.append(float(min(1.0, max(0.0, weight))))
        return Detection(bool(restored), max(confidences, default=0.0), tuple(restored))


class HybridPersonDetector:
    """优先使用低成本脸部检测，未发现时再以 HOG 全身检测补偿。"""

    name = "hybrid"

    def __init__(self, input_size: int = 320):
        self.face = FacePersonDetector(input_width=max(360, input_size))
        self.hog = HogPersonDetector(input_size)

    def detect(self, frame: np.ndarray[Any, Any], roi: dict[str, float]) -> Detection:
        face_result = self.face.detect(frame, roi)
        return face_result if face_result.present else self.hog.detect(frame, roi)


class YoloPersonDetector:
    """使用 OpenCV DNN 执行 YOLOv8n ONNX，避免额外引入 PyTorch/ONNX Runtime。"""

    name = "yolo"

    def __init__(self, model_path: Path, input_size: int = 320, confidence: float = 0.35):
        self.net = cv2.dnn.readNetFromONNX(str(model_path))
        self.input_size = input_size
        self.confidence = confidence

    def detect(self, frame: np.ndarray[Any, Any], roi: dict[str, float]) -> Detection:
        height, width = frame.shape[:2]
        side = max(height, width)
        padded = np.zeros((side, side, 3), dtype=np.uint8)
        padded[:height, :width] = frame
        blob = cv2.dnn.blobFromImage(padded, 1 / 255.0, (self.input_size, self.input_size), swapRB=True, crop=False)
        self.net.setInput(blob)
        output = self.net.forward()
        predictions = np.squeeze(output)
        if predictions.ndim != 2:
            return Detection(False, 0.0)
        if predictions.shape[0] < predictions.shape[1]:
            predictions = predictions.T
        scale = side / self.input_size
        boxes: list[list[int]] = []
        scores: list[float] = []
        for row in predictions:
            if len(row) < 5:
                continue
            person_score = float(row[4])  # COCO class 0: person
            if person_score < self.confidence:
                continue
            cx, cy, bw, bh = row[:4]
            boxes.append([int((cx - bw / 2) * scale), int((cy - bh / 2) * scale), int(bw * scale), int(bh * scale)])
            scores.append(person_score)
        selected = cv2.dnn.NMSBoxes(boxes, scores, self.confidence, 0.45)
        accepted: list[tuple[int, int, int, int]] = []
        accepted_scores: list[float] = []
        for index in np.array(selected).reshape(-1) if len(selected) else []:
            box = tuple(boxes[int(index)])
            if _roi_intersects(box, frame.shape, roi):
                accepted.append(box)
                accepted_scores.append(scores[int(index)])
        return Detection(bool(accepted), max(accepted_scores, default=0.0), tuple(accepted))


def create_detector(mode: str, model_path: Path, input_size: int) -> HybridPersonDetector | HogPersonDetector | YoloPersonDetector:
    if mode in {"auto", "yolo"} and model_path.is_file():
        return YoloPersonDetector(model_path, input_size)
    if mode == "yolo":
        raise FileNotFoundError(f"未找到 ONNX 模型：{model_path}")
    if mode == "hog":
        return HogPersonDetector(input_size)
    return HybridPersonDetector(input_size)
