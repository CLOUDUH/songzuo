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


def create_detector(mode: str, model_path: Path, input_size: int) -> HogPersonDetector | YoloPersonDetector:
    if mode in {"auto", "yolo"} and model_path.is_file():
        return YoloPersonDetector(model_path, input_size)
    if mode == "yolo":
        raise FileNotFoundError(f"未找到 ONNX 模型：{model_path}")
    return HogPersonDetector(input_size)
