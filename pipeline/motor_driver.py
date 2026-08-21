"""
motor_driver.py — ESP32-C3 TT Motor Chassis UART controller.

Ported from AKA-00 src/base_control/tt_pid/__init__.py.
Protocol: 0xAA 0x55 <cmd> <len> <payload...> <chk>

Supports two modes:
  - MockMotorDriver: for PC testing (prints commands, no hardware needed)
  - TtPidDriver: real UART to ESP32-C3 on the board
"""

import os
import sys
import time
import struct
from dataclasses import dataclass


# ═══════════════════════════════════════════════════════════════════════
# Protocol constants (from AKA-00 ESP32 firmware)
# ═══════════════════════════════════════════════════════════════════════

FRAME_H1 = 0xAA
FRAME_H2 = 0x55

CMD_INIT        = 0x01
CMD_CONFIG      = 0x02
CMD_SET_SPEED   = 0x10
CMD_SET_SPEEDS  = 0x13
CMD_STOP        = 0x11
CMD_BRAKE       = 0x12
CMD_GET_RPM     = 0x20
CMD_GET_STATUS  = 0x21
CMD_GET_ENCODER = 0x22
CMD_RESET       = 0xFF

RSP_ACK     = 0x80
RSP_NACK    = 0x81
RSP_RPM_DATA = 0x90
RSP_STATUS  = 0x91

# NACK error codes (from ESP32 firmware)
ERR_WRONG_STATE   = 0x01
ERR_BAD_CHECKSUM  = 0x02
ERR_INVALID_PARAM = 0x03
ERR_UNKNOWN_CMD   = 0x04
ERR_UNKNOWN       = 0xFF

ERR_NAMES = {
    0x01: "WRONG_STATE",
    0x02: "BAD_CHECKSUM",
    0x03: "INVALID_PARAM",
    0x04: "UNKNOWN_CMD",
}

# Diagnostic command (0x30): read raw encoder counts + ISR trigger counts
CMD_DIAG = 0x30

SPEED_MIN = -100
SPEED_MAX = 100


@dataclass
class MotorStatus:
    """Motor status report."""
    left_rpm: int = 0
    right_rpm: int = 0
    left_encoder: int = 0
    right_encoder: int = 0


# ═══════════════════════════════════════════════════════════════════════
# Mock — for PC testing
# ═══════════════════════════════════════════════════════════════════════

class MockMotorDriver:
    """Mock motor driver for PC testing. Logs commands instead of sending."""

    def __init__(self):
        self._last_left = 0
        self._last_right = 0
        self._log = []

    def init(self) -> bool:
        self._log.append("INIT")
        return True

    def config(self, ppr: int = 4680, pwm_freq: int = 20000) -> bool:
        self._log.append(f"CONFIG ppr={ppr} pwm={pwm_freq}")
        return True

    def set_speeds(self, left: int, right: int) -> None:
        """Set both motor speeds (-100 .. 100)."""
        l = max(SPEED_MIN, min(SPEED_MAX, int(left)))
        r = max(SPEED_MIN, min(SPEED_MAX, int(right)))
        self._last_left = l
        self._last_right = r

    def brake(self) -> None:
        self._last_left = 0
        self._last_right = 0
        self._log.append("BRAKE")

    def stop(self) -> None:
        """Coast (coast) — both motors freewheel."""
        self._last_left = 0
        self._last_right = 0
        self._log.append("STOP")

    def get_status(self) -> MotorStatus:
        """Return last set speeds (mock — no real RPM)."""
        return MotorStatus(left_rpm=self._last_left, right_rpm=self._last_right)

    def get_encoder(self) -> tuple:
        return (0, 0)

    def close(self) -> None:
        self._log.append("CLOSE")

    @property
    def last_speeds(self) -> tuple:
        return (self._last_left, self._last_right)


# ═══════════════════════════════════════════════════════════════════════
# Real — UART to ESP32-C3
# ═══════════════════════════════════════════════════════════════════════

class TtPidDriver:
    """
    Real ESP32-C3 TT motor chassis via UART.

    Frame format: 0xAA 0x55 <cmd> <len> <payload...> <chk>
    Checksum: cmd ^ len ^ payload[0] ^ ... ^ payload[last]

    v2 changes:
      - _recv_frame() properly parses ESP32 response frames (pyserial + raw fd)
      - _handshake() actually verifies ACK instead of blind True
      - set_speeds is fire-and-forget (no blocking), other commands verify ACK
    """

    def __init__(self, port: str = "/dev/ttyS1", baudrate: int = 115200,
                 ppr: int = 4680, pwm_freq: int = 20000):
        self._port = port
        self._baudrate = baudrate
        self._ppr = ppr
        self._pwm_freq = pwm_freq
        self._ser = None
        self._is_raw_fd = False
        self._fd = None  # raw integer fd (for os.read fallback)

        # Try to open serial port
        try:
            import serial
            self._ser = serial.Serial(
                port=port,
                baudrate=baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.1,
            )
            time.sleep(0.5)
            self._ser.reset_input_buffer()
        except ImportError:
            print(f"[TtPid] pyserial not installed; falling back to raw file I/O")
            self._open_raw()
        except Exception as e:
            print(f"[TtPid] Cannot open {port}: {e}; commands will be no-ops")
            self._ser = None

        if self._ser is not None:
            ok = self._handshake()
            if not ok:
                print(f"[TtPid] FATAL: ESP32 handshake FAILED — "
                      f"check wiring, power, and firmware")
                print(f"[TtPid] Motor commands will be no-ops until this is fixed")
                self._ser = None  # prevent blind sends
            else:
                print(f"[TtPid] ESP32 handshake OK — motor driver ready")

    def _open_raw(self):
        """Fallback: open serial via raw POSIX file I/O (no deps).
        Configures baudrate via termios so stty is not needed."""
        try:
            import termios
            self._fd = os.open(self._port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)

            # Set raw mode + baudrate
            attrs = termios.tcgetattr(self._fd)
            # Turn off canonical, echo
            attrs[3] = attrs[3] & ~(termios.ECHO | termios.ICANON | termios.ISIG)
            # Map baudrate
            baud_map = {
                9600: termios.B9600, 19200: termios.B19200, 38400: termios.B38400,
                57600: termios.B57600, 115200: termios.B115200,
            }
            baud_const = baud_map.get(self._baudrate, termios.B115200)
            attrs[4] = baud_const   # ispeed
            attrs[5] = baud_const   # ospeed
            termios.tcsetattr(self._fd, termios.TCSANOW, attrs)

            # Set blocking
            import fcntl
            flags = fcntl.fcntl(self._fd, fcntl.F_GETFL)
            fcntl.fcntl(self._fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)

            self._ser = os.fdopen(self._fd, 'r+b', buffering=0)
            self._is_raw_fd = True
            print(f"[TtPid] Raw FD opened on {self._port} @ {self._baudrate} baud (termios)")
        except Exception as e:
            print(f"[TtPid] Raw open failed: {e}")
            self._ser = None
            self._is_raw_fd = False
            self._fd = None

    # ── Protocol helpers ──

    def _build_frame(self, cmd: int, payload: bytes = b"") -> bytes:
        """Build protocol frame with checksum."""
        chk = cmd ^ len(payload)
        for b in payload:
            chk ^= b
        return bytes([FRAME_H1, FRAME_H2, cmd, len(payload)]) + payload + bytes([chk & 0xFF])

    def _read_exact(self, n: int, timeout: float = 0.2) -> bytes:
        """Read exactly n bytes from serial (pyserial or raw fd)."""
        if self._is_raw_fd and self._fd is not None:
            import select
            deadline = time.time() + timeout
            buf = b""
            while len(buf) < n:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                r, _, _ = select.select([self._fd], [], [], min(0.05, remaining))
                if not r:
                    continue
                try:
                    chunk = os.read(self._fd, n - len(buf))
                except BlockingIOError:
                    continue
                if not chunk:
                    break
                buf += chunk
            return buf
        else:
            # pyserial
            if hasattr(self._ser, 'read'):
                return self._ser.read(n)
            return b""

    def _bytes_available(self) -> int:
        """Return number of bytes available to read."""
        if self._is_raw_fd and self._fd is not None:
            import select
            r, _, _ = select.select([self._fd], [], [], 0)
            return 1 if r else 0  # binary: data or not
        elif hasattr(self._ser, 'in_waiting'):
            return self._ser.in_waiting
        return 0

    def _recv_frame(self, timeout: float = 0.3) -> dict | None:
        """Read one protocol frame from ESP32.

        Returns dict {cmd, payload, chk_ok} or None on timeout/error.
        Works with both pyserial and raw fd.
        """
        if self._ser is None:
            return None
        deadline = time.time() + timeout
        while time.time() < deadline:
            # Read header bytes one at a time to find sync
            b = self._read_exact(1, timeout=0.1)
            if not b or b[0] != FRAME_H1:
                continue
            b = self._read_exact(1, timeout=0.1)
            if not b or b[0] != FRAME_H2:
                continue
            # Read cmd + len
            header = self._read_exact(2, timeout=0.1)
            if len(header) < 2:
                continue
            cmd, length = header[0], header[1]
            # Read payload + checksum
            payload = self._read_exact(length, timeout=0.1) if length else b""
            if len(payload) < length:
                continue
            chk_byte = self._read_exact(1, timeout=0.1)
            if not chk_byte:
                continue

            chk_calc = cmd ^ length
            for pb in payload:
                chk_calc ^= pb
            chk_calc &= 0xFF

            return {
                "cmd": cmd,
                "payload": bytes(payload),
                "chk_ok": chk_calc == chk_byte[0],
            }
        return None

    def _drain_input(self) -> None:
        """Drain any pending input data."""
        if self._ser is None:
            return
        try:
            if hasattr(self._ser, 'reset_input_buffer'):
                self._ser.reset_input_buffer()
            elif self._is_raw_fd and self._fd is not None:
                import select
                while True:
                    r, _, _ = select.select([self._fd], [], [], 0.01)
                    if not r:
                        break
                    try:
                        data = os.read(self._fd, 256)
                    except BlockingIOError:
                        break
                    if not data:
                        break
        except Exception:
            pass

    def _send_cmd(self, cmd: int, payload: bytes = b"",
                  want_reply: bool = False, timeout: float = 0.3) -> dict | None:
        """Send command frame. If want_reply, wait for and return response frame."""
        if self._ser is None:
            return None
        try:
            frame = self._build_frame(cmd, payload)
            self._drain_input()
            if self._is_raw_fd and self._fd is not None:
                written = os.write(self._fd, frame)
            else:
                written = self._ser.write(frame)
                self._ser.flush()
            if want_reply:
                return self._recv_frame(timeout)
        except Exception as e:
            print(f"[TtPid] Send error (cmd=0x{cmd:02X}): {e}")
        return None

    def _check_ack(self, rsp: dict | None, sent_cmd: int, label: str) -> bool:
        """Verify a response is a valid ACK for the given command.

        ACK frame: cmd=0x80, payload[0]=echoed_cmd (per ESP32 firmware spec)
        NACK frame: cmd=0x81, payload=[echoed_cmd, error_code]
        """
        if rsp is None:
            print(f"[TtPid] {label}: no response from ESP32")
            return False
        if not rsp.get("chk_ok", True):
            print(f"[TtPid] {label}: checksum BAD — baudrate mismatch or noise")
            return False
        if rsp["cmd"] == RSP_NACK:
            err = ERR_UNKNOWN
            if len(rsp.get("payload", b"")) >= 2:
                err = rsp["payload"][1]
            err_name = ERR_NAMES.get(err, f"0x{err:02X}")
            print(f"[TtPid] {label}: NACK({err_name})")
            return False
        if rsp["cmd"] != RSP_ACK:
            print(f"[TtPid] {label}: expected ACK(0x80), got 0x{rsp['cmd']:02X}")
            return False
        # Verify echoed command
        if len(rsp.get("payload", b"")) < 1 or rsp["payload"][0] != sent_cmd:
            print(f"[TtPid] {label}: ACK but payload[0]={rsp['payload'][0] if rsp.get('payload') else '?'} "
                  f"≠ sent_cmd=0x{sent_cmd:02X}")
            return False
        return True

    def _handshake(self) -> bool:
        """INIT → expect ACK, then CONFIG → expect ACK. Returns True only if both ACK'd."""
        rsp = self._send_cmd(CMD_INIT, want_reply=True, timeout=0.3)
        if not self._check_ack(rsp, CMD_INIT, "INIT"):
            return False
        print("[TtPid] INIT ACK ✓")

        payload = struct.pack(">HH", self._ppr, self._pwm_freq)
        rsp = self._send_cmd(CMD_CONFIG, payload, want_reply=True, timeout=0.3)
        if not self._check_ack(rsp, CMD_CONFIG, "CONFIG"):
            return False
        print("[TtPid] CONFIG ACK ✓")
        return True

    # ── Public API ──

    def set_speeds(self, left: int, right: int) -> None:
        """Set left/right motor speeds (-100 .. 100). Fire-and-forget (no blocking)."""
        l = max(SPEED_MIN, min(SPEED_MAX, int(left)))
        r = max(SPEED_MIN, min(SPEED_MAX, int(right)))
        payload = struct.pack(">hh", l, r)
        self._send_cmd(CMD_SET_SPEEDS, payload, want_reply=False)

    def brake(self) -> None:
        """Brake both motors."""
        self._send_cmd(CMD_BRAKE, bytes([2]), want_reply=False)

    def stop(self) -> None:
        """Coast (coast) both motors."""
        self._send_cmd(CMD_STOP, bytes([2]), want_reply=False)

    def get_status(self) -> MotorStatus:
        """Read motor status (state + RPM per motor)."""
        rsp = self._send_cmd(CMD_GET_STATUS, want_reply=True, timeout=0.3)
        if rsp and rsp["cmd"] == RSP_STATUS and len(rsp.get("payload", b"")) >= 5:
            p = rsp["payload"]
            state = p[0]
            rpm1 = struct.unpack(">h", p[1:3])[0]
            rpm2 = struct.unpack(">h", p[3:5])[0]
            STATE_NAMES = {0: "UNINIT", 1: "IDLE", 2: "READY", 3: "RUNNING", 4: "ERROR"}
            print(f"[TtPid] Status: {STATE_NAMES.get(state, state)} M1={rpm1}rpm M2={rpm2}rpm")
            return MotorStatus(left_rpm=rpm1, right_rpm=rpm2)
        return MotorStatus()

    def get_diag(self) -> dict:
        """Read raw encoder counts and ISR triggers (CMD_DIAG 0x30).

        Returns dict with keys: m1_count, m2_count, m1_isr, m2_isr.
        Values are 0 if the command is unsupported by this firmware version.
        """
        rsp = self._send_cmd(CMD_DIAG, want_reply=True, timeout=0.3)
        result = {"m1_count": 0, "m2_count": 0, "m1_isr": 0, "m2_isr": 0}
        if rsp and rsp["cmd"] == CMD_DIAG:
            p = rsp["payload"]
            if len(p) >= 16:
                result["m1_count"] = struct.unpack(">l", p[0:4])[0]
                result["m2_count"] = struct.unpack(">l", p[4:8])[0]
                result["m1_isr"] = struct.unpack(">L", p[8:12])[0]
                result["m2_isr"] = struct.unpack(">L", p[12:16])[0]
            elif len(p) >= 8:
                result["m1_count"] = struct.unpack(">l", p[0:4])[0]
                result["m2_count"] = struct.unpack(">l", p[4:8])[0]
        return result

    def get_encoder(self) -> tuple:
        """Read encoder counts. Returns (0,0) if unavailable."""
        rsp = self._send_cmd(CMD_GET_ENCODER, want_reply=True, timeout=0.3)
        if rsp and len(rsp.get("payload", b"")) >= 8:
            c1 = struct.unpack(">i", rsp["payload"][0:4])[0]
            c2 = struct.unpack(">i", rsp["payload"][4:8])[0]
            return c1, c2
        return (0, 0)

    def close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None
            self._fd = None


# ═══════════════════════════════════════════════════════════════════════
# Factory
# ═══════════════════════════════════════════════════════════════════════

def create_motor_driver(mode: str = "mock",
                         port: str = "/dev/ttyS1",
                         baudrate: int = 115200):
    """
    Create a motor driver.

    Args:
        mode: "mock" for PC testing (no hardware),
              "tt_pid" for real ESP32 UART (Windows/Linux/macOS).
        port: serial port path (COM7, /dev/ttyS1, /dev/ttyUSB0, etc.).
        baudrate: serial baudrate.
    """
    if mode == "tt_pid":
        return TtPidDriver(port=port, baudrate=baudrate)
    else:
        return MockMotorDriver()
