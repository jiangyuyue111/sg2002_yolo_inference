#!/usr/bin/env python3
"""
serial_shell.py — 通过 COM6 (UART0 console) 与 SG2002 板子交互。

用法:
  python serial_shell.py "命令1" "命令2" ...   # 逐条发送, 每条后读回显
  python serial_shell.py --probe                # 只读当前串口缓冲, 不发命令
  python serial_shell.py --raw "echo -n '#000PVER!' > /dev/ttyS2"  # 发命令, 长等待读舵机回包

SG2002 console: UART0 @ 0x04140000, CH340, COM6, 115200-8N1
"""
import sys
import time
import serial

PORT = "COM6"
BAUD = 115200


def open_port():
    ser = serial.Serial(PORT, BAUD, timeout=0.3)
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    return ser


def read_available(ser, wait_s=0.6):
    """Read whatever is in the buffer, waiting up to wait_s."""
    out = b""
    deadline = time.time() + wait_s
    while time.time() < deadline:
        n = ser.in_waiting
        if n:
            chunk = ser.read(n)
            out += chunk
            # small extension: keep reading while data keeps coming
            deadline = time.time() + 0.4
        else:
            time.sleep(0.05)
    return out


def send_and_read(ser, cmd, wait_s=0.8):
    ser.write(cmd.encode("utf-8", errors="replace") + b"\n")
    time.sleep(0.3)
    return read_available(ser, wait_s)


def main():
    args = [a for a in sys.argv[1:]]
    ser = open_port()
    try:
        if not args or args[0] == "--probe":
            out = read_available(ser, 1.2)
            print(out.decode("utf-8", errors="replace"), end="")
            return
        raw_mode = args[0] == "--raw"
        cmds = args[1:] if raw_mode else args
        for cmd in cmds:
            # strip surrounding quotes the shell may have eaten
            if len(cmd) >= 2 and cmd[0] == '"' and cmd[-1] == '"':
                cmd = cmd[1:-1]
            print(f"\n>>> {cmd}")
            if raw_mode:
                ser.write(cmd.encode("utf-8", errors="replace") + b"\n")
                time.sleep(0.3)
                out = read_available(ser, 2.0)
            else:
                out = send_and_read(ser, cmd)
            txt = out.decode("utf-8", errors="replace")
            print(txt, end="")
            if not txt.strip():
                print("(no output)")
    finally:
        ser.close()


if __name__ == "__main__":
    main()
