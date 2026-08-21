#!/usr/bin/env python3
"""motor_direction_test.py — 在真车上标定电机方向符号 (PC端, 通过 COM6 串口)。

为什么需要它：
    motor_debug.py 只验证了 ACK 和编码器 RPM，没有验证轮子在真车上实际
    往哪个方向滚。而 real_pipeline.py 追球逻辑假设「set_speeds(+,+) = 前进、
    left/right = 物理左右轮」。这两个假设任何一个反了，车都会背对球跑或
    朝反方向转。追球调参之前必须先把方向定对。

用法：
    python board_tools/motor_direction_test.py

车会依次执行 4 段动作，每段停够时间。你观察车体/轮子实际怎么动，记录：
    [1] set_speeds(+60,+60)  → 车往 前 / 后 ?
    [2] set_speeds(-60,-60)  → 车往 前 / 后 ?
    [3] set_speeds(-60,+60)  → 车 左转 / 右转 / 原地左旋 / 原地右旋 ?
    [4] set_speeds(+60,-60)  → 车 左转 / 右转 / 原地左旋 / 原地右旋 ?

把观察结果告诉 Claude，据此修正 real_pipeline.py 的符号或左右轮映射。

建议：把车架空或放地上，确保轮子能自由滚动、能看清方向。
"""

import time
import serial as pyserial

PORT = "COM6"
BAUD = 115200

# 板端脚本：走 /pipeline/motor_driver.py 的 TtPidDriver（含握手 + termios 波特率）
SCRIPT = r'''
import sys, time
sys.path.insert(0, "/pipeline")
from motor_driver import create_motor_driver

m = create_motor_driver(mode="tt_pid", port="/dev/ttyS1")
if m is None or getattr(m, "_ser", None) is None:
    print("MOTOR_INIT_FAIL")
    sys.exit(1)
print("motor ready:", type(m).__name__)

def run(label, left, right, secs):
    print("")
    print("[%s] set_speeds(%+d,%+d) for %.1fs" % (label, left, right, secs))
    print("      >>> 观察车实际怎么动，记下来")
    m.set_speeds(left, right)
    time.sleep(secs)
    m.brake()
    time.sleep(1.0)

run("1", 60, 60, 2.5)
run("2", -60, -60, 2.5)
run("3", -60, 60, 2.0)
run("4", 60, -60, 2.0)

m.brake()
m.close()
print("MOTORDIR_OK")
'''


def main():
    ser = pyserial.Serial(PORT, BAUD, timeout=0.3)
    ser.reset_input_buffer()
    time.sleep(0.2)

    # pinmux: UART1 → JTAG 引脚 (0x64=TX, 0x68=RX)，再设波特率
    print("[runner] setting pinmux + baud ...")
    for c in ("echo '0x64 6' > /dev/pinmux",
              "echo '0x68 6' > /dev/pinmux",
              "stty -F /dev/ttyS1 115200 raw -echo"):
        ser.write((c + "\n").encode())
        time.sleep(0.35)
    ser.reset_input_buffer()

    # 写板端脚本到 /tmp 并运行
    ser.write(("cat > /tmp/mdt.py <<'PYEOF'\n" + SCRIPT + "\nPYEOF\n").encode())
    time.sleep(1.0)
    ser.reset_input_buffer()
    ser.write(b"PYTHONHOME=/ PYTHONPATH=/ LD_PRELOAD=/lib/libffi.so python3 -u /tmp/mdt.py\n")
    time.sleep(0.5)

    out = b""
    deadline = time.time() + 30.0
    while time.time() < deadline:
        if ser.in_waiting:
            out += ser.read(ser.in_waiting)
            if b"MOTORDIR_OK" in out or b"MOTOR_INIT_FAIL" in out or b"Traceback" in out:
                break
        time.sleep(0.1)

    txt = out.decode("utf-8", errors="replace")
    markers = ("motor", "set_speeds", ">>>", "OK", "FAIL", "Traceback",
               "Error", "handshake", "ready", "INIT")
    for line in txt.splitlines():
        s = line.strip()
        if s and any(m in s for m in markers):
            print(s)
    ser.close()


if __name__ == "__main__":
    main()
