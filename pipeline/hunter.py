"""
hunter.py — Autonomous tennis ball hunting robot main loop.

Integrates: TPU inference → 5-state FSM → PID control → motor/servo output.

Ported from AKA-00 tennis_hunter.py main_v(), adapted for:
  - SG2002 TPU (40ms inference vs CPU 14s)
  - C preprocessing (YUYV → CHW, 143ms)
  - YUYV native pipeline (no OpenCV)

Two modes:
  PC mock:   python pipeline/hunter.py --mode pc       # numpy mock, print commands
  Board:     python pipeline/hunter.py --mode board    # real TPU + hardware
"""

import sys
import os
import time
import signal
import argparse
from dataclasses import dataclass, field
from typing import Optional

from .config import Config, config_board, config_pc
from .state_machine import (
    HunterStateMachine, RobotGeometry, TargetInfo, ControlOutput, State,
)
from .motor_driver import MockMotorDriver, TtPidDriver, create_motor_driver
from .servo_driver import MockServo, create_servo


# ═══════════════════════════════════════════════════════════════════════
# Red bucket detector (HSV color, no OpenCV dependency)
# ═══════════════════════════════════════════════════════════════════════

def detect_red_bucket(yuv_data: bytes, width: int, height: int) -> Optional[TargetInfo]:
    """
    Detect red bucket from YUYV frame using simple color thresholding.

    No OpenCV dependency — operates directly on YUYV pixel pairs.
    Ported from AKA-00 get_red_bucket_local().

    Returns the largest red bounding box, or None.
    """
    pixels = len(yuv_data) // 2
    min_x, min_y = width, height
    max_x, max_y = 0, 0
    red_count = 0

    # Simple YUYV red detection: YUYV is Y0 U Y1 V Y2 U ...
    # Red in YUV: V channel high, U channel moderate
    # We check: V > 160 and (V - U) > 30 (red hue)
    for i in range(0, len(yuv_data) - 3, 4):
        # YUYV = [Y0, U, Y1, V]
        U = yuv_data[i + 1]
        V = yuv_data[i + 3]
        Y0 = yuv_data[i]
        Y1 = yuv_data[i + 2]

        for yi, Y in enumerate([Y0, Y1]):
            if V > 140 and (V - max(U, 0)) > 20 and Y > 50:
                # Red pixel found
                px_idx = i // 2 + yi
                x = px_idx % width
                y = px_idx // width
                if y < height:
                    min_x = min(min_x, x)
                    min_y = min(min_y, y)
                    max_x = max(max_x, x)
                    max_y = max(max_y, y)
                    red_count += 1

    # Filter by area: must be > 5000 pixels (same threshold as AKA-00)
    if red_count < 5000:
        return None

    return TargetInfo(
        has_target=True,
        x=float(min_x), y=float(min_y),
        w=float(max_x - min_x), h=float(max_y - min_y),
        confidence=0.8,  # not applicable for color detection
    )


# ═══════════════════════════════════════════════════════════════════════
# PC mock — simulated detections for testing state machine logic
# ═══════════════════════════════════════════════════════════════════════

class MockDetector:
    """
    Simulates YOLO detections for PC testing of the state machine.

    Cycles through a pre-scripted scenario to exercise all states:
      no detection → chase → detect far → approach → position → grab → bucket → release
    """

    def __init__(self, scenario: str = "full_cycle"):
        self._frame = 0
        self._scenario = scenario

    def detect(self) -> Optional[TargetInfo]:
        """Return a simulated detection for the current frame."""
        self._frame += 1
        f = self._frame

        if self._scenario == "full_cycle":
            return self._scenario_full(f)
        elif self._scenario == "approach_only":
            return self._scenario_approach(f)
        else:
            # Single target, centered
            return TargetInfo(has_target=True, x=200, y=300,
                              w=100, h=100, confidence=0.95)

    def _scenario_full(self, f: int) -> Optional[TargetInfo]:
        """Full 5-state cycle simulation."""
        if f < 30:
            # No target → CHASE_TENNIS (robot rotates searching)
            return None
        elif f < 60:
            # Far target appears on the left → turn left
            return TargetInfo(has_target=True, x=50, y=200, w=60, h=60,
                              confidence=0.85)
        elif f < 90:
            # Target approaching center, getting bigger → FORWARD
            return TargetInfo(has_target=True, x=250, y=220, w=200, h=200,
                              confidence=0.92)
        elif f < 120:
            # Target in position zone → POSITION_TENNIS
            return TargetInfo(has_target=True, x=270, y=280, w=340, h=340,
                              confidence=0.95)
        elif f < 135:
            # Target in grab zone (bottom center) → GRAB_TENNIS
            return TargetInfo(has_target=True, x=265, y=350, w=370, h=370,
                              confidence=0.97)
        elif f < 170:
            # After grab → CHASE_BUCKET
            # Red bucket detection (simulated via w=640 to fill frame)
            return TargetInfo(has_target=True, x=0, y=100, w=300, h=250,
                              confidence=0.80)
        elif f < 190:
            # Bucket fills frame → RELEASE_TENNIS
            return TargetInfo(has_target=True, x=0, y=0, w=640, h=400,
                              confidence=0.80)
        else:
            # Back to search
            return None

    def _scenario_approach(self, f: int) -> Optional[TargetInfo]:
        """Simple approach scenario."""
        step = min(300, f)
        # Target grows from far to near
        w = 40 + step * 1.2    # grows from 40 to ~400
        x = 320 - w / 2        # stays centered
        return TargetInfo(has_target=True, x=x, y=240 - w / 2,
                          w=w, h=w, confidence=0.90)


# ═══════════════════════════════════════════════════════════════════════
# Hunter — main robot loop
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class HunterConfig:
    """Hunter runtime configuration."""
    mode: str = "pc"             # "pc" or "board"
    dry_run: bool = True         # False → send real motor commands
    motor_driver_type: str = "mock"
    servo_driver_type: str = "mock"
    motor_port: str = "/dev/ttyS1"
    servo_port: str = "/dev/ttyS2"
    motor_baud: int = 115200
    servo_baud: int = 115200
    geometry: RobotGeometry = field(default_factory=RobotGeometry)

    # Board mode: paths
    model_path: str = "/akars_tennis/model/yolov8n_tennis_v2.cvimodel"
    c_lib_path: str = "/lib/preprocess_ops.so"
    camera_bin: str = "/guest/linux/2.camera"

    # Logging
    verbose: bool = True
    fps_interval: int = 10


class Hunter:
    """
    Main robot control loop.

    Usage:
        config = HunterConfig(mode="pc")
        hunter = Hunter(config)
        hunter.run()
    """

    def __init__(self, cfg: HunterConfig):
        self.cfg = cfg
        self.geom = cfg.geometry
        self._fsm = HunterStateMachine(self.geom)
        self._motor = create_motor_driver(
            mode=cfg.motor_driver_type,
            port=cfg.motor_port, baudrate=cfg.motor_baud,
        )
        self._servo = create_servo(
            driver=cfg.servo_driver_type,
            port=cfg.servo_port, baudrate=cfg.servo_baud,
        )
        self._detector = None  # set up in _init_mode()
        self._camera = None    # Board mode: subprocess camera
        self._pp = None        # Board mode: C preprocessor
        self._inf = None       # Board mode: TPU inference
        self._frame_count = 0
        self._running = False
        self._timings = {"cam": [], "pre": [], "tpu": [], "nms": [], "ctrl": []}

    def _init_mode(self):
        """Set up detectors based on mode."""
        if self.cfg.mode == "pc":
            self._detector = MockDetector("full_cycle")
            print("[Hunter] PC mock mode — simulated detections")
        else:
            # Board mode: set up real TPU pipeline
            self._init_board()

    def _init_board(self):
        """Initialize real hardware pipeline (same as run.py)."""
        import ctypes
        import struct
        import subprocess

        # Camera subprocess
        self._camera_bin = self.cfg.camera_bin
        self._cam_w, self._cam_h = self.geom.frame_width, self.geom.frame_height
        self._cam_fmt = "<IIIIIIQ"
        self._cam_fsz = struct.calcsize(self._cam_fmt)
        self._cam_magic = 0xC0C0C0C0
        self._cam_buf = b""
        self._cam_synced = False
        self._cam_proc = None

        # C preprocessor
        lib = ctypes.CDLL(self.cfg.c_lib_path)
        lib.yuyv_resize_planar.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
            ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
        ]
        lib.yuyv_resize_planar.restype = ctypes.c_int
        self._pp_lib = lib
        self._pp_tw, self._pp_th = 640, 640
        self._pp_out_sz = 640 * 640 * 3

        # TPU
        _cvi = ctypes.CDLL("libcviruntime.so")
        self._cvi = _cvi

        model = self.cfg.model_path.encode() if isinstance(self.cfg.model_path, str) else self.cfg.model_path
        self._tpu_model = ctypes.c_void_p(0)
        self._tpu_it = ctypes.c_void_p(0)
        self._tpu_in_n = ctypes.c_int32(0)
        self._tpu_ot = ctypes.c_void_p(0)
        self._tpu_on_n = ctypes.c_int32(0)

        rc = _cvi.CVI_RT_Init()
        if rc:
            raise RuntimeError(f"TPU init failed: {rc}")
        rc = _cvi.CVI_NN_RegisterModel(model, ctypes.byref(self._tpu_model))
        if rc:
            raise RuntimeError(f"Model register failed: {rc}")
        rc = _cvi.CVI_NN_GetInputOutputTensors(
            self._tpu_model, ctypes.byref(self._tpu_it), ctypes.byref(self._tpu_in_n),
            ctypes.byref(self._tpu_ot), ctypes.byref(self._tpu_on_n),
        )
        if rc:
            raise RuntimeError(f"GetTensors failed: {rc}")

        # NMS
        lib.nms_decode.argtypes = [
            ctypes.POINTER(ctypes.c_float), ctypes.c_int,
            ctypes.c_float, ctypes.c_float, ctypes.c_int,
            ctypes.POINTER(ctypes.c_float),
        ]
        lib.nms_decode.restype = ctypes.c_int
        self._det_array = (ctypes.c_float * (20 * 5))()

        print("[Hunter] Board mode — real TPU + camera ready")

    def run(self):
        """Main control loop."""
        self._init_mode()
        self._running = True
        self._frame_count = 0
        t_start = time.time()

        print("=" * 60)
        print(f"  Hunter starting — mode={self.cfg.mode} dry_run={self.cfg.dry_run}")
        print(f"  Initial state: {self._fsm.state}")
        print("=" * 60)

        # Signal handler
        def shutdown(sig=None, frame=None):
            self._running = False
        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)

        try:
            while self._running:
                self._tick()
        except KeyboardInterrupt:
            pass
        finally:
            self._shutdown(t_start)

    def _tick(self):
        """One iteration of the control loop."""
        tf = time.time()

        # ── 1. Get frame + detection ──
        if self.cfg.mode == "pc":
            target = self._detector.detect()
            cam_ms = 0
            pre_ms = 0
            tpu_ms = 0
            nms_ms = 0
        else:
            target, cam_ms, pre_ms, tpu_ms, nms_ms = self._board_detect()

        # ── 2. Red bucket detection (chase_bucket state) ──
        if self._fsm.state == State.CHASE_BUCKET and self.cfg.mode == "board":
            bucket = self._board_bucket_detect()
            if bucket and bucket.has_target:
                target = bucket

        # ── 3. State machine update ──
        out = self._fsm.update(target or TargetInfo())

        # ── 4. Execute motor command ──
        if not self.cfg.dry_run:
            self._motor.set_speeds(int(out.car_left), int(out.car_right))
        else:
            self._motor.set_speeds(int(out.car_left), int(out.car_right))

        # ── 5. Execute arm action ──
        if out.arm_action == "grab":
            if not self.cfg.dry_run:
                self._servo.grab()
            else:
                print(f"  [DRY RUN] ARM → GRAB")
        elif out.arm_action == "release":
            if not self.cfg.dry_run:
                self._servo.release()
            else:
                print(f"  [DRY RUN] ARM → RELEASE")

        # ── 6. Timing ──
        total_ms = (time.time() - tf) * 1000
        ctrl_ms = total_ms - cam_ms - pre_ms - tpu_ms - nms_ms if self.cfg.mode == "board" else total_ms

        self._frame_count += 1

        # ── 7. Log ──
        if self.cfg.verbose and self._frame_count % self.cfg.fps_interval == 0:
            self._log_frame(target, out, cam_ms, pre_ms, tpu_ms, nms_ms, ctrl_ms, total_ms)

    def _board_detect(self) -> tuple:
        """Run real TPU detection pipeline. Returns (target, cam_ms, pre_ms, tpu_ms, nms_ms)."""
        # Camera capture
        t0 = time.time()
        yuv = self._cam_read()
        cam_ms = (time.time() - t0) * 1000

        if yuv is None:
            return (None, cam_ms, 0, 0, 0)

        # Preprocess (YUYV → CHW, writes directly to TPU input buffer)
        t0 = time.time()
        in_ptr = ctypes.c_void_p(struct.unpack("<Q", ctypes.string_at(self._tpu_it.value + 64, 8))[0])
        src = ctypes.c_char_p(yuv)
        self._pp_lib.yuyv_resize_planar(src, self._cam_w, self._cam_h,
                                         in_ptr, self._pp_tw, self._pp_th)
        pre_ms = (time.time() - t0) * 1000

        # TPU Forward
        t0 = time.time()
        self._cvi.CVI_NN_Forward(self._tpu_model, self._tpu_it, self._tpu_in_n,
                                  self._tpu_ot, self._tpu_on_n)
        tpu_ms = (time.time() - t0) * 1000

        # NMS (zero-copy from TPU output buffer)
        t0 = time.time()
        out_ptr_addr = struct.unpack("<Q", ctypes.string_at(self._tpu_ot.value + 64, 8))[0]
        out_ptr = ctypes.cast(out_ptr_addr, ctypes.POINTER(ctypes.c_float))
        out_shape = [struct.unpack("<i", ctypes.string_at(self._tpu_ot.value + 8 + i * 4, 4))[0] for i in range(6)]
        n_anchors = out_shape[2]
        n = self._pp_lib.nms_decode(out_ptr, n_anchors,
                                     ctypes.c_float(0.5), ctypes.c_float(0.45),
                                     20, self._det_array)
        nms_ms = (time.time() - t0) * 1000

        # Convert to TargetInfo
        if n > 0:
            conf = float(self._det_array[4])
            cx = float(self._det_array[0])
            cy = float(self._det_array[1])
            w = float(self._det_array[2])
            h = float(self._det_array[3])
            x = cx - w / 2
            y = cy - h / 2
            target = TargetInfo(has_target=True, x=x, y=y, w=w, h=h, confidence=conf)
        else:
            target = None

        return (target, cam_ms, pre_ms, tpu_ms, nms_ms)

    def _board_bucket_detect(self) -> Optional[TargetInfo]:
        """Detect red bucket from current YUYV frame."""
        # Get latest frame
        yuv = self._cam_last_frame()
        if yuv is None:
            return None
        return detect_red_bucket(yuv, self._cam_w, self._cam_h)

    def _cam_read(self) -> Optional[bytes]:
        """Read one frame from the camera subprocess (same logic as run.py Cam.get())."""
        import struct
        # Simplified: re-use existing run.py Cam class logic
        # For now, return None (placeholder — real implementation reuses Cam class)
        return None  # TODO: integrate with existing Cam class on board

    def _cam_last_frame(self) -> Optional[bytes]:
        """Get last captured frame (non-blocking)."""
        return None  # TODO

    def _log_frame(self, target, out, cam_ms, pre_ms, tpu_ms, nms_ms, ctrl_ms, total_ms):
        """Print one-line status."""
        if target and target.has_target:
            det_str = (f"det={target.confidence:.2f} @ "
                       f"({target.x:.0f},{target.y:.0f}) "
                       f"{target.w:.0f}x{target.h:.0f}")
        else:
            det_str = "det=none"

        if self.cfg.mode == "board":
            timing = (f"cam:{cam_ms:.0f}ms pre:{pre_ms:.0f}ms "
                      f"tpu:{tpu_ms:.0f}ms nms:{nms_ms:.0f}ms "
                      f"ctrl:{ctrl_ms:.0f}ms")
        else:
            timing = f"mock:{total_ms:.0f}ms"

        motor = (f"L={out.car_left:+.0f} R={out.car_right:+.0f}")
        action = f" [{out.arm_action}]" if out.arm_action else ""
        log_line = (f"[{self._frame_count:04d}] {self._fsm.state:16s} "
                    f"{det_str:45s} {motor} "
                    f"grab={out.grab_confirm}/{self.geom.grab_confirm_frames}"
                    f"{action}")
        print(log_line)

        # Detailed log on state transitions
        if out.log:
            print(f"  >>> {out.log}")

    def _shutdown(self, t_start):
        """Clean shutdown."""
        elapsed = time.time() - t_start
        if self._frame_count > 0:
            fps = self._frame_count / elapsed
            print(f"\n{'='*60}")
            print(f"  Hunter shutdown: {self._frame_count} frames "
                  f"in {elapsed:.1f}s → {fps:.1f} fps")
            print(f"  Final state: {self._fsm.state}")
            print(f"{'='*60}")

        # Stop motors
        self._motor.stop()
        self._motor.close()

        # Close serial ports
        if hasattr(self._servo, 'close'):
            self._servo.close()

        print("  Motors stopped. Done.")


# ═══════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="AKA-00 Hunter — autonomous tennis ball robot")
    parser.add_argument("--mode", choices=["pc", "board"], default="pc",
                        help="Run mode: pc (mock) or board (real hardware)")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Print commands without sending to hardware")
    parser.add_argument("--real", action="store_true",
                        help="Send real commands to hardware")
    parser.add_argument("--scenario", default="full_cycle",
                        help="PC mock scenario: full_cycle, approach_only")
    parser.add_argument("--motor-port", default="/dev/ttyS1")
    parser.add_argument("--servo-port", default="/dev/ttyS2")
    parser.add_argument("--verbose", action="store_true", default=True)
    args = parser.parse_args()

    cfg = HunterConfig(
        mode=args.mode,
        dry_run=not args.real,
        motor_driver_type="mock" if args.mode == "pc" else "tt_pid",
        servo_driver_type="mock" if args.mode == "pc" else "zp10s",
        motor_port=args.motor_port,
        servo_port=args.servo_port,
        verbose=args.verbose,
    )

    hunter = Hunter(cfg)
    hunter.run()


if __name__ == "__main__":
    main()
