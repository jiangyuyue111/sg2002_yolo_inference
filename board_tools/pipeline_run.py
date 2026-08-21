#!/usr/bin/env python3
"""pipeline_run.py — 在板端运行 real_pipeline.py 并流式读输出，N 秒后 Ctrl+C 停止。

用法:
  python pipeline_run.py [run_seconds] [--quiet] [--chase-only]
默认跑 20 秒。--chase-only 只追球、近处停车不夹取。停止时发 Ctrl+C 触发管线 SIGINT 处理器（刹车+关设备）。
"""
import sys
import time
import serial as pyserial

# 板端 real_pipeline 会回传 ✓ (U+2713) 等非 ASCII 字符；Windows 控制台 GBK
# 编不了就崩，崩了还来不及发 Ctrl+C，留下孤儿管线。强制 UTF-8 + 替换兜底。
if sys.stdout.encoding and sys.stdout.encoding.lower().replace("-", "") not in ("utf8", "utf8sig"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PORT = "COM6"
BAUD = 115200

PIPELINE_CMD = (
    "cd /pipeline && PYTHONHOME=/ PYTHONPATH=/ LD_PRELOAD=/lib/libffi.so "
    "python3 -u /pipeline/real_pipeline.py\n"
)


def main():
    run_s = 20
    quiet = False
    chase_only = "--chase-only" in sys.argv
    if len(sys.argv) > 1 and sys.argv[1].lstrip("-").isdigit():
        run_s = int(sys.argv[1])
    if "--quiet" in sys.argv:
        quiet = True

    flag = " --chase-only" if chase_only else ""
    cmd = PIPELINE_CMD.rstrip("\n") + flag + "\n"

    ser = pyserial.Serial(PORT, BAUD, timeout=0.2)
    ser.reset_input_buffer()
    time.sleep(0.2)

    print(f"[runner] starting pipeline for {run_s}s (chase_only={chase_only}) ...")
    ser.write(cmd.encode())
    ser.flush()

    t0 = time.time()
    buf = b""
    hang_reason = None
    last_activity = time.time()

    # 相机 UVC DMA 偶发挂死(内核态,用户态重启子进程无法恢复——只能断电)。
    # 挂死时板端 watchdog 3s 无帧会打印 `[WATCHDOG]` 并刹车——立即响应。
    # 兜底:管线静默超时(正常搜索时最长 ~6.3s/30帧才打印一帧,8s 不会误报)。
    WATCHDOG_MARK = b"[WATCHDOG]"
    SILENT_TIMEOUT = 8.0

    while time.time() - t0 < run_s:
        if ser.in_waiting:
            chunk = ser.read(ser.in_waiting)
            buf += chunk
            last_activity = time.time()
            if WATCHDOG_MARK in buf:
                hang_reason = "相机挂死(watchdog 已刹车)"
                break
            if not quiet:
                sys.stdout.write(chunk.decode("utf-8", errors="replace"))
                sys.stdout.flush()
        if time.time() - last_activity > SILENT_TIMEOUT:
            hang_reason = "相机挂死(管线静默超时)"
            break
        time.sleep(0.05)

    # Ctrl+C to stop (hang 时同样发,触发管线 SIGINT 处理器刹车+关设备)
    print(f"\n[runner] sending Ctrl+C ...")
    ser.write(b"\x03")
    ser.flush()

    # read shutdown output
    t1 = time.time()
    while time.time() - t1 < 4.0:
        if ser.in_waiting:
            chunk = ser.read(ser.in_waiting)
            buf += chunk
            if not quiet:
                sys.stdout.write(chunk.decode("utf-8", errors="replace"))
                sys.stdout.flush()
        time.sleep(0.05)

    if hang_reason:
        print(f"\n[runner] !! {hang_reason} —— 相机 UVC DMA 已在内核态卡死,"
              f"重启子进程无法恢复。请断电重启后重试。")
    else:
        print(f"\n[runner] done (未检测到挂死)。")
    ser.close()


if __name__ == "__main__":
    main()
