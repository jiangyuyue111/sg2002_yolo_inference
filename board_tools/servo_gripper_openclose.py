#!/usr/bin/env python3
"""servo_gripper_openclose.py — 夹爪(ID=2) 大开合测试（4位时间）。

P2100(开) / P0900(闭) 大行程，多次读回追踪，靠人眼确认夹爪物理开合。
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

print("=== OPEN/CLOSE TEST ID=2 (4-digit time) ===")
send(b"#002PULR!")
time.sleep(0.4)
print("start PRAD:", repr(q(b"#002PRAD!")))

print(">> OPEN  -> P2100 T1500  (watch gripper!)")
send(b"#002P2100T1500!")
for i in range(4):
    time.sleep(1.0)
    print("   t+%ds PRAD: %r" % (i+1, q(b"#002PRAD!")))

print(">> CLOSE -> P0900 T1500  (watch gripper!)")
send(b"#002P0900T1500!")
for i in range(4):
    time.sleep(1.0)
    print("   t+%ds PRAD: %r" % (i+1, q(b"#002PRAD!")))

print(">> OPEN  -> P2100 T1500  (again)")
send(b"#002P2100T1500!")
time.sleep(2.0)
print("   PRAD:", repr(q(b"#002PRAD!")))

print("OPENCLOSE" + chr(95) + "OK")
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

    ser.write(("cat > /tmp/soc.py <<'PYEOF'\n" + SCRIPT + "\nPYEOF\n").encode())
    time.sleep(1.0)
    ser.reset_input_buffer()
    ser.write(b"PYTHONHOME=/ PYTHONPATH=/ LD_PRELOAD=/lib/libffi.so python3 -u /tmp/soc.py\n")
    time.sleep(0.5)

    out = b""
    deadline = time.time() + 35.0
    while time.time() < deadline:
        if ser.in_waiting:
            out += ser.read(ser.in_waiting)
            if b"OPENCLOSE_OK" in out or b"Traceback" in out:
                break
        time.sleep(0.1)

    txt = out.decode("utf-8", errors="replace")
    for line in txt.splitlines():
        s = line.strip()
        if s and ("PRAD" in s or "OPEN" in s or "CLOSE" in s or "OPENCLOSE" in s
                  or "Traceback" in s or "===" in s or "start" in s or "watch" in s):
            print(s)
    ser.close()


if __name__ == "__main__":
    main()
