# Bundled detector asset

`face_detection_yunet_2023mar.onnx` is the official OpenCV Zoo YuNet face detector:

- Source: https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet
- SHA-256: `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`
- Size: approximately 227 KB

YuNet is used only inside the configured seat ROI. It replaces Haar profile-face detection as the primary fallback detector because it provides a real confidence score and is substantially less susceptible to chair-grid and monitor-edge false positives.
