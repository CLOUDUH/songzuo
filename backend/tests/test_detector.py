from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.detector import YuNetSeatDetector, create_detector  # noqa: E402


def test_auto_mode_uses_yunet_seat_detector(tmp_path: Path) -> None:
    face_model = Path(__file__).parents[1] / "assets" / "face_detection_yunet_2023mar.onnx"
    detector = create_detector("auto", tmp_path / "missing-yolo.onnx", face_model, 320)
    assert isinstance(detector, YuNetSeatDetector)
    assert detector.name == "seat-yunet"
