#!/usr/bin/env python3
"""servo_gripper_cal.py — 夹爪(ID=2)开合标定。

温和步进 + PRAD 读回，确定夹爪实际开/闭对应的脉冲值。
绝不用 500/2500 极限（避免堵转烧板）。
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

# 1. 恢复扭力 + 回中
send(b"#002PULR!")
time.sleep(0.3)
send(b"#002P1500T800!")
time.sleep(1.2)

print("=== GRIPPER(ID=2) CALIBRATION ===")
print("P1500 -> PRAD:", repr(q(b"#002PRAD!")))

# 2. 开方向：加大脉冲 (gripper open)
print("--- open direction (pulse up) ---")
for p in (1700, 1900, 2100):
    send(b"#002P%04dT800!" % p)
    time.sleep(1.3)
    print("P%04d -> PRAD: %r" % (p, q(b"#002PRAD!")))

# 3. 回中
send(b"#002P1500T800!")
time.sleep(1.2)

# 4. 闭方向：减小脉冲 (gripper close)
print("--- close direction (pulse down) ---")
for p in (1300, 1100, 900):
    send(b"#002P%04dT800!" % p)
    time.sleep(1.3)
    print("P%04d -> PRAD: %r" % (p, q(b"#002PRAD!")))

# 5. 回中
send(b"#002P1500T800!")
time.sleep(1.0)
print("GRIPCAL" + chr(95) + "OK")
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

    ser.write(("cat > /tmp/sgc.py <<'PYEOF'\n" + SCRIPT + "\nPYEOF\n").encode())
    time.sleep(1.0)
    ser.reset_input_buffer()
    ser.write(b"PYTHONHOME=/ PYTHONPATH=/ LD_PRELOAD=/lib/libffi.so python3 -u /tmp/sgc.py\n")
    time.sleep(0.5)

    out = b""
    deadline = time.time() + 35.0
    while time.time() < deadline:
        if ser.in_waiting:
            out += ser.read(ser.in_waiting)
            if b"GRIPCAL_OK" in out or b"Traceback" in out:
                break
        time.sleep(0.1)

    txt = out.decode("utf-8", errors="replace")
    for line in txt.splitlines():
        s = line.strip()
        if s and ("P1" in s or "P0" in s or "PRAD" in s or "GRIPCAL" in s
                  or "Traceback" in s or "===" in s or "open" in s or "close" in s):
            print(s)
    ser.close()


if __name__ == "__main__":
    main()
