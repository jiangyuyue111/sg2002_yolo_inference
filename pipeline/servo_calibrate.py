#!/usr/bin/env python3
"""
servo_calibrate.py — ZP10S 舵机真机校准工具

用途：确认 ZP10S 量程（500-2500 究竟对应 0°~180° 还是 0°~270°），
      并为夹爪/手臂每个关键动作找到正确的 pulse 值。

板上运行（无 pyserial，走 raw fd + termios）:
    PYTHONPATH=/ python3 /pipeline/servo_calibrate.py                 # 关键动作对照
    PYTHONPATH=/ python3 /pipeline/servo_calibrate.py --id 2 --pulse 1500  # 单点
    PYTHONPATH=/ python3 /pipeline/servo_calibrate.py --sweep --id 2        # 全量程扫

扫描时每个位置停 2 秒，请观察舵机实际转到的角度并记录，据此反推量程。
若 pulse=1500 时舵机在 90° 附近 → 量程 0°~180°（当前 servo_driver 假设正确）。
若 pulse=1500 时舵机在 135° 附近 → 量程 0°~270°（还需改回 /270 公式）。
"""

import os
import sys
import time
import argparse
import fcntl


# ═══════════════════════════════════════════════════════════════════════
# 串口打开（pyserial 优先，板端 raw fd 回退）
# ═══════════════════════════════════════════════════════════════════════

class Port:
    def __init__(self, port, baud=115200):
        self._ser = None
        self._fd = None
        try:
            import serial
            self._ser = serial.Serial(port, baud, timeout=0.1)
            print(f"[cal] pyserial opened {port} @ {baud}")
            return
        except ImportError:
            pass
        except Exception as e:
            print(f"[cal] pyserial open failed ({e}); trying raw fd")

        import termios
        fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        attrs = termios.tcgetattr(fd)
        attrs[3] = attrs[3] & ~(termios.ECHO | termios.ICANON | termios.ISIG)
        attrs[4] = termios.B115200
        attrs[5] = termios.B115200
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)
        self._fd = fd
        print(f"[cal] raw fd opened {port} @ {baud} (termios)")

    def write(self, data: bytes) -> None:
        if self._ser is not None:
            self._ser.write(data)
            self._ser.flush()
        elif self._fd is not None:
            os.write(self._fd, data)

    def close(self) -> None:
        if self._ser is not None:
            self._ser.close()
        elif self._fd is not None:
            os.close(self._fd)


# ═══════════════════════════════════════════════════════════════════════
# 指令
# ═══════════════════════════════════════════════════════════════════════

def send_pulse(p, servo_id: int, pulse: int, time_ms: int = 1000) -> str:
    pulse = max(500, min(2500, int(pulse)))
    cmd = f"#{servo_id:03d}P{pulse:04d}T{time_ms:04d}!"
    p.write(cmd.encode("ascii"))
    return cmd


def restore_torque(p):
    for sid in (0, 1, 2):
        p.write(f"#{sid:03d}PULR!".encode("ascii"))
    print("[cal] 已发送恢复扭力 #000/#001/#002 PULR!")


# 关键动作：当前 servo_driver.py 的角度值（180° 量程）→ 对应 pulse
KEY_ACTIONS = [
    ("servo0 底座 下探(prepare)", 163),
    ("servo0 底座 抬升(lift)",   133),
    ("servo1 肩   下探(prepare)", 120),
    ("servo1 肩   抬升(lift)",   120),
    ("servo2 夹爪 张开(prepare)", 135),
    ("servo2 夹爪 闭合(grab)",    88),
]


def angle_to_pulse(angle: float) -> int:
    return int(500 + (angle / 180.0) * 2000)


# ═══════════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="ZP10S 舵机校准")
    ap.add_argument("--port", default="/dev/ttyS2")
    ap.add_argument("--id", type=int, default=-1,
                    help="只操作这个舵机 ID（默认全部 0/1/2）")
    ap.add_argument("--pulse", type=int, default=None,
                    help="发单个 pulse 值（配合 --id）")
    ap.add_argument("--sweep", action="store_true",
                    help="全量程扫描 500~2500")
    ap.add_argument("--step", type=int, default=250,
                    help="扫描步进（默认 250）")
    ap.add_argument("--hold", type=float, default=2.0,
                    help="每个位置停留秒数（默认 2.0）")
    args = ap.parse_args()

    p = Port(args.port)
    restore_torque(p)
    time.sleep(0.3)

    ids = [args.id] if args.id >= 0 else [0, 1, 2]

    # 单点模式
    if args.pulse is not None:
        for sid in ids:
            cmd = send_pulse(p, sid, args.pulse, 1000)
            print(f"[cal] 舵机{sid}: {cmd}")
        p.close()
        return

    # 全量程扫描
    if args.sweep:
        for sid in ids:
            print(f"\n=== 扫描 舵机{sid}（观察实际角度，判断量程）===")
            pulse = 500
            while pulse <= 2500:
                send_pulse(p, sid, pulse, 1000)
                print(f"  pulse={pulse:4d}  →  若量程180°则为 {int((pulse-500)/2000*180):3d}°，"
                      f"若270°则为 {int((pulse-500)/2000*270):3d}°")
                time.sleep(args.hold)
                pulse += args.step
            send_pulse(p, sid, 1500, 1000)  # 回中
        p.close()
        return

    # 默认：关键动作对照
    print("\n=== 关键动作对照（当前 servo_driver.py 假设 180° 量程）===")
    for label, angle in KEY_ACTIONS:
        pulse = angle_to_pulse(angle)
        # 只对涉及的舵机发（label 里有 servo0/1/2）
        sid = int(label.split("servo")[1][0])
        if args.id >= 0 and sid != args.id:
            continue
        send_pulse(p, sid, pulse, 1000)
        print(f"  {label:28s}  角度={angle:3d}°  pulse={pulse:4d}")
        time.sleep(args.hold)
    print("\n若这些位置不对，记录每个动作的正确 pulse 后，"
          "改 pipeline/servo_driver.py 的 _angles 字典。")
    p.close()


if __name__ == "__main__":
    main()
