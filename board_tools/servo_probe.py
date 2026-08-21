#!/usr/bin/env python3
"""
servo_probe.py — 通过 COM6 控制台，在板端用 Python 干净地查询 ZP10S 舵机。
写一个 /tmp/servo_probe.py 到板子，运行它读回包 (PVER/PID/PRTV)。
"""
import sys
import time
import serial

PORT = "COM6"
BAUD = 115200

BOARD_SCRIPT = r'''
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

def read_reply(timeout=1.2):
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
            if out.count(b"!") >= 1 and out.endswith(b"!"):
                break
    return out

for label, cmd in [("PVER", b"#000PVER!"), ("PID", b"#000PID!"),
                   ("PRAD", b"#000PRAD!"), ("PRTV", b"#000PRTV!")]:
    time.sleep(0.3)
    send(cmd)
    r = read_reply()
    print("QUERY", label, "->", repr(r))

print("PROBE_DONE")
'''

W = r'''
import os, time, select, termios
PORT = "/dev/ttyS2"
fd = os.open(PORT, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
a = termios.tcgetattr(fd)
a[0] &= ~(termios.IGNBRK | termios.BRKINT | termios.PARMRK | termios.ISTRIP | termios.INLCR | termios.IGNCR | termios.ICRNL | termios.IXON)
a[1] &= ~termios.OPOST
a[2] &= ~(termios.CSIZE | termios.PARENB)
a[2] |= termios.CS8
a[3] &= ~(termios.ECHO | termios.ICANON | termios.ISIG | termios.IEXTEN)
a[4] = termios.B115200
a[5] = termios.B115200
termios.tcsetattr(fd, termios.TCSANOW, a)
def send(b):
    os.write(fd, b)
def read_reply(timeout=1.2):
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
            if out.endswith(b"!"):
                break
    return out
for label, cmd in [("PVER", b"#000PVER!"), ("PID", b"#000PID!"), ("PRAD", b"#000PRAD!"), ("PRTV", b"#000PRTV!")]:
    time.sleep(0.3)
    send(cmd)
    r = read_reply()
    print("QUERY", label, "->", repr(r))
print("PROBE_DONE")
'''


def main():
    ser = serial.Serial(PORT, BAUD, timeout=0.3)
    ser.reset_input_buffer()
    time.sleep(0.2)

    # 1. write the script to the board via heredoc
    heredoc = "cat > /tmp/servo_probe.py <<'PYEOF'\n" + W + "\nPYEOF\n"
    ser.write(heredoc.encode())
    time.sleep(0.8)
    ser.reset_input_buffer()

    # 2. run it
    run = "PYTHONHOME=/ PYTHONPATH=/ LD_PRELOAD=/lib/libffi.so python3 -u /tmp/servo_probe.py\n"
    ser.write(run.encode())
    time.sleep(0.5)

    # 3. read output until PROBE_DONE or timeout
    out = b""
    deadline = time.time() + 8.0
    while time.time() < deadline:
        if ser.in_waiting:
            out += ser.read(ser.in_waiting)
            if b"PROBE_DONE" in out or b"Traceback" in out:
                break
        time.sleep(0.1)
    txt = out.decode("utf-8", errors="replace")
    # strip the echoed heredoc lines / prompt noise, keep QUERY lines
    for line in txt.splitlines():
        if "QUERY" in line or "PROBE_DONE" in line or "Traceback" in line or "Error" in line:
            print(line)
    ser.close()


if __name__ == "__main__":
    main()
