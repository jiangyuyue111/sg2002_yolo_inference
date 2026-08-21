#!/usr/bin/env python3
"""
baud_probe.py — 决定性验证：Python 打开 ttyS2 后波特率是否被重置。

流程 (全在板端做):
  1. stty 115200 -> 确认 115200
  2. Python: os.open + tcgetattr + tcsetattr(B115200) + close  (模拟 servo_driver._open_raw)
  3. stty -a -> 看波特率是否还是 115200
  4. Python: pyserial serial.Serial(115200) + close  (若 pyserial 存在)
  5. stty -a -> 再看
"""
import time
import serial as pyserial

PORT = "COM6"
BAUD = 115200

SCRIPT = r'''
import subprocess
def stty():
    return subprocess.run(["stty", "-F", "/dev/ttyS2", "-a"], capture_output=True).stdout.decode()

print("=== [1] after stty 115200 ===")
print([l for l in stty().splitlines() if "speed" in l])

print("=== [2] after raw-fd tcsetattr(B115200) ===")
import os, termios
fd = os.open("/dev/ttyS2", os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
a = termios.tcgetattr(fd)
a[3] &= ~(termios.ECHO | termios.ICANON | termios.ISIG)
a[4] = termios.B115200
a[5] = termios.B115200
termios.tcsetattr(fd, termios.TCSANOW, a)
os.close(fd)
print([l for l in stty().splitlines() if "speed" in l])

print("=== [3] pyserial availability ===")
try:
    import serial
    print("pyserial installed:", serial.VERSION)
except Exception as e:
    print("pyserial NOT installed:", repr(e))

print("=== [4] after pyserial serial.Serial(115200) ===")
try:
    import serial
    s = serial.Serial("/dev/ttyS2", 115200, timeout=0.1)
    s.close()
    print([l for l in stty().splitlines() if "speed" in l])
except Exception as e:
    print("pyserial open failed:", repr(e))

print("BAUD_PROBE_DONE")
'''


def main():
    ser = pyserial.Serial(PORT, BAUD, timeout=0.3)
    ser.reset_input_buffer()
    time.sleep(0.2)

    # write script
    ser.write(("cat > /tmp/baud_probe.py <<'PYEOF'\n" + SCRIPT + "\nPYEOF\n").encode())
    time.sleep(0.8)
    ser.reset_input_buffer()

    # prepare port first
    ser.write(b"stty -F /dev/ttyS2 115200 raw -echo\n")
    time.sleep(0.4)
    ser.reset_input_buffer()

    # run
    ser.write(b"PYTHONHOME=/ PYTHONPATH=/ LD_PRELOAD=/lib/libffi.so python3 -u /tmp/baud_probe.py\n")
    time.sleep(0.5)

    out = b""
    deadline = time.time() + 10.0
    while time.time() < deadline:
        if ser.in_waiting:
            out += ser.read(ser.in_waiting)
            if b"BAUD_PROBE_DONE" in out or b"Traceback" in out:
                break
        time.sleep(0.1)
    txt = out.decode("utf-8", errors="replace")
    for line in txt.splitlines():
        s = line.strip()
        if s.startswith("===") or "speed" in s or "pyserial" in s or "Traceback" in s or "Error" in s or "DONE" in s:
            print(line)
    ser.close()


if __name__ == "__main__":
    main()
