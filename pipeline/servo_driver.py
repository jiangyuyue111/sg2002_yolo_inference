"""
servo_driver.py — Robotic arm servo controller.

Ported from AKA-00 src/arm_control/zl/zp10s/uart_control.py
and src/arm_control/sts3215/__init__.py.

Supports two servo protocols:
  - ZP10S: ASCII command protocol (#001P1500T1000!)
  - STS3215: Dynamixel-compatible binary protocol

Both have mock implementations for PC testing.
"""

import os
import sys
import time


# ═══════════════════════════════════════════════════════════════════════
# Mock — for PC testing
# ═══════════════════════════════════════════════════════════════════════

class MockServo:
    """Mock servo for PC testing."""

    def __init__(self):
        self._angles = {i: 150 for i in range(4)}  # default angles
        self._last_action = ""

    def set_angle(self, servo_id: int, angle: float) -> None:
        self._angles[servo_id] = angle
        self._last_action = f"set_angle({servo_id}, {angle})"

    def get_angle(self, servo_id: int) -> float:
        return self._angles.get(servo_id, 0.0)

    def grab(self) -> None:
        self._last_action = "grab"
        self._angles[2] = 88   # close gripper (calibrated 08-18: ~1478 pulse)

    def release(self) -> None:
        self._last_action = "release"
        self._angles[2] = 135  # open gripper (calibrated 08-18: ~2000 pulse)

    def restore_torque(self) -> None:
        self._last_action = "restore_torque"

    @property
    def last_action(self) -> str:
        return self._last_action


# ═══════════════════════════════════════════════════════════════════════
# ZP10S — ASCII protocol
# ═══════════════════════════════════════════════════════════════════════

class ZP10S:
    """
    ZP10S servo via UART (ASCII protocol).

    Command format: #<ID:03d>P<pulse:04d>T<time:04d>!
    Pulse range: 500-2500 (maps to 0-270 degrees)
    """

    def __init__(self, port: str = "/dev/ttyS2", baudrate: int = 115200):
        self._port = port
        self._baudrate = baudrate
        self._ser = None
        self._fd = None
        self._is_raw_fd = False
        # Angles in degrees on the correct 0-180 scale (see PULSE_RANGE_DEG).
        # These are the AKA-00 defaults re-expressed so the emitted pulse
        # widths match the reference robot (old /270 scale under-rotated
        # every angle to 2/3).
        #
        # servo2 (gripper) is now calibrated on the real arm (08-18): its
        # mechanical range is ~1462 (fully closed) .. ~2023 (fully open)
        # pulse, far narrower than the 500-2500 servo range. Higher pulse =
        # open, lower pulse = close. We command 2000/1478 with margin so the
        # fingers never stall against the end stops. servo0/servo1 still use
        # AKA-00 defaults and MUST be re-calibrated — see servo_calibrate.py.
        self._angles = {
            "servo0_prepare": 163, "servo1_prepare": 120, "servo2_prepare": 135,
            "servo2_approach": 135,
            "servo2_grab": 88, "servo0_lift": 133, "servo1_lift": 120,
            "servo2_lift": 88,
        }

        # Prefer pyserial (PC). On the board (no pyserial) fall back to raw fd
        # + termios so the servo actually sends — this is what was missing.
        try:
            import serial
            self._ser = serial.Serial(
                port=port, baudrate=baudrate,
                bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE, timeout=0.1,
            )
            time.sleep(0.3)
        except ImportError:
            self._open_raw()
        except Exception as e:
            print(f"[ZP10S] pyserial open failed ({e}); trying raw fd")
            self._open_raw()

        # Defensive: clear any torque-off state left from a previous run.
        self.restore_torque()

    def _open_raw(self) -> None:
        """Board fallback: raw POSIX fd + termios baudrate (no pyserial).

        ZP10S is strictly 115200; the kernel tty default is 38400, so the
        baudrate MUST be set explicitly (same root cause as the 08-13 fix).
        """
        try:
            import termios
            import fcntl
            self._fd = os.open(self._port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)

            attrs = termios.tcgetattr(self._fd)
            attrs[3] = attrs[3] & ~(termios.ECHO | termios.ICANON | termios.ISIG)
            baud_map = {
                9600: termios.B9600, 19200: termios.B19200, 38400: termios.B38400,
                57600: termios.B57600, 115200: termios.B115200,
            }
            baud_const = baud_map.get(self._baudrate, termios.B115200)
            attrs[4] = baud_const   # ispeed
            attrs[5] = baud_const   # ospeed
            termios.tcsetattr(self._fd, termios.TCSANOW, attrs)

            flags = fcntl.fcntl(self._fd, fcntl.F_GETFL)
            fcntl.fcntl(self._fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)

            self._is_raw_fd = True
            print(f"[ZP10S] Raw fd opened on {self._port} @ {self._baudrate} baud (termios)")
        except Exception as e:
            print(f"[ZP10S] Raw open failed: {e}")
            self._fd = None
            self._is_raw_fd = False

    def _write(self, data: bytes) -> None:
        """Write a command to the servo (pyserial or raw fd)."""
        if self._is_raw_fd and self._fd is not None:
            try:
                os.write(self._fd, data)
            except Exception:
                pass
        elif self._ser is not None:
            try:
                self._ser.write(data)
                self._ser.flush()
            except Exception:
                pass

    # ZP10S pulse range 500-2500 spans 0-180 degrees (1500 ≈ 90°, per the
    # manual — NOT 0-270). The old /270.0 scale was wrong and turned every
    # angle to 2/3 of its intended position, which is why 90° came out as
    # pulse 1166 instead of 1500.
    PULSE_RANGE_DEG = 180.0

    def _angle_to_pulse(self, angle: float) -> int:
        """Map angle 0-180 to pulse width 500-2500."""
        pulse = int(500 + (angle / self.PULSE_RANGE_DEG) * 2000)
        return max(500, min(2500, pulse))

    def set_angle(self, servo_id: int, angle: float, move_time: int = 1000) -> None:
        """Set servo angle. angle in degrees, move_time in ms.

        Command format: #<id:03d>P<pulse:04d>T<time:04d>!  ('!' is the
        terminator — no newline, matching AKA-00 and the shell test.)
        """
        pulse = self._angle_to_pulse(angle)
        cmd = f"#{servo_id:03d}P{pulse:04d}T{move_time:04d}!"
        self._write(cmd.encode('ascii'))

    def restore_torque(self) -> None:
        """Restore torque on all three servos.

        Defends against the released / torque-off state that stray bytes can
        trigger (PULK). If a servo silently ignores commands, this is the
        first thing to try (see 08-14 note).
        """
        for sid in (0, 1, 2):
            self._write(f"#{sid:03d}PULR!".encode("ascii"))

    def grab(self) -> None:
        """Execute the full grab sequence: open → reach → close → lift.

        Ported from AKA-00 src/arm_control/zl/zp10s/uart_control.py grab().
        servo0 = base, servo1 = shoulder, servo2 = gripper.
        Blocks ~4s total (the servo move times are on the device).
        """
        a = self._angles
        # 1. Open gripper
        self.set_angle(2, a["servo2_prepare"], 1000)
        time.sleep(0.5)
        # 2. Reach down: base + shoulder + gripper to approach
        self.set_angle(0, a["servo0_prepare"], 1000)
        self.set_angle(1, a["servo1_prepare"], 1000)
        self.set_angle(2, a["servo2_approach"], 1000)
        time.sleep(1.0)
        # 3. Close gripper on the ball
        self.set_angle(2, a["servo2_grab"], 1000)
        time.sleep(1.5)
        # 4. Lift arm
        self.set_angle(0, a["servo0_lift"], 1000)
        self.set_angle(1, a["servo1_lift"], 1000)
        self.set_angle(2, a["servo2_lift"], 1000)

    def release(self) -> None:
        """Open the gripper (return arm to ready)."""
        self.set_angle(2, self._angles["servo2_prepare"], 1000)

    def close(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except Exception:
                pass
            self._fd = None
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None


# ═══════════════════════════════════════════════════════════════════════
# STS3215 — Dynamixel-compatible binary protocol
# ═══════════════════════════════════════════════════════════════════════

class STS3215:
    """
    STS3215 servo via UART (Dynamixel-compatible protocol).

    Uses 0xFF 0xFF header + packet structure.
    Position range: 0-4095 (maps to 0-360 degrees)
    """

    def __init__(self, port: str = "/dev/ttyACM0", baudrate: int = 115200):
        self._port = port
        self._ser = None
        self._angles = {
            "servo1_prepare": 2300, "servo2_prepare": 2100,
            "servo3_prepare": 4000,
            "servo1_enter": 1850, "servo2_enter": 2650,
            "servo3_enter": 4000,
            "servo3_grab": 3000,
            "servo1_lift": 2300, "servo2_lift": 2100,
            "servo3_lift": 3000,
        }

        try:
            import serial
            self._ser = serial.Serial(
                port=port, baudrate=baudrate, timeout=0.1,
            )
            self._ser.flushInput()
            self._ser.flushOutput()
        except ImportError:
            self._ser = None
        except Exception as e:
            print(f"[STS3215] Cannot open {port}: {e}")
            self._ser = None

    def _checksum(self, data: bytes) -> int:
        return (~sum(data)) & 0xFF

    def _send_packet(self, servo_id: int, instruction: int, params: bytes):
        """Send a Dynamixel-compatible packet."""
        length = len(params) + 2
        pkt = bytearray([0xFF, 0xFF, servo_id, length, instruction])
        pkt += params
        pkt.append(self._checksum(pkt[2:]))
        if self._ser:
            try:
                self._ser.flushInput()
                self._ser.write(pkt)
                time.sleep(0.005)
            except Exception:
                pass

    def set_position(self, servo_id: int, pos: int) -> None:
        """Set servo position (0-4095)."""
        pos = max(0, min(4095, int(pos)))
        data = pos.to_bytes(2, 'little')
        self._send_packet(servo_id, 0x03, bytes([0x2A]) + data)

    def set_angle(self, servo_id: int, angle: float) -> None:
        """Set servo angle in degrees."""
        pos = int((angle / 360.0) * 4095)
        self.set_position(servo_id, pos)

    def grab(self) -> None:
        """Execute grab sequence."""
        self.set_position(3, self._angles["servo3_prepare"])
        self.set_position(2, self._angles["servo2_prepare"])
        self.set_position(1, self._angles["servo1_prepare"])
        time.sleep(0.4)
        self.set_position(1, self._angles["servo1_enter"])
        self.set_position(2, self._angles["servo2_enter"])
        time.sleep(1.0)
        self.set_position(3, self._angles["servo3_grab"])
        time.sleep(1.0)
        self.set_position(1, self._angles["servo1_lift"])
        self.set_position(2, self._angles["servo2_lift"])

    def release(self) -> None:
        """Release the gripper."""
        self.set_position(1, self._angles["servo1_lift"])
        time.sleep(0.5)
        self.set_position(3, self._angles["servo3_prepare"])

    def close(self) -> None:
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════════════
# Factory
# ═══════════════════════════════════════════════════════════════════════

def create_servo(driver: str = "zp10s",
                  port: str = "/dev/ttyS2",
                  baudrate: int = 115200):
    """
    Create a servo driver.

    Args:
        driver: "zp10s", "sts3215", or "mock".
        port: serial port path.
        baudrate: serial baudrate.
    """
    if driver == "mock" or os.name == "nt" or sys.platform == "darwin":
        return MockServo()

    if driver == "zp10s":
        return ZP10S(port=port, baudrate=baudrate)
    elif driver == "sts3215":
        return STS3215(port=port, baudrate=baudrate)
    else:
        return MockServo()
