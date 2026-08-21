#!/usr/bin/env python3
"""servo_gripper_timefmt.py — 验证时间字段位数是否导致夹爪忽略指令。

对比 T800(3位) vs T1000(4位)，读回 PRAD 确认是否移动。
"""
import time
import serial as pyserial

PORT = "COM6"
BAUD = 115200

SCRIPT = r'''
import os, time, select, termios
PORT = "/dev/ttyS2"
fd = os.open(PORT, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
a = termios.tcgetattr(fd)
a[0] &= ~(termios.IGNBRK | termios.BRKINT | termios.PARMRK | termios.ISTRIP
          | termios.INLCR | termios.IGNCR | termios.ICRNL | termios.IXON)
a[1] &= ~termios.OPOST
a[2] &= ~(termios.CSIZE | termios.PARENB)
a[2] |= termios.CS8
a[3] &= ~(termios.ECHO | termios.ICANON | termios.ISIG | termios.IEXTEN)
a[4] = termios.B115200
a[5] = termios.B115200
termios.tcsetattr(fd, termios.TCSANOW, a)

def send(b):
    os.write(fd, b)

def drain():
    while True:
        r, _, _ = select.select([fd], [], [], 0.05)
        if fd not in r:
            break
        try:
            if not os.read(fd, 256):
                break
        except OSError:
            break

def read_all(timeout=0.6):
    out = b""
    end = time.time() + timeout
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.1)
        if fd in r:
            try:
                d = os.read(fd, 256)
            except OSError:
                d = b""
            if not d:
                break
            out += d
    return out

def q(cmd):
    drain()
    send(cmd)
    return read_all()

print("=== TIME-FMT TEST ID=2 ===")
print("start PRAD:", repr(q(b"#002PRAD!")))

print("-- 3-digit time T800 --")
send(b"#002P1700T800!")
time.sleep(1.5)
print("  T800  -> PRAD:", repr(q(b"#002PRAD!")))

print("-- 4-digit time T1000 --")
send(b"#002P1700T1000!")
time.sleep(1.5)
print("  T1000 -> PRAD:", repr(q(b"#002PRAD!")))

print("-- back center T1000 --")
send(b"#002P1500T1000!")
time.sleep(1.5)
print("  T1000 -> PRAD:", repr(q(b"#002PRAD!")))

print("TIMEFMT" + chr(95) + "OK")
'''


def main():
    ser = pyserial.Serial(PORT, BAUD, timeout=0.3)
    ser.reset_input_buffer()
    time.sleep(0.2)
    for c in ("echo '0x70 2' > /dev/pinmux",
              "echo '0x74 2' > /dev/pinmux",
              "stty -F /dev/ttyS2 115200 raw -echo"):
        ser.write((c + "\n").encode())
        time.sleep(0.35)
    ser.reset_input_buffer()

    ser.write(("cat > /tmp/stf.py <<'PYEOF'\n" + SCRIPT + "\nPYEOF\n").encode())
    time.sleep(1.0)
    ser.reset_input_buffer()
    ser.write(b"PYTHONHOME=/ PYTHONPATH=/ LD_PRELOAD=/lib/libffi.so python3 -u /tmp/stf.py\n")
    time.sleep(0.5)

    out = b""
    deadline = time.time() + 30.0
    while time.time() < deadline:
        if ser.in_waiting:
            out += ser.read(ser.in_waiting)
            if b"TIMEFMT_OK" in out or b"Traceback" in out:
                break
        time.sleep(0.1)

    txt = out.decode("utf-8", errors="replace")
    for line in txt.splitlines():
        s = line.strip()
        if s and ("PRAD" in s or "T800" in s or "T1000" in s or "TIMEFMT" in s
                  or "Traceback" in s or "===" in s or "start" in s or "back" in s):
            print(s)
    ser.close()


if __name__ == "__main__":
    main()
