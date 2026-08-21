#!/usr/bin/env python3
"""SG2002 UART Register Diagnostic — run on the board:
   PYTHONHOME=/ PYTHONPATH=/ python3 /pipeline/regdump.py
"""
import struct
import mmap
import os

# ─── Register addresses (physical) ──────────────────────────────────────────
REGISTERS = {
    # FMUX — pin function mux
    "FMUX_JTAG_CPU_TMS": 0x0300_1064,   # UART1 TX (should be 0x6 after pinmux)
    "FMUX_JTAG_CPU_TCK": 0x0300_1068,   # UART1 RX (should be 0x6)
    "FMUX_GPIOA28":      0x0300_1070,   # Default UART1_TX (should be 0x1)
    "FMUX_GPIOA29":      0x0300_1074,   # Default UART1_RX (should be 0x1)
    "FMUX_PWR_GPIO0":    0x0300_10A4,   # UART2 TX
    "FMUX_PWR_GPIO1":    0x0300_10A8,   # UART2 RX

    # CLKGEN — clock generator
    "CLKGEN_CLK_EN_0":   0x0300_2000,   # UART0, ETH, SD clocks
    "CLKGEN_CLK_EN_1":   0x0300_2004,   # UART1/2 clocks: bits 16-19

    # RSTC — reset controller
    "RSTC_SOFT_RSTN_0":  0x0300_3000,   # UART1/2 reset: bits 24-25
}

# ─── Helper ─────────────────────────────────────────────────────────────────
PAGE_SIZE = os.sysconf("SC_PAGE_SIZE")

def read32(phys_addr):
    """Read a 32-bit word from physical memory via /dev/mem."""
    base = phys_addr & ~(PAGE_SIZE - 1)
    offset = phys_addr - base
    with open("/dev/mem", "rb") as f:
        mm = mmap.mmap(f.fileno(), PAGE_SIZE, mmap.MAP_SHARED,
                       mmap.PROT_READ, offset=base)
        mm.seek(offset)
        val = struct.unpack("<I", mm.read(4))[0]
        mm.close()
    return val

# ─── Main ───────────────────────────────────────────────────────────────────
print("=" * 64)
print("  SG2002 UART Register Diagnostic")
print("=" * 64)

for name, addr in REGISTERS.items():
    try:
        val = read32(addr)
        bits = f"{val:032b}"
        # Group bits for readability
        bits_str = "_".join([bits[i:i+4] for i in range(0, 32, 4)])
        print(f"  {name:24s} @ 0x{addr:08X} = 0x{val:08X}  {bits_str}")
    except Exception as e:
        print(f"  {name:24s} @ 0x{addr:08X} = ERROR: {e}")

# ─── Analysis ────────────────────────────────────────────────────────────────
print()
print("─" * 64)
print("  Quick Check:")
print("─" * 64)

try:
    fmux_tms = read32(0x0300_1064)
    fmux_tck = read32(0x0300_1068)
    clk_en1  = read32(0x0300_2004)
    rstn0    = read32(0x0300_3000)

    ok = True

    # FMUX: expect 0x6 (UART1_TX/RX) on JTAG pins
    if fmux_tms == 0x6:
        print("  [OK] JTAG_CPU_TMS → UART1_TX (0x6)")
    else:
        print(f"  [FAIL] JTAG_CPU_TMS = 0x{fmux_tms:X}, expected 0x6")
        ok = False

    if fmux_tck == 0x6:
        print("  [OK] JTAG_CPU_TCK → UART1_RX (0x6)")
    else:
        print(f"  [FAIL] JTAG_CPU_TCK = 0x{fmux_tck:X}, expected 0x6")
        ok = False

    # CLKGEN: expect bits 16-19 set
    expected_clk = (1<<16) | (1<<17) | (1<<18) | (1<<19)
    if (clk_en1 & expected_clk) == expected_clk:
        print(f"  [OK] CLK_EN_1 UART1/2 clocks enabled (0x{clk_en1:08X})")
    else:
        missing = []
        if not (clk_en1 & (1<<16)): missing.append("clk_uart1")
        if not (clk_en1 & (1<<17)): missing.append("clk_apb_uart1")
        if not (clk_en1 & (1<<18)): missing.append("clk_uart2")
        if not (clk_en1 & (1<<19)): missing.append("clk_apb_uart2")
        print(f"  [FAIL] CLK_EN_1 = 0x{clk_en1:08X}, missing: {missing}")
        ok = False

    # RSTC: expect bits 24-25 set
    if (rstn0 & (1<<24)) and (rstn0 & (1<<25)):
        print(f"  [OK] RSTC UART1/2 reset released (0x{rstn0:08X})")
    else:
        print(f"  [FAIL] RSTC = 0x{rstn0:08X}, UART1_rst={bool(rstn0 & (1<<24))}, UART2_rst={bool(rstn0 & (1<<25))}")
        ok = False

    print()
    if ok:
        print("  All hardware registers correct — issue is elsewhere (wiring/software)")
    else:
        print("  Register mismatch detected — kernel init may not have run!")

except Exception as e:
    print(f"  Analysis error: {e}")
