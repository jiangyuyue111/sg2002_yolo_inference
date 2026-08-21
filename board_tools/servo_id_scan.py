#!/usr/bin/env python3
"""servo_id_scan.py — 扫 ID 0..5，每个 ID 做一次可见摆动，靠人眼确定 ID↔物理舵机 映射。

避开 500/2500 极限（servo1 在 500 曾堵转），只用 1500<->1900 的安全范围。
"""
import time
import serial as pyserial

PORT = "COM6"
BAUD = 115200

SCRIPT = r'''
import os, time, termios
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

# 先对所有可能 ID 恢复扭力 + 回中
for sid in range(6):
    send(b"#%03dPULR!" % sid)
    time.sleep(0.15)
    send(b"#%03dP1500T800!" % sid)
    time.sleep(0.15)
time.sleep(1.0)

print("=== 逐个 ID 摆动，请只盯着夹爪(爪子)，看它在哪个 ID 动 ===")
for sid in range(6):
    print(">>> 现在 ID=%d 摆动: 1500 -> 2100 -> 1500，夹爪动了吗？" % sid)
    send(b"#%03dP2100T1200!" % sid)
    time.sleep(2.6)
    send(b"#%03dP1500T1200!" % sid)
    time.sleep(2.6)

print("IDSCAN" + chr(95) + "OK")
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

    ser.write(("cat > /tmp/sis.py <<'PYEOF'\n" + SCRIPT + "\nPYEOF\n").encode())
    time.sleep(1.0)
    ser.reset_input_buffer()
    ser.write(b"PYTHONHOME=/ PYTHONPATH=/ LD_PRELOAD=/lib/libffi.so python3 -u /tmp/sis.py\n")
    time.sleep(0.5)

    out = b""
    deadline = time.time() + 30.0
    while time.time() < deadline:
        if ser.in_waiting:
            out += ser.read(ser.in_waiting)
            if b"IDSCAN_OK" in out or b"Traceback" in out:
                break
        time.sleep(0.1)

    txt = out.decode("utf-8", errors="replace")
    for line in txt.splitlines():
        s = line.strip()
        if s and ("ID=" in s or "IDSCAN_OK" in s or "Traceback" in s or "===" in s):
            print(s)
    ser.close()


if __name__ == "__main__":
    main()
