#!/usr/bin/env python3
"""baud_probe2.py — 决定性测试：tcsetattr 能否真正改波特率。

stty 38400 -> python tcsetattr(B115200) -> stty -a 看是否变 115200。
"""
import time
import serial as pyserial

PORT = "COM6"
BAUD = 115200

SCRIPT = r'''
import subprocess, os, termios
def speed():
    out = subprocess.run(["stty", "-F", "/dev/ttyS2", "-a"], capture_output=True).stdout.decode()
    for l in out.splitlines():
        if "speed" in l:
            return l.split(";")[0].strip()
    return "?"

subprocess.run(["stty", "-F", "/dev/ttyS2", "38400", "raw", "-echo"])
print("A after stty 38400 ->", speed())
fd = os.open("/dev/ttyS2", os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
a = termios.tcgetattr(fd)
a[3] &= ~(termios.ECHO | termios.ICANON | termios.ISIG)
a[4] = termios.B115200
a[5] = termios.B115200
termios.tcsetattr(fd, termios.TCSANOW, a)
os.close(fd)
print("B after tcsetattr(B115200) ->", speed())
print("DONE")
'''


def main():
    ser = pyserial.Serial(PORT, BAUD, timeout=0.3)
    ser.reset_input_buffer()
    time.sleep(0.2)
    ser.write(("cat > /tmp/bp2.py <<'PYEOF'\n" + SCRIPT + "\nPYEOF\n").encode())
    time.sleep(0.8)
    ser.reset_input_buffer()
    ser.write(b"PYTHONHOME=/ PYTHONPATH=/ LD_PRELOAD=/lib/libffi.so python3 -u /tmp/bp2.py\n")
    time.sleep(0.5)
    out = b""
    deadline = time.time() + 8.0
    while time.time() < deadline:
        if ser.in_waiting:
            out += ser.read(ser.in_waiting)
            if b"DONE" in out or b"Traceback" in out:
                break
        time.sleep(0.1)
    txt = out.decode("utf-8", errors="replace")
    for line in txt.splitlines():
        if "->" in line or "DONE" in line or "Traceback" in line:
            print(line)
    # restore 115200 for safety
    ser.write(b"stty -F /dev/ttyS2 115200 raw -echo\n")
    time.sleep(0.3)
    ser.close()


if __name__ == "__main__":
    main()
