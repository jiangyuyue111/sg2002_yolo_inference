#!/usr/bin/env python3
"""servo_gripper_diag.py — 夹爪(ID=2) 释力/故障诊断。

查 PVER/PRTV/PRAD，多次 PULR 强制恢复扭力，再单步 P 移动读回。
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

print("=== DIAG ID=2 ===")
print("PVER:", repr(q(b"#002PVER!")))
print("PRTV:", repr(q(b"#002PRTV!")))
print("PRAD:", repr(q(b"#002PRAD!")))

# 多次 PULR 强制恢复扭力
for i in range(5):
    send(b"#002PULR!")
    time.sleep(0.25)

time.sleep(0.5)
print("after 5x PULR, PRAD:", repr(q(b"#002PRAD!")))

# 单步移动 + 读回
for p in (1700, 1500):
    print(">> P%04d (watch gripper!)" % p)
    send(b"#002P%04dT800!" % p)
    time.sleep(1.5)
    print("   PRAD:", repr(q(b"#002PRAD!")))

print("DIAG" + chr(95) + "OK")
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

    ser.write(("cat > /tmp/sgd.py <<'PYEOF'\n" + SCRIPT + "\nPYEOF\n").encode())
    time.sleep(1.0)
    ser.reset_input_buffer()
    ser.write(b"PYTHONHOME=/ PYTHONPATH=/ LD_PRELOAD=/lib/libffi.so python3 -u /tmp/sgd.py\n")
    time.sleep(0.5)

    out = b""
    deadline = time.time() + 30.0
    while time.time() < deadline:
        if ser.in_waiting:
            out += ser.read(ser.in_waiting)
            if b"DIAG_OK" in out or b"Traceback" in out:
                break
        time.sleep(0.1)

    txt = out.decode("utf-8", errors="replace")
    for line in txt.splitlines():
        s = line.strip()
        if s and ("PVER" in s or "PRTV" in s or "PRAD" in s or "PULR" in s
                  or ">>" in s or "DIAG" in s or "Traceback" in s or "===" in s):
            print(s)
    ser.close()


if __name__ == "__main__":
    main()
