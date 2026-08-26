from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.detector import HybridPersonDetector, create_detector  # noqa: E402


def test_auto_mode_uses_hybrid_detector_without_onnx_model(tmp_path: Path) -> None:
    detector = create_detector("auto", tmp_path / "missing.onnx", 320)
    assert isinstance(detector, HybridPersonDetector)
    assert detector.name == "hybrid"
