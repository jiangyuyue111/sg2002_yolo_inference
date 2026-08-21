"""
controller.py — Control command output.

Current phase: dry-run mode — prints commands to stdout.
Future phase: send commands over serial/UART to car + robotic arm.

The controller receives a PositionResult and emits the corresponding
car and arm commands. In dry_run mode, it only logs. When dry_run is
False, it sends actual signals to the hardware.
"""

import time
import json
from typing import Optional

from .position import PositionResult, Command


# ═══════════════════════════════════════════════════════════════════════
# Controller
# ═══════════════════════════════════════════════════════════════════════

class Controller:
    """
    Execute control commands for car and robotic arm.

    Two modes:
        dry_run=True  → print commands to console (current phase)
        dry_run=False → send commands over serial/GPIO (future phase)

    Usage:
        ctrl = Controller(dry_run=True)
        ctrl.execute(position_result)
    """

    def __init__(self, dry_run: bool = True,
                 serial_port: str = "/dev/ttyS0",
                 serial_baud: int = 115200):
        self.dry_run = dry_run
        self._serial_port = serial_port
        self._serial_baud = serial_baud
        self._serial: Optional[object] = None
        self._prev_car_cmd: str = ""
        self._prev_arm_cmd: str = ""
        self._cmd_count: int = 0

    def execute(self, result: PositionResult) -> dict:
        """
        Execute the commands from a PositionResult.

        Args:
            result: analyzed position with car/arm commands.

        Returns:
            dict with execution info (for logging).
        """
        car_cmd = result.car_command
        arm_cmd = result.arm_command

        # Only log if command changed (reduce noise)
        changed = (car_cmd != self._prev_car_cmd or arm_cmd != self._prev_arm_cmd)
        self._prev_car_cmd = car_cmd
        self._prev_arm_cmd = arm_cmd
        self._cmd_count += 1

        if self.dry_run:
            return self._execute_dry(car_cmd, arm_cmd, result, changed)
        else:
            return self._execute_real(car_cmd, arm_cmd)

    def _execute_dry(self, car: str, arm: str,
                     result: PositionResult, changed: bool) -> dict:
        """Dry run — print commands with frame context."""
        # Always print on change, otherwise throttle
        if changed:
            print(f"\n{'─'*60}")
            print(f"  FRAME #{self._cmd_count}")
            if result.has_target:
                d = result.all_detections[0] if result.all_detections else None
                print(f"  Detect: {result.target_class} "
                      f"conf={result.target_confidence:.3f}")
                print(f"  Position: ({result.center_x:.2f}, {result.center_y:.2f}) "
                      f"zone={result.zone} dist={result.distance}")
                if d:
                    print(f"  BBox: ({d.x1:.0f},{d.y1:.0f}) → "
                          f"({d.x2:.0f},{d.y2:.0f}) "
                          f"size={d.width:.0f}×{d.height:.0f}")
            else:
                print(f"  Detect: NONE")
            print(f"  → CAR:  {car}")
            print(f"  → ARM:  {arm}")
            print(f"{'─'*60}")

        return {
            "frame": self._cmd_count,
            "car": car, "arm": arm,
            "changed": changed,
            "has_target": result.has_target,
            "zone": result.zone,
        }

    def _execute_real(self, car: str, arm: str) -> dict:
        """Send commands to real hardware over serial (raw file I/O, no deps)."""
        try:
            cmd = json.dumps({"car": car, "arm": arm}) + "\n"
            self._get_serial().write(cmd.encode())
        except Exception as e:
            import sys
            print(f"[CTRL] Serial error: {e}", file=sys.stderr)
        return {"car": car, "arm": arm}

    def _get_serial(self):
        """Lazy-open serial port via raw file I/O (no pyserial on board)."""
        if self._serial is None:
            import os
            # Configure serial port (raw binary, non-blocking)
            fd = os.open(self._serial_port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
            self._serial = open(fd, 'wb', buffering=0)
        return self._serial

    def close(self):
        """Send stop command and release resources."""
        if not self.dry_run:
            try:
                self._execute_real(Command.STOP, Command.ARM_HOME)
            except Exception:
                pass
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def __repr__(self):
        mode = "dry_run" if self.dry_run else f"real({self._serial_port})"
        return f"Controller(mode={mode})"


# ═══════════════════════════════════════════════════════════════════════
# Command protocol (for future hardware integration)
# ═══════════════════════════════════════════════════════════════════════
#
# When dry_run=False, commands are sent as JSON lines over serial:
#   {"car": "FORWARD", "arm": "ARM_READY", "ts": 123456789}\n
#
# Car command set:
#   STOP, FORWARD, FORWARD_SLOW, BACKWARD,
#   TURN_LEFT, TURN_RIGHT, TURN_LEFT_SLOW, TURN_RIGHT_SLOW,
#   SEARCH_LEFT, SEARCH_RIGHT
#
# Arm command set:
#   ARM_READY, ARM_GRIP, ARM_RELEASE, ARM_HOME
#
# Composite commands are expanded by the controller:
#   APPROACH_AND_GRIP → car=FORWARD_SLOW + arm=ARM_GRIP
#   SEARCH_MODE        → car=SEARCH_LEFT  + arm=ARM_HOME
# ═══════════════════════════════════════════════════════════════════════

COMMAND_PROTOCOL_VERSION = "1.0"
