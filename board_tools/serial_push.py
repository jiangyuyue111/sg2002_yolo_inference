#!/usr/bin/env python3
"""serial_push.py — 免拔卡：通过 COM6 串口把 PC 文件推送到板端。

用 base64 走串口（规避二进制/引号/换行），板端 /bin/base64 -d 还原。
推完后用板端 Python 校验「字节数 + null 字节数 + 字节和」，三者全对才覆盖，
避免「尺寸对但内容坏」（串口偶发丢/错字节）静默上卡。

用法:
  python board_tools/serial_push.py <本地文件> <板端目标路径>
"""
import sys
import time
import base64
import re
import serial

PORT = "COM6"
BAUD = 115200
CHUNK = 200         # 每次写出的字节数
CHUNK_SLEEP = 0.02  # 块间隔，限制速率 < 115200 线速

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def send(ser, text, wait=0.4):
    ser.write(text.encode())
    ser.flush()
    time.sleep(wait)


def drain(ser, wait=1.0):
    """读回串口缓冲直到安静，返回文本。"""
    out = b""
    deadline = time.time() + wait
    while time.time() < deadline:
        n = ser.in_waiting
        if n:
            out += ser.read(n)
            deadline = time.time() + 0.4
        else:
            time.sleep(0.05)
    return out.decode("utf-8", errors="replace")


def read_until(ser, marker, timeout):
    """读串口直到出现 marker 或超时（固定时长，不因静默提前返回）。"""
    out = b""
    t0 = time.time()
    while time.time() - t0 < timeout:
        n = ser.in_waiting
        if n:
            out += ser.read(n)
            if marker in out:
                time.sleep(0.3)
                out += ser.read(ser.in_waiting)
                break
        else:
            time.sleep(0.05)
    return out.decode("utf-8", errors="replace")


def clean_line(s):
    """去掉 kernel 调试日志 / base64 回显 / 提示符，只留 shell 有效输出。"""
    s = ANSI.sub("", s)
    if "starry_kernel" in s or "Enter user space" in s or "sys_waitpid" in s:
        return None
    if s.strip().startswith("root@starry"):
        return None
    if s.strip().startswith(">"):
        return None
    st = s.strip()
    # base64 回显行：长且仅含 base64 字符
    if len(st) > 60 and all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=" for c in st):
        return None
    return s


def main():
    if len(sys.argv) < 3:
        print("用法: python board_tools/serial_push.py <本地文件> <板端目标>")
        sys.exit(1)
    local, remote = sys.argv[1], sys.argv[2]

    data = open(local, "rb").read()
    b64 = base64.b64encode(data).decode()
    wrapped = "\n".join(b64[i:i + 76] for i in range(0, len(b64), 76))
    tmp = "/tmp/_push.b64"
    expect_size = len(data)
    expect_sum = sum(data)

    ser = serial.Serial(PORT, BAUD, timeout=0.3)
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    time.sleep(0.2)

    # 敲回车确保在干净提示符
    send(ser, "\n", wait=0.3)
    ser.reset_input_buffer()

    send(ser, f"rm -f {tmp}\n", wait=0.4)
    send(ser, f"cat > {tmp} <<'B64EOF'\n", wait=0.5)

    # 分块推 base64 体
    for i in range(0, len(wrapped), CHUNK):
        ser.write(wrapped[i:i + CHUNK].encode())
        ser.flush()
        time.sleep(CHUNK_SLEEP)

    send(ser, "\nB64EOF\n", wait=0.6)

    # base64 回显会淹没 PC 接收缓冲，等它流完再清空，避免干扰后续校验输出
    time.sleep(2.5)
    ser.reset_input_buffer()

    # 解码到 .new
    send(ser, f"base64 -d {tmp} > {remote}.new\n", wait=0.8)

    # 板端 Python 校验：字节数 / null 字节数 / 字节和
    # 用 heredoc 写临时脚本再跑，避免 -c 复杂引号在串口里被 shell 吞掉。
    vfy_script = (
        "import sys\n"
        "d=open(sys.argv[1],'rb').read()\n"
        "print('VFY_RESULT', len(d), d.count(bytes([0])), sum(d))\n"
    )
    send(ser, "cat > /tmp/_vfy.py <<'VEOF'\n" + vfy_script + "VEOF\n", wait=0.8)
    ser.reset_input_buffer()   # 清掉 heredoc 回显
    send(ser, f"PYTHONHOME=/ PYTHONPATH=/ LD_PRELOAD=/lib/libffi.so python3 -u "
              f"/tmp/_vfy.py {remote}.new\n", wait=0.5)
    txt = read_until(ser, b"VFY_RESULT", timeout=14.0)   # Python 启动慢，固定时长等
    lines = [clean_line(l) for l in txt.splitlines()]
    lines = [l for l in lines if l]

    ok = False
    for l in lines:
        if l.startswith("VFY_RESULT"):
            parts = l.split()
            if len(parts) == 4:
                sz, nulls, sm = int(parts[1]), int(parts[2]), int(parts[3])
                ok = (sz == expect_size and nulls == 0 and sm == expect_sum)
                print(f"[push] 校验: size={sz} nulls={nulls} sum={sm} "
                      f"(期望 {expect_size}/0/{expect_sum}) -> {'OK' if ok else 'MISMATCH'}")
    if not ok:
        print(f"[push] [FAIL] 内容校验不匹配，不覆盖。残留 {remote}.new 供排查。")
        ser.close()
        sys.exit(2)

    # 覆盖 + sync 刷盘（SD 卡 ext4 必须显式 sync，否则重启后页缓存丢失 → 文件坏）
    send(ser, f"mv -f {remote}.new {remote} && sync && ls -l {remote}\n", wait=1.2)
    txt = drain(ser, 2.0)
    lines = [clean_line(l) for l in txt.splitlines()]
    lines = [l for l in lines if l]
    for l in lines:
        print("   ", l)

    print(f"[push] [OK] {local} ({expect_size} B) -> {remote} done (synced)")
    ser.close()


if __name__ == "__main__":
    main()
