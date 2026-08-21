"""
Pipeline — complete YOLO TPU inference pipeline for SG2002.

Modules:
    config        — all tunable parameters
    image_source  — frame acquisition (local file, camera subprocess, mock)
    preprocessor  — BGR frame → INT8 tensor (C lib or numpy fallback)
    inference     — TPU inference + NMS decoding
    position      — 9-grid position analysis + decision
    controller    — control command output (print or serial)

Usage:
    from pipeline import Pipeline
    pipe = Pipeline(config=Config())
    pipe.run_once("test.jpg")        # single frame
    pipe.run_loop(source)             # continuous loop
"""

from .config import Config
from .image_source import ImageSource, LocalImageSource, CameraImageSource, MockCameraSource, StdinImageSource, Int8ImageSource
from .preprocessor import Preprocessor
from .inference import TPUInference, MockInference, Detection
from .position import PositionAnalyzer
from .controller import Controller

__all__ = [
    "Config",
    "ImageSource", "LocalImageSource", "CameraImageSource", "MockCameraSource",
    "StdinImageSource", "Int8ImageSource",
    "Preprocessor",
    "TPUInference", "MockInference", "Detection",
    "PositionAnalyzer",
    "Controller",
]
