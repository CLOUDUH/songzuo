#!/usr/bin/env sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
mkdir -p "$project_dir/models"
docker run --rm -v "$project_dir/models:/models" python:3.12-slim sh -c \
  "pip install --no-cache-dir ultralytics onnx >/dev/null && yolo export model=yolov8n.pt format=onnx imgsz=320 simplify=True && cp yolov8n.onnx /models/yolov8n.onnx"
echo "模型已生成：$project_dir/models/yolov8n.onnx"
