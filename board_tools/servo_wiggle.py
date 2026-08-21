#!/usr/bin/env python3
"""servo_wiggle.py — 持续驱动 ID=2 开合，让用户同时摇线，定位夹爪接触不良点。"""
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

send(b"#002PULR!")
time.sleep(0.4)

print("=== 持续驱动 ID=2 开合，请现在摇夹爪的线 ===")
for i in range(12):
    send(b"#002P2100T800!")
    print("  第%2d轮: 开(2100) ..." % (i+1))
    time.sleep(1.2)
    send(b"#002P900T800!")
    print("  第%2d轮: 闭(900)  ..." % (i+1))
    time.sleep(1.2)

send(b"#002P1500T800!")
print("WIGGLE" + chr(95) + "OK")
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

    ser.write(("cat > /tmp/sw.py <<'PYEOF'\n" + SCRIPT + "\nPYEOF\n").encode())
    time.sleep(1.0)
    ser.reset_input_buffer()
    ser.write(b"PYTHONHOME=/ PYTHONPATH=/ LD_PRELOAD=/lib/libffi.so python3 -u /tmp/sw.py\n")
    time.sleep(0.5)

    out = b""
    deadline = time.time() + 40.0
    while time.time() < deadline:
        if ser.in_waiting:
            out += ser.read(ser.in_waiting)
            if b"WIGGLE_OK" in out or b"Traceback" in out:
                break
        time.sleep(0.1)

    txt = out.decode("utf-8", errors="replace")
    for line in txt.splitlines():
        s = line.strip()
        if s and ("轮" in s or "WIGGLE_OK" in s or "Traceback" in s or "===" in s
                  or "摇" in s):
            print(s)
    ser.close()


if __name__ == "__main__":
    main()
