#!/usr/bin/env python3
"""servo_move_test.py — 覆盖全部 3 舵机的决定性测试（v3，修复串口回显检测）。

对每个舵机 ID=0/1/2：PULR(恢复扭力) -> PRAD 基线 -> P1500 -> P0500 -> P2500 -> PRAD。
用 PRAD 读回位置值客观判断是否在动。sentinel 用 chr() 拼装，避免 heredoc 回显误触发。
"""
import time
import serial as pyserial

PORT = "COM6"
BAUD = 115200

# 注意：sentinel 在板端脚本里用 chr(95) 拼 "_"，源码里不含字面 "MOVETEST_OK"，
# 这样 PC 端读到的 heredoc 回显不会误触发完成判断。
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

def read_until_bang(timeout=1.5):
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
            if b"!" in out:
                break
    return out

def prad(sid, label):
    drain()
    send(b"#%03dPRAD!" % sid)
    r = read_until_bang()
    print("s%d PRAD %-10s -> %r" % (sid, label, r))

for sid in (0, 1, 2):
    print("=== servo id=%d ===" % sid)
    drain()
    send(b"#%03dPULR!" % sid)
    time.sleep(0.4)
    prad(sid, "baseline")
    for p in (1500, 500, 2500, 1500):
        send(b"#%03dP%04dT1000!" % (sid, p))
        time.sleep(1.6)
        prad(sid, "afterP%04d" % p)

print("MOVETEST" + chr(95) + "OK")
'''


def main():
    ser = pyserial.Serial(PORT, BAUD, timeout=0.3)
    ser.reset_input_buffer()
    time.sleep(0.2)

    # 幂等配置 pinmux + stty
    for c in ("echo '0x70 2' > /dev/pinmux",
              "echo '0x74 2' > /dev/pinmux",
              "stty -F /dev/ttyS2 115200 raw -echo"):
        ser.write((c + "\n").encode())
        time.sleep(0.35)
    ser.reset_input_buffer()

    # 写板端脚本（heredoc）
    ser.write(("cat > /tmp/smt.py <<'PYEOF'\n" + SCRIPT + "\nPYEOF\n").encode())
    time.sleep(1.0)
    # 清掉 heredoc 的回显，避免干扰后续读取
    ser.reset_input_buffer()

    # 运行
    ser.write(b"PYTHONHOME=/ PYTHONPATH=/ LD_PRELOAD=/lib/libffi.so python3 -u /tmp/smt.py\n")
    time.sleep(0.5)

    out = b""
    deadline = time.time() + 40.0
    while time.time() < deadline:
        if ser.in_waiting:
            out += ser.read(ser.in_waiting)
            if b"MOVETEST_OK" in out or b"Traceback" in out:
                break
        time.sleep(0.1)

    txt = out.decode("utf-8", errors="replace")
    for line in txt.splitlines():
        s = line.strip()
        if s and ("servo id" in s or "PRAD" in s or "Traceback" in s
                  or "MOVETEST_OK" in s or "Error" in s):
            print(s)
    ser.close()


if __name__ == "__main__":
    main()
