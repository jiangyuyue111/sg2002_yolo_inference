"""
state_machine.py — Robot state machine for autonomous tennis ball hunting.

Ported from AKA-00 tennis_hunter.py Robot class, adapted for our TPU pipeline.
5-state FSM: chase_tennis → position_tennis → grab_tennis → chase_bucket → release_tennis

Design:
  - Pure logic, no I/O — easy to test on PC.
  - Receives detection boxes, outputs car/arm commands.
  - Confirm-grab counter prevents false triggers.
"""

import time
from dataclasses import dataclass, field
from typing import Optional


# ═══════════════════════════════════════════════════════════════════════
# State enum
# ═══════════════════════════════════════════════════════════════════════

class State:
    CHASE_TENNIS   = "chase_tennis"     # 搜索网球
    POSITION_TENNIS = "position_tennis"  # 对准网球
    GRAB_TENNIS    = "grab_tennis"       # 夹取网球
    CHASE_BUCKET   = "chase_bucket"      # 找桶
    RELEASE_TENNIS = "release_tennis"    # 释放网球

    # Display order
    ALL = [CHASE_TENNIS, POSITION_TENNIS, GRAB_TENNIS,
           CHASE_BUCKET, RELEASE_TENNIS]


# ═══════════════════════════════════════════════════════════════════════
# Frame geometry constants (same as AKA-00 Robot)
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class RobotGeometry:
    """Physical parameters of the robot for control decisions."""
    frame_width: int = 640       # camera frame width
    frame_height: int = 480      # camera frame height

    # Tennis grab zone (horizontal center of frame)
    # Object center_x in [x_left_grab, x_right_grab] → ready to grab
    x_left_grab: float = 258.0
    x_right_grab: float = 298.0

    # Tennis width thresholds
    # box_width > tennis_width_near → close enough to grab
    # box_width > tennis_width_far → close enough to position
    tennis_width_far: float = 320.0    # start positioning
    tennis_width_near: float = 380.0   # start grabbing

    # Bucket detection
    # box fills the frame width → at bucket
    bucket_full_width: float = 640.0

    # Grab confirmation: consecutive frames with target in grab zone
    grab_confirm_frames: int = 10

    # 9-grid boundaries (fraction of frame)
    left_boundary: float = 0.33
    right_boundary: float = 0.66
    top_boundary: float = 0.33
    bottom_boundary: float = 0.66


# ═══════════════════════════════════════════════════════════════════════
# Detection info for the state machine
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class TargetInfo:
    """Minimal detection info for state machine decisions."""
    has_target: bool = False
    x: float = 0.0             # bounding box left (pixels)
    y: float = 0.0             # bounding box top (pixels)
    w: float = 0.0             # bounding box width (pixels)
    h: float = 0.0             # bounding box height (pixels)
    confidence: float = 0.0

    @property
    def cx(self) -> float:
        """Center x of box."""
        return self.x + self.w / 2 if self.w > 0 else 0.0

    @property
    def cy(self) -> float:
        """Center y of box."""
        return self.y + self.h / 2 if self.h > 0 else 0.0

    @classmethod
    def from_detection(cls, det, frame_w=640, frame_h=480):
        """Convert a Detection tuple (conf, cx, cy, w, h) to TargetInfo.

        Args:
            det: tuple of (confidence, center_x_px, center_y_px, x1_px, y1_px, x2_px, y2_px)
                 or simpler (confidence, cx, cy, w, h)
            frame_w, frame_h: used for coordinate conversion.
        """
        if not det:
            return cls()
        if len(det) >= 7:
            # Full format: conf, cx, cy, x1, y1, x2, y2
            conf, cx, cy, x1, y1, x2, y2 = det[:7]
            return cls(
                has_target=True,
                x=float(x1), y=float(y1),
                w=float(x2 - x1), h=float(y2 - y1),
                confidence=float(conf),
            )
        elif len(det) >= 5:
            # Simple format: conf, cx, cy, w, h
            conf, cx, cy, w, h = det[:5]
            x1 = cx - w / 2
            y1 = cy - h / 2
            return cls(
                has_target=True,
                x=float(x1), y=float(y1),
                w=float(w), h=float(h),
                confidence=float(conf),
            )
        return cls()

    @classmethod
    def from_bbox(cls, bbox: dict):
        """Convert from {"x": int, "y": int, "w": int, "h": int} dict."""
        if not bbox:
            return cls()
        return cls(
            has_target=True,
            x=float(bbox.get("x", 0)),
            y=float(bbox.get("y", 0)),
            w=float(bbox.get("w", 0)),
            h=float(bbox.get("h", 0)),
            confidence=float(bbox.get("conf", 0)),
        )


# ═══════════════════════════════════════════════════════════════════════
# Car / Arm command enums
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class ControlOutput:
    """Output of the state machine for one frame."""
    state: str = State.CHASE_TENNIS
    car_left: float = 0.0      # left motor speed (-100 .. 100)
    car_right: float = 0.0     # right motor speed (-100 .. 100)
    arm_action: str = ""       # "grab", "release", "" (none)
    box_x: float = 0.0
    box_w: float = 0.0
    box_h: float = 0.0
    frame_h: float = 0.0
    grab_confirm: int = 0
    log: str = ""


# ═══════════════════════════════════════════════════════════════════════
# HunterStateMachine
# ═══════════════════════════════════════════════════════════════════════

class HunterStateMachine:
    """
    5-state FSM for autonomous tennis ball collection.

    Ported from AKA-00 tennis_hunter.py Robot class.
    Pure logic — no I/O dependencies.

    Usage:
        fsm = HunterStateMachine(geometry)
        for each frame:
            target = TargetInfo.from_detection(dets[0] if dets else None)
            out = fsm.update(target)
            # out.car_left, out.car_right → motor speeds
            # out.arm_action → "grab" / "release" / ""
    """

    def __init__(self, geom: Optional[RobotGeometry] = None):
        self.geom = geom or RobotGeometry()
        self._state = State.CHASE_TENNIS
        self._grab_confirm = 0
        self._box_x = 0.0
        self._box_w = 0.0
        self._box_h = 0.0
        self._frame_h = 0.0
        self._state_start_time = time.time()
        self._state_entry_count = 0

    # ── Properties ──

    @property
    def state(self) -> str:
        return self._state

    @property
    def grab_confirm(self) -> int:
        return self._grab_confirm

    @property
    def elapsed_in_state(self) -> float:
        return time.time() - self._state_start_time

    @property
    def grab_confirm_needed(self) -> int:
        return self.geom.grab_confirm_frames

    # ── Main update ──

    def update(self, target: TargetInfo) -> ControlOutput:
        """
        Process one frame's detection and produce control output.

        Args:
            target: detected object info (or empty TargetInfo if nothing found).

        Returns:
            ControlOutput with motor speeds and arm actions.
        """
        g = self.geom

        if target is not None and target.has_target and target.w > 0:
            self._box_x = target.x
            self._box_w = target.w
            self._box_h = target.h

        # ── State transition logic ──
        self._apply_transitions(target)

        # ── Generate motor speeds ──
        left, right = self._compute_motor_speeds(target)

        # ── Handle grab/release actions ──
        arm_action, extra_log = self._handle_arm()

        out = ControlOutput(
            state=self._state,
            car_left=left, car_right=right,
            arm_action=arm_action,
            box_x=self._box_x, box_w=self._box_w, box_h=self._box_h,
            frame_h=self._frame_h,
            grab_confirm=self._grab_confirm,
            log=extra_log,
        )
        return out

    # ── State transitions ──

    def _apply_transitions(self, target: TargetInfo):
        """Evaluate and execute state transitions."""
        g = self.geom
        w = self._box_w
        x = self._box_x

        if self._state == State.CHASE_TENNIS:
            if (target is not None and target.has_target and
                    g.tennis_width_far <= w <= g.tennis_width_near):
                self._enter_state(State.POSITION_TENNIS)

        elif self._state == State.POSITION_TENNIS:
            # Fallback: lost target or wrong distance
            if (target is None or not target.has_target or
                    not (g.tennis_width_far < w < g.tennis_width_near)):
                self._enter_state(State.CHASE_TENNIS)
            # Ready to grab: target centered in grab zone
            elif g.x_left_grab <= x <= g.x_right_grab:
                self._enter_state(State.GRAB_TENNIS)

        elif self._state == State.GRAB_TENNIS:
            # Stay in grab until confirm counter reached
            # The _handle_arm() method will trigger transition to CHASE_BUCKET
            pass

        elif self._state == State.CHASE_BUCKET:
            if (target is not None and target.has_target and
                    w >= g.bucket_full_width):
                self._enter_state(State.RELEASE_TENNIS)

        elif self._state == State.RELEASE_TENNIS:
            # Auto-transition back to chase after release
            # (handled in _handle_arm for timing)
            pass

    def _enter_state(self, new_state: str):
        """Transition to a new state."""
        old = self._state
        self._state = new_state
        self._state_start_time = time.time()
        self._state_entry_count += 1
        if new_state == State.GRAB_TENNIS:
            self._grab_confirm = 0

    # ── Motor speed computation ──

    def _compute_motor_speeds(self, target: TargetInfo) -> tuple:
        """
        Compute left/right motor speeds based on current state and target.

        Returns (left_pwm, right_pwm) in range -100..100.
        """
        g = self.geom

        # Default: idle search rotation
        if self._state == State.CHASE_TENNIS:
            return self._idle_search()

        if self._state == State.GRAB_TENNIS:
            return (0.0, 0.0)  # brake

        if self._state == State.RELEASE_TENNIS:
            return self._release_sequence()

        if target is None or not target.has_target:
            return self._idle_search()

        # ── PID-based tracking (position_tennis / chase_bucket) ──
        return self._pid_track(target)

    def _idle_search(self) -> tuple:
        """Rotate in place to search for targets."""
        idle = 80.0  # same as AKA-00 idle_speed = MAX_SPEED // 3
        return (idle, -idle)  # turn right

    def _pid_track(self, target: TargetInfo) -> tuple:
        """
        Proportional controller for tracking a target.

        Ported from AKA-00 Robot.set_motor_speed().
        Uses box position/size to compute differential drive speeds.
        """
        g = self.geom
        MAX_SPEED = 100.0
        MIN_SPEED = MAX_SPEED / 6.0  # ~16.7

        w = target.w
        cx = target.cx
        IMG_WIDTH = g.frame_width

        # Target parameters differ by state
        if self._state == State.CHASE_BUCKET:
            TARGET_W = IMG_WIDTH          # want bucket to fill frame
            Kp_dist = 1.0
            Kp_angle = 0.04
        else:
            TARGET_W = g.tennis_width_far * 0.6 + g.tennis_width_near * 0.4  # ~344
            Kp_dist = 0.8
            Kp_angle = 0.02

        TARGET_X = IMG_WIDTH / 2.0          # center of frame
        WHEEL_BASE = 10.0

        # Compute errors
        error_x = cx - TARGET_X             # horizontal offset
        error_w = w - TARGET_W              # size error (distance)

        # Proportional control
        raw_v = -Kp_dist * error_w          # linear velocity
        raw_omega = -Kp_angle * error_x     # angular velocity

        # Dynamic speed limit based on turn amount
        turn_factor = abs(error_x) / (IMG_WIDTH / 2.0)
        if turn_factor > 0.8:
            max_v = MIN_SPEED * 0.3
        else:
            max_v = MAX_SPEED

        # Clamp linear velocity
        v = max(-max_v, min(max_v, raw_v))
        if 0 < abs(v) < MIN_SPEED:
            v = MIN_SPEED if v > 0 else -MIN_SPEED

        # Differential drive
        diff_speed = raw_omega * WHEEL_BASE
        left_pwm = v + diff_speed
        right_pwm = v - diff_speed

        # Clamp to [-MAX, MAX]
        left_pwm = max(-MAX_SPEED, min(MAX_SPEED, left_pwm))
        right_pwm = max(-MAX_SPEED, min(MAX_SPEED, right_pwm))

        # Minimum speed enforcement
        if 0 < abs(left_pwm) < MIN_SPEED:
            left_pwm = MIN_SPEED if left_pwm > 0 else -MIN_SPEED
        if 0 < abs(right_pwm) < MIN_SPEED:
            right_pwm = MIN_SPEED if right_pwm > 0 else -MIN_SPEED

        return (left_pwm, right_pwm)

    def _release_sequence(self) -> tuple:
        """Timed release sequence: forward → stop → release arm → backward."""
        elapsed = self.elapsed_in_state
        MAX_SPEED = 100.0

        if elapsed < 0.5:
            # Phase 1: drive forward
            return (MAX_SPEED, MAX_SPEED)
        elif elapsed < 1.0:
            # Phase 2: stop (arm release happens in _handle_arm)
            return (0.0, 0.0)
        elif elapsed < 1.5:
            # Phase 3: back up
            return (-MAX_SPEED, -MAX_SPEED)
        else:
            # Phase 4: return to chase
            self._enter_state(State.CHASE_TENNIS)
            return (0.0, 0.0)

    # ── Arm actions ──

    def _handle_arm(self) -> tuple:
        """Handle arm actions based on current state. Returns (action_str, log_str)."""
        if self._state == State.GRAB_TENNIS:
            self._grab_confirm += 1
            if self._grab_confirm >= self.geom.grab_confirm_frames:
                self._grab_confirm = 0
                self._enter_state(State.CHASE_BUCKET)
                return ("grab", f"GRAB! ({self.geom.grab_confirm_frames} frames confirmed)")
            return ("", f"grab_confirm={self._grab_confirm}/{self.geom.grab_confirm_frames}")

        elif self._state == State.RELEASE_TENNIS:
            # Release at the right timing phase
            elapsed = self.elapsed_in_state
            if 0.45 < elapsed < 0.55:
                return ("release", "RELEASE!")
            return ("", "")

        return ("", "")

    # ── Debug ──

    def status_line(self, target: TargetInfo) -> str:
        """One-line status for logging."""
        if target is not None and target.has_target:
            det = (f"box=({target.x:.0f},{target.y:.0f}) "
                   f"{target.w:.0f}x{target.h:.0f} "
                   f"con={target.confidence:.2f}")
        else:
            det = "no-target"
        return f"[{self._state}] {det} | grab={self._grab_confirm}"

    def reset(self):
        """Reset to initial state."""
        self._state = State.CHASE_TENNIS
        self._grab_confirm = 0
        self._state_start_time = time.time()
