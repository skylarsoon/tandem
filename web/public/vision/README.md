# Local camera models

- `face-detector.tflite`: Google MediaPipe BlazeFace short-range float16 v1.
  https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite
- `hand-landmarker.task`: Google MediaPipe Hand Landmarker float16 v1.
  https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task
- `wasm/`: generated from the installed `@mediapipe/tasks-vision` package by
  `scripts/prepare-vision.mjs` during npm postinstall; excluded from Git.

Official API documentation:
https://developers.google.com/edge/mediapipe/solutions/vision/hand_landmarker/web_js

Both model and runtime requests use this app's origin. The camera is opt-in,
position/gesture data stays in memory, and no frames are uploaded or saved.
