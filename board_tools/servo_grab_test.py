#!/usr/bin/env python3
"""servo_grab_test.py — 板端跑真实管线代码 servo_driver.grab() 抓球序列。

不回读（回包线路已不可靠），靠人眼观察手臂动作。
"""
import time
import serial as pyserial

PORT = "COM6"
BAUD = 115200

SCRIPT = r'''
import sys, time, subprocess
sys.path.insert(0, "/pipeline")
from servo_driver import create_servo

subprocess.run(["stty", "-F", "/dev/ttyS2", "115200", "raw", "-echo"])
s = create_servo(driver="zp10s", port="/dev/ttyS2")
print("servo type:", type(s).__name__)
print("restore_torque (PULR x3) ...")
time.sleep(0.5)

# 先回中，确认三舵机都活着
print("center all to 1500 ...")
for sid in (0, 1, 2):
    s._write(b"#%03dP1500T1000!" % sid)
    time.sleep(0.3)
time.sleep(1.5)

print(">>> 开始 grab() 抓球序列：张开->下探->闭合->抬臂")
print(">>> 请观察手臂动作")
s.grab()
time.sleep(1.0)
print(">>> grab() 完成，回到中心")
for sid in (0, 1, 2):
    s._write(b"#%03dP1500T1000!" % sid)
    time.sleep(0.3)
time.sleep(1.5)
s.close()
print("GRABTEST" + chr(95) + "OK")
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

    ser.write(("cat > /tmp/sgt.py <<'PYEOF'\n" + SCRIPT + "\nPYEOF\n").encode())
    time.sleep(1.0)
    ser.reset_input_buffer()
    ser.write(b"PYTHONHOME=/ PYTHONPATH=/ LD_PRELOAD=/lib/libffi.so python3 -u /tmp/sgt.py\n")
    time.sleep(0.5)

    out = b""
    deadline = time.time() + 25.0
    while time.time() < deadline:
        if ser.in_waiting:
            out += ser.read(ser.in_waiting)
            if b"GRABTEST_OK" in out or b"Traceback" in out:
                break
        time.sleep(0.1)

    txt = out.decode("utf-8", errors="replace")
    for line in txt.splitlines():
        s = line.strip()
        if s and ("servo" in s or "center" in s or "grab" in s or ">>>" in s
                  or "OK" in s or "Traceback" in s or "Error" in s):
            print(s)
    ser.close()


if __name__ == "__main__":
    main()
