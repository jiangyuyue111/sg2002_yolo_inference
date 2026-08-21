"""
motor_debug.py — ESP32-C3 电机串口诊断 (Windows/Linux 双平台)

用法:
  PC端:   python motor_debug.py COM7
  板端:   PYTHONPATH=/ python3 /pipeline/motor_debug.py

逐步诊断:
  Test 1: 串口存在 + 打开
  Test 2: ESP32 握手 (INIT→ACK, CONFIG→ACK, 含 payload 回显校验)
  Test 3: 状态查询 (GET_STATUS, CMD_DIAG 编码器/ISR)
  Test 4: 电机转动 (SET_SPEEDS + GET_RPM)
  Test 5: 停止/刹车 + RESET
"""

import os
import sys
import time
import struct
import select

PORT = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyS1"
BAUD = 115200

# ── Protocol ──────────────────────────────────────────
FRAME_H1, FRAME_H2 = 0xAA, 0x55

CMD_INIT       = 0x01
CMD_CONFIG     = 0x02
CMD_SET_SPEED  = 0x10
CMD_STOP       = 0x11
CMD_BRAKE      = 0x12
CMD_SET_SPEEDS = 0x13
CMD_GET_RPM    = 0x20
CMD_GET_STATUS = 0x21
CMD_GET_ENCODER= 0x22
CMD_DIAG       = 0x30
CMD_RESET      = 0xFF

RSP_ACK        = 0x80
RSP_NACK       = 0x81
RSP_RPM_DATA   = 0x90
RSP_STATUS     = 0x91

ERR_WRONG_STATE   = 0x01
ERR_BAD_CHECKSUM  = 0x02
ERR_INVALID_PARAM = 0x03
ERR_UNKNOWN_CMD   = 0x04

ERR_NAMES = {0x01:"WRONG_STATE", 0x02:"BAD_CHECKSUM", 0x03:"INVALID_PARAM", 0x04:"UNKNOWN_CMD"}
STATE_NAMES = {0:"UNINIT", 1:"IDLE", 2:"READY", 3:"RUNNING", 4:"ERROR"}

PASS = "[PASS]"
FAIL = "[FAIL]"
WARN = "[WARN]"


# ── Serial I/O (pyserial first, raw termios fallback) ──

_ser = None        # pyserial Serial object (Windows/PC)
_ser_fd = None     # raw integer fd (Linux board fallback)


def open_serial(port, baud):
    """Open serial port. Tries pyserial first, falls back to raw termios."""
    global _ser, _ser_fd

    # ── Path A: pyserial (cross-platform) ──
    try:
        import serial
        s = serial.Serial(port, baud, timeout=0.1)
        s.dtr = False
        s.rts = False
        time.sleep(0.3)
        s.reset_input_buffer()
        _ser = s
        _ser_fd = None
        return True
    except ImportError:
        pass
    except Exception as e:
        print(f"  [WARN] pyserial open failed: {e}, trying raw...")

    # ── Path B: raw termios (Linux board only) ──
    try:
        import termios, fcntl
        fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        attrs = termios.tcgetattr(fd)
        attrs[3] &= ~(termios.ECHO | termios.ICANON | termios.ISIG)
        baud_map = {9600: termios.B9600, 19200: termios.B19200,
                    38400: termios.B38400, 57600: termios.B57600,
                    115200: termios.B115200, 230400: termios.B230400}
        b = baud_map.get(baud, termios.B115200)
        attrs[4] = attrs[5] = b
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)
        _ser_fd = fd
        _ser = None
        return True
    except Exception as e:
        print(f"  [FAIL] raw open failed: {e}")
        return False


def close_serial():
    global _ser, _ser_fd
    if _ser is not None:
        try:
            _ser.close()
        except Exception:
            pass
        _ser = None
    if _ser_fd is not None:
        try:
            os.close(_ser_fd)
        except Exception:
            pass
        _ser_fd = None


def drain():
    """Drain any pending input data."""
    if _ser is not None:
        _ser.reset_input_buffer()
    elif _ser_fd is not None:
        while True:
            r, _, _ = select.select([_ser_fd], [], [], 0.01)
            if not r:
                break
            try:
                if not os.read(_ser_fd, 256):
                    break
            except BlockingIOError:
                break


def read_exact(n, timeout=0.2):
    """Read exactly n bytes."""
    if _ser is not None:
        deadline = time.time() + timeout
        buf = b""
        while len(buf) < n:
            rem = deadline - time.time()
            if rem <= 0:
                break
            _ser.timeout = min(0.05, rem)
            chunk = _ser.read(n - len(buf))
            if not chunk:
                break
            buf += chunk
        return buf
    elif _ser_fd is not None:
        deadline = time.time() + timeout
        buf = b""
        while len(buf) < n:
            rem = deadline - time.time()
            if rem <= 0:
                break
            r, _, _ = select.select([_ser_fd], [], [], min(0.05, rem))
            if not r:
                continue
            try:
                chunk = os.read(_ser_fd, n - len(buf))
            except BlockingIOError:
                continue
            if not chunk:
                break
            buf += chunk
        return buf
    return b""


def write_serial(data):
    """Write bytes to serial."""
    if _ser is not None:
        _ser.write(data)
        _ser.flush()
    elif _ser_fd is not None:
        os.write(_ser_fd, data)


def build_frame(cmd, payload=b""):
    chk = cmd ^ len(payload)
    for b in payload: chk ^= b
    return bytes([FRAME_H1, FRAME_H2, cmd, len(payload)]) + payload + bytes([chk & 0xFF])


def recv_frame(timeout=0.5):
    """Parse one protocol frame. Returns {cmd, payload, chk_ok} or None."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        b = read_exact(1, timeout=0.1)
        if not b or b[0] != FRAME_H1: continue
        b = read_exact(1, timeout=0.1)
        if not b or b[0] != FRAME_H2: continue
        hdr = read_exact(2, timeout=0.1)
        if len(hdr) < 2: continue
        cmd, length = hdr[0], hdr[1]
        payload = read_exact(length, timeout=0.1) if length else b""
        if len(payload) < length: continue
        chk_b = read_exact(1, timeout=0.1)
        if not chk_b: continue
        chk = cmd ^ length
        for pb in payload: chk ^= pb
        return {"cmd": cmd, "payload": bytes(payload), "chk_ok": chk == chk_b[0]}
    return None


def send_recv(cmd, payload=b"", timeout=0.5):
    drain()
    write_serial(build_frame(cmd, payload))
    return recv_frame(timeout)


def check_ack(rsp, sent_cmd):
    """Verify ACK response: cmd=0x80, chk OK, payload[0] echoes sent_cmd."""
    if rsp is None: return False, "no response"
    if not rsp.get("chk_ok", True): return False, "checksum BAD"
    if rsp["cmd"] == RSP_NACK:
        err = rsp["payload"][1] if len(rsp.get("payload", b"")) >= 2 else "?"
        return False, f"NACK({ERR_NAMES.get(err, f'0x{err:02X}')})"
    if rsp["cmd"] != RSP_ACK: return False, f"cmd=0x{rsp['cmd']:02X} (not ACK)"
    if len(rsp.get("payload", b"")) < 1 or rsp["payload"][0] != sent_cmd:
        return False, f"ACK payload[0]=0x{rsp['payload'][0]:02X} != 0x{sent_cmd:02X}"
    return True, ""


# ── Tests ─────────────────────────────────────────────

passed = 0
failed = 0

def test(name, ok, detail=""):
    global passed, failed
    if ok:
        print(f"  {PASS} {name}" + (f"  ({detail})" if detail else ""))
        passed += 1
    else:
        print(f"  {FAIL} {name}" + (f"  ({detail})" if detail else ""))
        failed += 1
    return ok


def test1_port():
    print(f"\n── Test 1: Serial port ──")
    ok = open_serial(PORT, BAUD)
    test("port open", ok, f"{PORT} @ {BAUD}")
    return ok


def test2_handshake():
    print(f"\n── Test 2: Handshake (INIT + CONFIG) ──")

    # INIT
    rsp = send_recv(CMD_INIT)
    ok, detail = check_ack(rsp, CMD_INIT)
    if not test("INIT → ACK", ok, detail):
        # Try other baudrates
        for baud in [9600, 38400, 57600, 230400]:
            print(f"  {WARN} retrying at {baud} baud...")
            close_serial()
            if not open_serial(PORT, baud):
                continue
            rsp = send_recv(CMD_INIT)
            ok, detail = check_ack(rsp, CMD_INIT)
            if ok:
                global BAUD
                BAUD = baud
                test(f"INIT → ACK @ {baud}", True)
                break
            test(f"INIT → ACK @ {baud}", False, detail)
        else:
            return False

    # CONFIG
    payload = struct.pack(">HH", 4680, 20000)
    rsp = send_recv(CMD_CONFIG, payload)
    ok, detail = check_ack(rsp, CMD_CONFIG)
    if not test("CONFIG → ACK", ok, detail):
        return False
    return True


def test3_status():
    print(f"\n── Test 3: Status + Diagnostics ──")

    # GET_STATUS
    rsp = send_recv(CMD_GET_STATUS)
    ok = rsp is not None and rsp["cmd"] == RSP_STATUS and len(rsp.get("payload", b"")) >= 5
    if test("GET_STATUS", ok):
        p = rsp["payload"]
        state = p[0]
        rpm1 = struct.unpack(">h", p[1:3])[0]
        rpm2 = struct.unpack(">h", p[3:5])[0]
        print(f"       state={STATE_NAMES.get(state, state)} M1={rpm1}rpm M2={rpm2}rpm")

    # CMD_DIAG (0x30) — encoder raw counts + ISR triggers
    rsp = send_recv(CMD_DIAG)
    if rsp and rsp["cmd"] == CMD_DIAG:
        p = rsp["payload"]
        if len(p) >= 16:
            m1c = struct.unpack(">l", p[0:4])[0]
            m2c = struct.unpack(">l", p[4:8])[0]
            m1i = struct.unpack(">L", p[8:12])[0]
            m2i = struct.unpack(">L", p[12:16])[0]
            test("CMD_DIAG encoder", True, f"M1 cnt={m1c} M2 cnt={m2c}")
            test("CMD_DIAG ISR", m1i > 0 or m2i > 0, f"M1 ISR={m1i} M2 ISR={m2i}")
        elif len(p) >= 8:
            m1c = struct.unpack(">l", p[0:4])[0]
            m2c = struct.unpack(">l", p[4:8])[0]
            test("CMD_DIAG encoder (old fw)", True, f"M1={m1c} M2={m2c}")
    else:
        test("CMD_DIAG supported", False, "firmware may not support 0x30")


def test4_motor_spin():
    print(f"\n── Test 4: Motor spin ──")

    # SET_SPEEDS both forward
    for speed, label in [(30, "slow"), (60, "medium"), (100, "fast")]:
        payload = struct.pack(">hh", speed, speed)
        rsp = send_recv(CMD_SET_SPEEDS, payload)
        ok, detail = check_ack(rsp, CMD_SET_SPEEDS)
        test(f"SET_SPEEDS({label} +{speed},+{speed}) → ACK", ok, detail)
        time.sleep(0.8)

    # Reverse
    payload = struct.pack(">hh", -50, -50)
    rsp = send_recv(CMD_SET_SPEEDS, payload)
    ok, detail = check_ack(rsp, CMD_SET_SPEEDS)
    test("SET_SPEEDS(reverse -50,-50) → ACK", ok, detail)
    time.sleep(0.8)

    # Turn
    payload = struct.pack(">hh", 60, -60)
    rsp = send_recv(CMD_SET_SPEEDS, payload)
    ok, detail = check_ack(rsp, CMD_SET_SPEEDS)
    test("SET_SPEEDS(turn +60,-60) → ACK", ok, detail)
    time.sleep(0.8)

    # Check RPM while spinning
    drain()
    write_serial(build_frame(CMD_GET_RPM, bytes([2])))
    r1 = recv_frame(0.3)
    r2 = recv_frame(0.3)
    if r1 and r2 and r1["cmd"] == RSP_RPM_DATA and r2["cmd"] == RSP_RPM_DATA:
        rpm1 = struct.unpack(">h", r1["payload"][1:3])[0]
        rpm2 = struct.unpack(">h", r2["payload"][1:3])[0]
        test("GET_RPM dual", rpm1 != 0 or rpm2 != 0, f"M1={rpm1} M2={rpm2}")
    else:
        test("GET_RPM dual", False, "no RPM data frames")


def test5_stop_reset():
    print(f"\n── Test 5: Stop/Brake + RESET ──")

    rsp = send_recv(CMD_STOP, bytes([2]))
    ok, detail = check_ack(rsp, CMD_STOP)
    test("STOP(both) → ACK", ok, detail)
    time.sleep(0.3)

    # Re-spin then brake
    write_serial(build_frame(CMD_SET_SPEEDS, struct.pack(">hh", 60, 60)))
    recv_frame(0.2)
    time.sleep(0.3)

    rsp = send_recv(CMD_BRAKE, bytes([2]))
    ok, detail = check_ack(rsp, CMD_BRAKE)
    test("BRAKE(both) → ACK", ok, detail)
    time.sleep(0.3)

    rsp = send_recv(CMD_RESET)
    ok, detail = check_ack(rsp, CMD_RESET)
    test("RESET → ACK", ok, detail)


# ── Main ──────────────────────────────────────────────

def main():
    global passed, failed
    print("=" * 50)
    print("  ESP32-C3 Motor Serial Diagnostic")
    print(f"  Port: {PORT}  Baud: {BAUD}")
    print("=" * 50)

    if not test1_port():
        print(f"\n{FAIL} Cannot access {PORT}. Check wiring/drivers.")
        sys.exit(1)

    try:
        if test2_handshake():
            test3_status()
            test4_motor_spin()
            test5_stop_reset()
        else:
            print(f"\n{WARN} Handshake failed — skipping motor tests")
            print("  Passive listen for 3s to check if ESP32 sends anything...")
            deadline = time.time() + 3
            while time.time() < deadline:
                data = read_exact(256, timeout=0.1)
                if data:
                    print(f"  RAW RX: {data.hex(' ')}  |  {repr(data)}")
    finally:
        # Ensure motors stop
        try:
            write_serial(build_frame(CMD_RESET))
        except Exception:
            pass
        close_serial()
        print(f"\n{'='*50}")
        print(f"  Results: {passed}/{passed+failed} passed")
        if failed:
            print(f"  {failed} FAILED — check wiring, power, and ESP32 firmware")
        print(f"{'='*50}")


if __name__ == "__main__":
    main()
