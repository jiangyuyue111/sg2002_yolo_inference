"""
config.py — centralized configuration for the SG2002 TPU pipeline.

All tuneable parameters live here. No magic numbers in code.
"""

import os
from dataclasses import dataclass, field


@dataclass
class Config:
    """Pipeline configuration — one place to tune everything."""

    # ── Model ──────────────────────────────────────────────────────
    # cvimodel path on the board
    model_path: str = "/akars_tennis/model/yolov8n_tennis_v2.cvimodel"

    # TPU input tensor dimensions (must match cvimodel)
    input_width: int = 640
    input_height: int = 640
    input_channels: int = 3

    # Output tensor shape [N, C, num_anchors, 1] — parsed from engine at runtime
    # C=5 means cx,cy,w,h,conf for single-class model

    # ── Detection thresholds ───────────────────────────────────────
    conf_threshold: float = 0.5     # minimum confidence to consider a detection
    nms_iou_threshold: float = 0.45  # NMS suppression IoU threshold

    # ── Position analysis ──────────────────────────────────────────
    # 9-grid zone thresholds (fraction of frame width/height)
    # zone_left < 0.33 < zone_center_x < 0.66 < zone_right
    zone_left_boundary: float = 0.33
    zone_right_boundary: float = 0.66
    zone_top_boundary: float = 0.33
    zone_bottom_boundary: float = 0.66

    # Size threshold for "near" vs "far" (box area / frame area)
    near_size_threshold: float = 0.05   # >5% of frame → near
    mid_size_threshold: float = 0.01    # >1% → mid-range

    # ── Control ────────────────────────────────────────────────────
    # dry_run=True → print commands, don't send to hardware
    dry_run: bool = True

    # Serial port for car/arm control (only used when dry_run=False)
    serial_port: str = "/dev/ttyS0"
    serial_baud: int = 115200

    # ── Camera interface format (agreed with 李明涛) ────────────────
    # Frame header magic number
    frame_magic: int = 0xC0C0C0C0
    # Expected pixel format: "BGR" or "RGB"
    camera_pixel_format: str = "BGR"

    # ── Runtime mode ───────────────────────────────────────────────
    # "board" = real TPU + C preprocessing
    # "pc"    = numpy preprocessing + mock inference (for testing logic)
    mode: str = field(default_factory=lambda: os.environ.get("PIPELINE_MODE", "pc"))

    # C library path for accelerated preprocessing + NMS (board mode)
    preprocess_lib_path: str = "/lib/preprocess_ops.so"

    # Use C NMS (fast) instead of Python NMS (fallback when lib not found)
    use_c_nms: bool = True

    # ── Logging ────────────────────────────────────────────────────
    verbose: bool = True
    log_fps: bool = True       # print FPS every N frames
    fps_interval: int = 30     # print FPS every this many frames

    @property
    def is_board(self) -> bool:
        return self.mode == "board"

    @property
    def is_pc(self) -> bool:
        return self.mode == "pc"

    # ── Class labels ───────────────────────────────────────────────
    # Maps class index → label name (for multi-class models)
    # Current model is single-class (tennis ball), extend as needed
    class_labels: dict = field(default_factory=lambda: {
        0: "tennis_ball",
    })


# Pre-built configs for common scenarios
def config_board() -> Config:
    """Configuration for SG2002 board with real TPU."""
    return Config(mode="board", dry_run=True)


def config_pc() -> Config:
    """Configuration for PC testing with mock inference."""
    return Config(mode="pc", dry_run=True)


def config_production() -> Config:
    """Production config — real TPU + real hardware control."""
    return Config(mode="board", dry_run=True)
