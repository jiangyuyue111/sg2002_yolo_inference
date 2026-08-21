#!/usr/bin/env python3
"""
motor_test.py — Minimal motor spin test for SG2002 board.
No pyserial dependency — works with raw /dev/ttyS1 file I/O.

Usage (on board):
    python3 /pipeline/motor_test.py
"""

import os
import sys
import time
import struct

PORT = "/dev/ttyS1"
BAUD = 115200

# Protocol
H1, H2 = 0xAA, 0x55
CMD_INIT       = 0x01
CMD_CONFIG     = 0x02
CMD_SET_SPEEDS = 0x13  # dual-motor: left, right (int16 each)
CMD_STOP       = 0x11
CMD_BRAKE      = 0x12


def build_frame(cmd: int, payload: bytes = b"") -> bytes:
    chk = cmd ^ len(payload)
    for b in payload:
        chk ^= b
    return bytes([H1, H2, cmd, len(payload)]) + payload + bytes([chk & 0xFF])


def open_serial(port, baudrate):
    """Open serial port via termios (no pyserial needed)."""
    import termios
    import fcntl

    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)

    # Configure baudrate and raw mode
    attrs = termios.tcgetattr(fd)
    attrs[3] = attrs[3] & ~(termios.ECHO | termios.ICANON | termios.ISIG)
    baud_map = {
        9600: termios.B9600, 19200: termios.B19200,
        38400: termios.B38400, 57600: termios.B57600,
        115200: termios.B115200,
    }
    b = baud_map.get(baudrate, termios.B115200)
    attrs[4] = b  # ispeed
    attrs[5] = b  # ospeed
    termios.tcsetattr(fd, termios.TCSANOW, attrs)

    # Set blocking
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)

    return os.fdopen(fd, 'r+b', buffering=0)


def send(ser, cmd, payload=b""):
    frame = build_frame(cmd, payload)
    ser.write(frame)
    ser.flush()
    print(f"  TX: cmd=0x{cmd:02X} payload={payload.hex() if payload else '-'}")


def main():
    print("=" * 50)
    print("  SG2002 Motor Spin Test")
    print(f"  Port: {PORT} @ {BAUD} baud")
    print("=" * 50)

    # 1. Open serial
    try:
        ser = open_serial(PORT, BAUD)
        print(f"[OK] {PORT} opened")
    except Exception as e:
        print(f"[FAIL] Cannot open {PORT}: {e}")
        sys.exit(1)

    try:
        # 2. Init ESP32
        print("\n[1/5] INIT...")
        send(ser, CMD_INIT)
        time.sleep(0.2)

        # 3. Config
        print("\n[2/5] CONFIG (PPR=4680, PWM=20000Hz)...")
        send(ser, CMD_CONFIG, struct.pack(">HH", 4680, 20000))
        time.sleep(0.2)

        # 4. Test forward: both wheels spin
        print("\n[3/5] FORWARD — both motors speed=50 (should spin forward)")
        send(ser, CMD_SET_SPEEDS, struct.pack(">hh", 50, 50))
        print("  >>> CHECK: Are both wheels spinning forward?")
        print("  >>> Waiting 3 seconds...")
        time.sleep(3)

        # 5. Stop
        print("\n[4/5] STOP — coast")
        send(ser, CMD_STOP, bytes([2]))
        time.sleep(0.5)

        # 6. Test backward
        print("\n[5/5] BACKWARD — both motors speed=-50 (should spin backward)")
        send(ser, CMD_SET_SPEEDS, struct.pack(">hh", -50, -50))
        print("  >>> CHECK: Are both wheels spinning backward?")
        print("  >>> Waiting 3 seconds...")
        time.sleep(3)

        # Stop motors
        print("\n[DONE] Stopping motors...")
        send(ser, CMD_STOP, bytes([2]))

    finally:
        ser.close()
        print(f"\n[OK] {PORT} closed")

    print("\n" + "=" * 50)
    print("  TEST COMPLETE")
    print("  If wheels moved: ESP32 / DRV8833 / N20 motors all OK!")
    print("  If no movement: check wiring, power (battery voltage),")
    print("    and serial protocol compatibility.")
    print("=" * 50)


if __name__ == "__main__":
    main()
