#!/usr/bin/env python3
"""servo_id_confirm.py — 确认夹爪 ID 到底是 4 还是 5。

先 ID=5 大幅开合，再 ID=4 大幅开合，每步清晰播报，靠人眼确认夹爪。
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

for sid in range(6):
    send(b"#%03dPULR!" % sid)
    time.sleep(0.15)

for sid in (2, 3):
    print("=== 现在测试 ID=%d，请盯夹爪：开(2100) -> 闭(900) -> 中 ===" % sid)
    send(b"#%03dP2100T1200!" % sid)
    time.sleep(2.2)
    send(b"#%03dP900T1200!" % sid)
    time.sleep(2.2)
    send(b"#%03dP1500T1200!" % sid)
    time.sleep(2.2)

print("CONFIRM" + chr(95) + "OK")
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

    ser.write(("cat > /tmp/sic.py <<'PYEOF'\n" + SCRIPT + "\nPYEOF\n").encode())
    time.sleep(1.0)
    ser.reset_input_buffer()
    ser.write(b"PYTHONHOME=/ PYTHONPATH=/ LD_PRELOAD=/lib/libffi.so python3 -u /tmp/sic.py\n")
    time.sleep(0.5)

    out = b""
    deadline = time.time() + 25.0
    while time.time() < deadline:
        if ser.in_waiting:
            out += ser.read(ser.in_waiting)
            if b"CONFIRM_OK" in out or b"Traceback" in out:
                break
        time.sleep(0.1)

    txt = out.decode("utf-8", errors="replace")
    for line in txt.splitlines():
        s = line.strip()
        if s and ("ID=" in s or "CONFIRM_OK" in s or "Traceback" in s):
            print(s)
    ser.close()


if __name__ == "__main__":
    main()
