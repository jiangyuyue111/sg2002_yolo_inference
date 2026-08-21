#!/usr/bin/env python3
"""servo_driver_test.py — 在板端测管线真实代码路径 servo_driver.py。

stty 预热 + PULR 恢复扭力 + create_servo + set_angle 扫摆。
"""
import time
import serial as pyserial

PORT = "COM6"
BAUD = 115200

SCRIPT = r'''
import sys, time, subprocess
sys.path.insert(0, "/pipeline")
from servo_driver import create_servo

# 预热 + 恢复扭力
subprocess.run(["stty", "-F", "/dev/ttyS2", "115200", "raw", "-echo"])
s = create_servo(driver="zp10s", port="/dev/ttyS2")
print("servo type:", type(s).__name__)
s._write(b"#000PULR!")
time.sleep(0.5)

print("sweep via set_angle:")
for angle in [270, 0, 90]:
    s.set_angle(0, angle, 1000)
    print("  set_angle(0, %d) -> pulse %d" % (angle, s._angle_to_pulse(angle)))
    time.sleep(2)

s.close()
print("SERVO_DRIVER_TEST_DONE")
'''


def main():
    ser = pyserial.Serial(PORT, BAUD, timeout=0.3)
    ser.reset_input_buffer()
    time.sleep(0.2)
    ser.write(("cat > /tmp/sdt.py <<'PYEOF'\n" + SCRIPT + "\nPYEOF\n").encode())
    time.sleep(0.8)
    ser.reset_input_buffer()
    ser.write(b"PYTHONHOME=/ PYTHONPATH=/ LD_PRELOAD=/lib/libffi.so python3 -u /tmp/sdt.py\n")
    time.sleep(0.5)
    out = b""
    deadline = time.time() + 12.0
    while time.time() < deadline:
        if ser.in_waiting:
            out += ser.read(ser.in_waiting)
            if b"SERVO_DRIVER_TEST_DONE" in out or b"Traceback" in out:
                break
        time.sleep(0.1)
    txt = out.decode("utf-8", errors="replace")
    for line in txt.splitlines():
        s = line.strip()
        if ("servo" in s or "set_angle" in s or "sweep" in s or "pulse" in s
                or "DONE" in s or "Traceback" in s or "Error" in s or "Raw" in s):
            print(line)
    ser.close()


if __name__ == "__main__":
    main()
