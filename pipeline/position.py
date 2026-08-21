"""
position.py — Position analysis and decision logic.

Given detection boxes and frame dimensions, determines:
    - Which 9-grid zone each object is in
    - Whether the object is near/mid/far (based on bounding box size)
    - What action the car and arm should take

Designed for tennis ball tracking — adjust zoning and decision rules
for other objects by modifying the thresholds in Config.
"""

from dataclasses import dataclass, field
from typing import Optional

from .inference import Detection


# ═══════════════════════════════════════════════════════════════════════
# Zone system — 9-grid overlay
# ═══════════════════════════════════════════════════════════════════════

#    col 0      col 1      col 2
#  ┌─────────┬──────────┬─────────┐
#  │ 左上    │    上     │ 右上    │  row 0
#  │ Z_LEFT  │ Z_CENTER  │ Z_RIGHT │
#  │ _TOP    │ _TOP      │ _TOP    │
#  ├─────────┼──────────┼─────────┤
#  │  左     │   正中    │   右    │  row 1
#  │ Z_LEFT  │ Z_CENTER  │ Z_RIGHT │
#  │ _MID    │ _MID      │ _MID    │
#  ├─────────┼──────────┼─────────┤
#  │ 左下    │    下     │ 右下    │  row 2
#  │ Z_LEFT  │ Z_CENTER  │ Z_RIGHT │
#  │ _BOTTOM │ _BOTTOM   │ _BOTTOM │
#  └─────────┴──────────┴─────────┘

class Zone:
    """Namespaced constants for the 9-grid zone labels."""
    LEFT_TOP      = "left_top"
    CENTER_TOP    = "center_top"
    RIGHT_TOP     = "right_top"
    LEFT_MID      = "left_mid"
    CENTER_MID    = "center_mid"
    RIGHT_MID     = "right_mid"
    LEFT_BOTTOM   = "left_bottom"
    CENTER_BOTTOM = "center_bottom"
    RIGHT_BOTTOM  = "right_bottom"
    NONE          = "none"   # no detection


# ═══════════════════════════════════════════════════════════════════════
# Command hints
# ═══════════════════════════════════════════════════════════════════════

class Command:
    """Namespaced constants for control commands."""
    # Car movement
    STOP           = "STOP"
    FORWARD        = "FORWARD"
    FORWARD_SLOW   = "FORWARD_SLOW"
    BACKWARD       = "BACKWARD"
    TURN_LEFT      = "TURN_LEFT"
    TURN_RIGHT     = "TURN_RIGHT"
    TURN_LEFT_SLOW = "TURN_LEFT_SLOW"
    TURN_RIGHT_SLOW = "TURN_RIGHT_SLOW"
    SEARCH_LEFT    = "SEARCH_LEFT"     # rotate in place to search
    SEARCH_RIGHT   = "SEARCH_RIGHT"

    # Arm
    ARM_READY      = "ARM_READY"       # move to ready position
    ARM_GRIP       = "ARM_GRIP"        # grip object
    ARM_RELEASE    = "ARM_RELEASE"     # release object
    ARM_HOME       = "ARM_HOME"        # return to home

    # Composite
    APPROACH_AND_GRIP = "APPROACH_AND_GRIP"   # car forward + arm grip
    SEARCH_MODE       = "SEARCH_MODE"          # rotate to find target
    IDLE              = "IDLE"


# ═══════════════════════════════════════════════════════════════════════
# Analysis result
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class PositionResult:
    """Output of position analysis for one frame."""

    # Primary target (highest confidence detection)
    target_class: str = ""
    target_confidence: float = 0.0
    center_x: float = 0.0        # normalized [0, 1], 0=left, 1=right
    center_y: float = 0.0        # normalized [0, 1], 0=top, 1=bottom
    size_ratio: float = 0.0      # detection area / frame area
    zone: str = Zone.NONE        # 9-grid zone label
    distance: str = "none"       # "near", "mid", "far", "none"

    # Control suggestion
    car_command: str = Command.IDLE
    arm_command: str = Command.ARM_HOME

    # Raw data
    detection_count: int = 0
    all_detections: list = field(default_factory=list)

    @property
    def has_target(self) -> bool:
        return self.detection_count > 0

    def summary(self) -> str:
        if not self.has_target:
            return f"[{self.zone}] no target → car={self.car_command}"
        return (f"[{self.zone}] {self.target_class} "
                f"conf={self.target_confidence:.2f} "
                f"pos=({self.center_x:.2f},{self.center_y:.2f}) "
                f"dist={self.distance} size={self.size_ratio:.3f} "
                f"→ car={self.car_command} arm={self.arm_command}")


# ═══════════════════════════════════════════════════════════════════════
# PositionAnalyzer — main analysis engine
# ═══════════════════════════════════════════════════════════════════════

class PositionAnalyzer:
    """
    Analyze detection positions and generate control commands.

    Decision rules:
        - Object in center_mid + near  → APPROACH_AND_GRIP
        - Object in center_mid + far   → FORWARD (approach)
        - Object left of center        → TURN_LEFT / TURN_LEFT_SLOW
        - Object right of center       → TURN_RIGHT / TURN_RIGHT_SLOW
        - No object detected           → SEARCH_MODE (rotate to find)

    Usage:
        pa = PositionAnalyzer(frame_w=640, frame_h=480)
        result = pa.analyze(detections)
        print(result.summary())
    """

    def __init__(self, frame_w: int = 640, frame_h: int = 640,
                 left_boundary: float = 0.33,
                 right_boundary: float = 0.66,
                 top_boundary: float = 0.33,
                 bottom_boundary: float = 0.66,
                 near_threshold: float = 0.05,
                 mid_threshold: float = 0.01,
                 target_class: str = None):
        """
        Args:
            frame_w, frame_h: frame dimensions in pixels.
            left_boundary: x < this → "left" column (fraction of width).
            right_boundary: x > this → "right" column.
            top_boundary: y < this → "top" row.
            bottom_boundary: y > this → "bottom" row.
            near_threshold: detection_area / frame_area > this → "near".
            mid_threshold: detection_area / frame_area > this → "mid".
            target_class: if set, only consider detections of this class.
        """
        self.frame_w = frame_w
        self.frame_h = frame_h
        self.frame_area = frame_w * frame_h
        self.left_b = left_boundary
        self.right_b = right_boundary
        self.top_b = top_boundary
        self.bottom_b = bottom_boundary
        self.near_th = near_threshold
        self.mid_th = mid_threshold
        self.target_class = target_class

    def analyze(self, detections: list[Detection]) -> PositionResult:
        """
        Analyze a list of detections and produce a PositionResult.

        Args:
            detections: list of Detection objects from inference.

        Returns:
            PositionResult with zone, distance, and control commands.
        """
        result = PositionResult()
        result.all_detections = detections
        result.detection_count = len(detections)

        # Filter by target class if specified
        if self.target_class:
            detections = [d for d in detections if d.label == self.target_class]

        if not detections:
            result.zone = Zone.NONE
            result.distance = "none"
            result.car_command = Command.SEARCH_MODE
            result.arm_command = Command.ARM_HOME
            return result

        # Pick primary target: highest confidence
        primary = max(detections, key=lambda d: d.confidence)
        result.target_class = primary.label
        result.target_confidence = primary.confidence

        # Normalized center coordinates [0, 1]
        result.center_x = primary.center_x / self.frame_w
        result.center_y = primary.center_y / self.frame_h

        # Size ratio
        result.size_ratio = primary.area / self.frame_area

        # Determine zone
        result.zone = self._classify_zone(result.center_x, result.center_y)

        # Determine distance
        result.distance = self._classify_distance(result.size_ratio)

        # Generate commands
        result.car_command, result.arm_command = self._decide_command(
            result.zone, result.distance
        )

        return result

    def _classify_zone(self, cx: float, cy: float) -> str:
        """Classify normalized (cx, cy) into a 9-grid zone."""
        # Column
        if cx < self.left_b:
            col = "left"
        elif cx > self.right_b:
            col = "right"
        else:
            col = "center"

        # Row
        if cy < self.top_b:
            row = "top"
        elif cy > self.bottom_b:
            row = "bottom"
        else:
            row = "mid"

        return f"{col}_{row}"

    def _classify_distance(self, size_ratio: float) -> str:
        """Classify detection size into distance category."""
        if size_ratio > self.near_th:
            return "near"
        elif size_ratio > self.mid_th:
            return "mid"
        else:
            return "far"

    def _decide_command(self, zone: str, distance: str) -> tuple:
        """
        Decision matrix: zone × distance → (car_cmd, arm_cmd).

        This is the core control logic — tune this for your specific
        car/arm behavior.
        """
        car = Command.STOP
        arm = Command.ARM_READY

        # ── Center column ──
        if zone == Zone.CENTER_MID:
            if distance == "near":
                car = Command.APPROACH_AND_GRIP
                arm = Command.ARM_GRIP
            elif distance == "mid":
                car = Command.FORWARD_SLOW
                arm = Command.ARM_READY
            else:  # far
                car = Command.FORWARD
                arm = Command.ARM_HOME

        elif zone == Zone.CENTER_TOP:
            car = Command.FORWARD_SLOW   # go forward, object is above
            arm = Command.ARM_READY

        elif zone == Zone.CENTER_BOTTOM:
            car = Command.FORWARD       # object low → might be close but low angle
            arm = Command.ARM_READY

        # ── Left column ──
        elif zone in (Zone.LEFT_TOP, Zone.LEFT_MID):
            car = Command.TURN_LEFT if distance == "far" else Command.TURN_LEFT_SLOW
            arm = Command.ARM_HOME

        elif zone == Zone.LEFT_BOTTOM:
            car = Command.TURN_LEFT_SLOW
            arm = Command.ARM_HOME

        # ── Right column ──
        elif zone in (Zone.RIGHT_TOP, Zone.RIGHT_MID):
            car = Command.TURN_RIGHT if distance == "far" else Command.TURN_RIGHT_SLOW
            arm = Command.ARM_HOME

        elif zone == Zone.RIGHT_BOTTOM:
            car = Command.TURN_RIGHT_SLOW
            arm = Command.ARM_HOME

        return car, arm
