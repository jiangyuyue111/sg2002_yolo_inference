#!/usr/bin/env python3
"""
real_pipeline.py — SG2002 真机全自动网球捡球管线

Camera → TPU检测 → 状态机决策 → 电机+舵机驱动

用法:
    PYTHONPATH=/ python3 /pipeline/real_pipeline.py
    PYTHONPATH=/ python3 /pipeline/real_pipeline.py --chase-only   # 只追球,近处停车不夹取

硬件:
    /dev/ttyS1 → ESP32-C3 → N20 TT马达 (左右轮)
    /dev/ttyS2 → ZP10S 舵机 ×3 (arm + gripper)
"""

import sys, time, signal, os, threading
sys.path.insert(0, "/pipeline")

from pipeline.config import Config
from pipeline.image_source import RawYUYVSource
from pipeline.preprocessor import Preprocessor
from pipeline.inference import TPUInference
from pipeline.position import PositionAnalyzer, Command
from pipeline.motor_driver import create_motor_driver
from pipeline.servo_driver import create_servo

# ── Config ────────────────────────────────────────────
cfg = Config(
    mode="real",
    dry_run=False,
    model_path="/akars_tennis/model/yolov8n_tennis_v2.cvimodel",
    preprocess_lib_path="/lib/preprocess_ops.so",
    conf_threshold=0.35,
    nms_iou_threshold=0.45,
    serial_port="/dev/ttyS0",
    serial_baud=115200,
)

ARM_DRIVER = "zp10s"       # "zp10s" or "sts3215"
ARM_PORT   = "/dev/ttyS2"
MOTOR_PORT = "/dev/ttyS1"

# Chase-only mode: track the ball but STOP at "near" instead of grabbing.
# Use while tuning the chase loop — servo0/servo1 (base + shoulder) are not
# yet calibrated, so grab() would be unreliable and would interfere with
# observing the car's tracking behaviour. Run with `--chase-only`.
CHASE_ONLY = "--chase-only" in sys.argv


# ── Components ─────────────────────────────────────────
print("=" * 55)
print("  SG2002 REAL Pipeline — TPU + Motors + Servos")
print("=" * 55)

pp = Preprocessor(target_w=640, target_h=640, lib_path=cfg.preprocess_lib_path, use_c=True)
print(f"  Preprocessor: {pp}")

infer = TPUInference(model_path=cfg.model_path, conf_threshold=cfg.conf_threshold,
                     nms_iou=cfg.nms_iou_threshold, class_labels=cfg.class_labels,
                     c_lib_path=cfg.preprocess_lib_path)
print(f"  Inference: {infer}  C NMS: {infer.using_c_nms}")

pos = PositionAnalyzer(frame_w=640, frame_h=640)

# Motor
motor = create_motor_driver(mode="tt_pid", port=MOTOR_PORT)
if motor:
    print(f"  Motor: {type(motor).__name__} @ {MOTOR_PORT}")
else:
    print(f"  Motor: FAILED — check {MOTOR_PORT}")
    sys.exit(1)

# Servo
servo = create_servo(driver=ARM_DRIVER, port=ARM_PORT)
print(f"  Servo: {type(servo).__name__} @ {ARM_PORT}")

# Camera
camera = RawYUYVSource(binary_path="/guest/linux/2.camera", width=640, height=480)
print(f"  Camera: {camera}")

mode_str = "CHASE-ONLY (no grab)" if CHASE_ONLY else "FULL (chase + grab)"
print("=" * 55)
print(f"  READY [{mode_str}] — Press Ctrl+C to stop")
print("=" * 55)


# ── Simple proportional tracking ───────────────────────
# Direct motor control based on detection position (no full FSM for now).
#
# The horizontal error (ball center vs frame center) drives a proportional
# turn. SIGN CONVENTION — SETTLED OBJECTIVELY on the real car (2026-08-20):
#   * set_speeds(arg1, arg2): arg1 = physical LEFT wheel, arg2 = physical
#     RIGHT wheel (matches the ESP32's natural left/right naming).
#   * Positive speed value = FORWARD, negative = BACKWARD.
# So to command the car:  arg1 = +fwd + turn, arg2 = +fwd - turn, where
# fwd>0 = forward and turn>0 = turn right (clockwise, toward a ball on the right).
#
# Evidence (2026-08-20, three mutually-consistent observations — UNIQUE solution):
#   * rot_probe: set(-15,+15) → car turned LEFT; set(+15,-15) → turned RIGHT.
#   * chase run: arg≈(-25,-25) → car backed up (ball receded) → negative=backward.
#   * chase run: ball on RIGHT → car turned RIGHT while backing (toward ball).
# Together they pin down arg1=LEFT, arg2=RIGHT, positive=forward. The earlier
# "arg1=RIGHT, positive=backward" (commit feefe9a) was WRONG — it misread both
# rot_probe's noisy cx drift and motor_direction_test's "positive=backward"
# (a viewpoint trap: the operator watched the car from the front).
#
# The camera IMAGE is NOT mirrored (verified 2026-08-20 TPU probe: ball held to
# the car's LEFT → cx≈0.34, image-left). So image-x maps directly to physical-x.

SEARCH_SPEED  = 20     # in-place rotation speed while searching
BASE_FAR      = 35     # straight-line speed when ball is far
BASE_MID      = 22     # straight-line speed when ball is mid
TURN_GAIN_FAR = 0.06   # proportional turn strength when far
TURN_GAIN_MID = 0.05   # proportional turn strength when mid
DEAD_ZONE     = 25     # |error| in px below which the car drives straight

# ── Safety watchdog ─────────────────────────────────────
# Camera DQBUF occasionally hangs (seen 2026-08-20): get_frame() blocks forever
# and the motors keep running the LAST command (e.g. rotating in place while
# searching) with nobody driving them. A daemon thread watches how long it's
# been since the main loop last completed a set_speeds(), and brakes hard if
# the loop goes silent for WATCHDOG_TIMEOUT s.
WATCHDOG_TIMEOUT  = 3.0   # s of no completed loop → emergency brake
WATCHDOG_INTERVAL = 0.5   # watchdog check period
_watchdog_last_ok = time.time()
_watchdog_fired   = False
_stop_flag        = threading.Event()


def clamp(v, lo=-100, hi=100):
    return max(lo, min(hi, int(v)))


def compute_motor_speeds(result):
    """
    Simple tracking:
      - If no target → search (rotate in place)
      - If target left → turn left
      - If target right → turn right
      - If target centered → approach straight
      - If target very close → grab (or stop, in chase-only mode)

    Returns (action, left_speed, right_speed, arm_cmd, error_px).
    error_px > 0 means the ball is right of frame center.
    """
    if not result.has_target:
        # Search: rotate in place. arg1=+ (LEFT wheel forward) + arg2=-
        # (RIGHT wheel backward) => left-fwd + right-back = clockwise (RIGHT)
        # spin. Direction is arbitrary (just keep turning).
        return "SEARCH", SEARCH_SPEED, -SEARCH_SPEED, "", 0

    # Has target → track it
    cx = result.center_x   # normalized 0..1 (PositionResult.center_x / frame_w)
    distance = result.distance  # "near" / "mid" / "far"

    # Horizontal error from center (normalized → pixel, frame width = 640).
    # Camera image is NOT mirrored (TPU probe 2026-08-20: ball physical LEFT →
    # cx≈0.34): image-left = car-left. So err>0 = ball physically RIGHT.
    error = int(cx * 640) - 320   # -320 .. +320, + = ball physically RIGHT

    if distance == "near":
        # Close enough → stop and grab (or just stop in chase-only mode)
        if CHASE_ONLY:
            return "NEAR_STOP", 0, 0, "", error
        return "GRAB", 0, 0, "grab", error

    # Proportional turn. Dead zone suppresses micro-steering near center.
    if abs(error) <= DEAD_ZONE:
        turn = 0
    elif distance == "mid":
        turn = int(error * TURN_GAIN_MID)
    else:
        turn = int(error * TURN_GAIN_FAR)

    fwd = BASE_MID if distance == "mid" else BASE_FAR
    # Verified mapping (2026-08-20): arg1 = LEFT wheel, arg2 = RIGHT wheel,
    # + = forward. forward → positive base; turn>0 (ball right) → LEFT wheel
    # faster than RIGHT → car turns right (clockwise), toward the ball.
    left_arg  = clamp(+fwd + turn)   # arg1 → physical LEFT wheel
    right_arg = clamp(+fwd - turn)   # arg2 → physical RIGHT wheel
    action = "APPROACH" if distance == "mid" else "CHASE"
    return action, left_arg, right_arg, "", error


# ── Main loop ──────────────────────────────────────────
fid, fps_window = 0, []
grabbed = False   # True once the ball is held — stop chasing and hold it

def shutdown(signum=None, frame=None):
    elapsed = time.time() - t_start
    avg_fps = fid / elapsed if elapsed > 0 and fid > 0 else 0
    print(f"\n{'=' * 55}")
    print(f"  {fid} frames in {elapsed:.1f}s  avg {avg_fps:.1f} fps")
    print(f"{'=' * 55}")
    motor.brake()
    time.sleep(0.1)
    _stop_flag.set()          # stop the watchdog thread
    motor.close()
    servo.close()
    camera.close()
    infer.close()
    print("Shutdown complete.")
    sys.exit(0)

signal.signal(signal.SIGINT, shutdown)
signal.signal(signal.SIGTERM, shutdown)

t_start = time.time()


def _watchdog_loop():
    """Brake if the main loop stalls (camera hang / TPU wedge)."""
    global _watchdog_fired
    while not _stop_flag.is_set():
        if not grabbed and time.time() - _watchdog_last_ok > WATCHDOG_TIMEOUT:
            # Main loop is blocked on a frame — the last motor command is still
            # being driven (worst case: spinning in place during SEARCH).
            if not _watchdog_fired:
                print(f"[WATCHDOG] {WATCHDOG_TIMEOUT:.0f}s with no completed frame — "
                      f"emergency brake (camera/infer likely hung)")
                _watchdog_fired = True
            motor.brake()
        time.sleep(WATCHDOG_INTERVAL)


threading.Thread(target=_watchdog_loop, daemon=True, name="motor-watchdog").start()

try:
    while True:
        if grabbed:
            # Ball already held — keep the car stopped and the gripper
            # closed, and do NOT pull frames (the camera subprocess just
            # blocks on write instead of backing up DQBUF).
            motor.set_speeds(0, 0)
            _watchdog_last_ok = time.time()   # held state is "alive" too
            _watchdog_fired   = False
            time.sleep(0.05)
            continue

        t_frame = time.time()

        # 1. Camera
        yuyv_bytes, w, h = camera.get_frame()

        # 2. Preprocess
        planar = pp.process_bgr_bytes(yuyv_bytes, w, h)

        # 3. TPU + NMS
        dets = infer.infer(planar)
        tpu_ms, nms_ms = infer.last_timing

        # 4. Position analysis
        result = pos.analyze(dets)

        # 5. Motor/Servo control
        action, left, right, arm, error = compute_motor_speeds(result)
        motor.set_speeds(left, right)
        _watchdog_last_ok = time.time()   # completed one full loop → re-arm
        _watchdog_fired   = False

        if arm == "grab":
            # Stop, then run the full grab sequence once (blocks ~4s).
            motor.brake()
            print("  >>> GRAB: braking, executing grab sequence ...")
            servo.grab()
            grabbed = True
            print("  >>> GRABBED — holding ball. Ctrl+C to stop.")
            continue

        # ── Log ──────────────────────────────────────
        total_ms = (time.time() - t_frame) * 1000
        fps_window.append(1000 / total_ms if total_ms > 0 else 0)
        if len(fps_window) > 30:
            fps_window.pop(0)
        fid += 1

        if fid % 30 == 0 or dets:
            avg_fps = sum(fps_window) / len(fps_window)
            if dets:
                det_str = (f"conf={result.target_confidence:.2f} "
                           f"dist={result.distance:4s} size={result.size_ratio:.3f} "
                           f"cx={result.center_x:.2f} err={error:+d}")
            else:
                det_str = "none"
            print(f"  [{fid:04d}] {det_str:47s}  "
                  f"action={action:9s}  motor=({left:4d},{right:4d})  "
                  f"tpu={tpu_ms:.0f}ms  total={total_ms:.0f}ms  fps={avg_fps:.1f}")

except EOFError:
    print("Camera ended")
finally:
    shutdown()
