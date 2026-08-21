"""
motor_raw_test.py — SG2002 板端电机原始串口测试（带响应读取）
无 pyserial 依赖，纯 os.read/os.write
"""

import os, struct, time, select

PORT = '/dev/ttyS1'

def build_frame(cmd, payload=b''):
    length = len(payload)
    chk = cmd ^ length
    for b in payload:
        chk ^= b
    return bytes([0xAA, 0x55, cmd, length]) + payload + bytes([chk & 0xFF])

def recv_frame(fd, timeout=1.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], 0.1)
        if not r:
            continue
        b = os.read(fd, 1)
        if not b:
            continue
        if b[0] == 0xAA:
            b2 = os.read(fd, 1)
            if b2 and b2[0] == 0x55:
                break
    else:
        return None

    header = os.read(fd, 2)
    if len(header) < 2:
        return None
    cmd, length = header[0], header[1]

    payload = os.read(fd, length) if length else b''
    chk_b = os.read(fd, 1)
    if not chk_b:
        return None

    chk = cmd ^ length
    for b in payload:
        chk ^= b
    ok = chk == chk_b[0]
    return cmd, payload, ok

# ── 主流程 ──
fd = os.open(PORT, os.O_RDWR | os.O_NOCTTY)
print(f"Opened {PORT} fd={fd}")

# 1. INIT
print(">>> INIT")
os.write(fd, build_frame(0x01))
time.sleep(0.15)
resp = recv_frame(fd)
print(f"    {resp}")

# 2. CONFIG
print(">>> CONFIG PPR=4680 FREQ=20000")
payload = struct.pack(">HH", 4680, 20000)
os.write(fd, build_frame(0x02, payload))
time.sleep(0.15)
resp = recv_frame(fd)
print(f"    {resp}")

# 3. SET_SPEEDS 前进
print(">>> SET_SPEEDS (80, 80)")
payload = struct.pack(">hh", 80, 80)
os.write(fd, build_frame(0x13, payload))
time.sleep(0.15)
resp = recv_frame(fd)
print(f"    {resp}")

time.sleep(2)

# 4. BRAKE
print(">>> BRAKE")
os.write(fd, build_frame(0x12, b'\x02'))
time.sleep(0.15)
resp = recv_frame(fd)
print(f"    {resp}")

os.close(fd)
print("DONE")
