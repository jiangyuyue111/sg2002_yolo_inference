"""
uart_loopback.py — SG2002 UART 回环测试
短接 TX/RX 后自发自收，验证 UART 物理层是否正常。

用法:
  PYTHONPATH=/ python3 /pipeline/uart_loopback.py          # 测试 /dev/ttyS1
  PYTHONPATH=/ python3 /pipeline/uart_loopback.py /dev/ttyS2
"""

import os
import sys
import time
import select

PORT = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyS1"
BAUDS = [115200, 57600, 38400, 9600]
TEST_DATA = b"Hello_UART_Loopback_Test_0123456789"


def open_raw(port, baud):
    """Open serial in raw mode via termios (board only)."""
    import termios, fcntl
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    attrs = termios.tcgetattr(fd)
    attrs[3] &= ~(termios.ECHO | termios.ICANON | termios.ISIG)
    baud_map = {9600: termios.B9600, 19200: termios.B19200, 38400: termios.B38400,
                57600: termios.B57600, 115200: termios.B115200, 230400: termios.B230400}
    b = baud_map.get(baud, termios.B115200)
    attrs[4] = attrs[5] = b
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)
    return fd


def loopback_test(fd, data, timeout=0.5):
    """Write data to fd, read it back, compare."""
    # Drain any pending input
    while True:
        r, _, _ = select.select([fd], [], [], 0.02)
        if not r:
            break
        try:
            os.read(fd, 256)
        except BlockingIOError:
            break

    written = os.write(fd, data)
    print(f"  TX: {written} bytes → {data[:30]}...")

    # Read back
    deadline = time.time() + timeout
    buf = b""
    while len(buf) < len(data):
        rem = deadline - time.time()
        if rem <= 0:
            break
        r, _, _ = select.select([fd], [], [], min(0.05, rem))
        if not r:
            continue
        try:
            chunk = os.read(fd, len(data) - len(buf))
        except BlockingIOError:
            continue
        if not chunk:
            break
        buf += chunk

    print(f"  RX: {len(buf)} bytes → {buf[:30]}...")
    return buf == data


def main():
    print("=" * 50)
    print("  SG2002 UART Loopback Test")
    print(f"  Port: {PORT}")
    print("=" * 50)
    print()
    print("  ⚠️  请先用杜邦线短接 TX 和 RX 引脚!")
    print("  SG2002 UART1: TX=GPIOA19(A19), RX=GPIOA18(A18)")
    print()

    if not os.path.exists(PORT):
        print(f"[FAIL] {PORT} 不存在")
        devices = [f"/dev/{d}" for d in os.listdir("/dev") if d.startswith("tty")]
        print(f"  可用串口: {devices}")
        sys.exit(1)

    print(f"[OK] {PORT} 设备存在")

    for baud in BAUDS:
        print(f"\n── 测试波特率 {baud} ──")
        try:
            fd = open_raw(PORT, baud)
        except Exception as e:
            print(f"  [SKIP] 无法打开: {e}")
            continue

        try:
            match = loopback_test(fd, TEST_DATA)
            if match:
                print(f"  [PASS] {baud} 回环成功! TX→RX 数据一致 ✅")
                os.close(fd)
                print(f"\n{'='*50}")
                print(f"  结论: UART{ PORT[-1] } 物理层正常!")
                print(f"  问题不在 SG2002 串口驱动")
                print(f"  请排查 SG2002↔ESP32 之间的接线/电平")
                print(f"{'='*50}")
                return
            else:
                print(f"  [FAIL] {baud} 回环失败 — 收到数据不匹配")
        finally:
            os.close(fd)

    print(f"\n{'='*50}")
    print(f"  结论: 所有波特率回环均失败")
    print(f"  问题在 SG2002 UART 驱动或硬件")
    print(f"  请检查: 内核 UART 驱动 / pinmux 配置")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
