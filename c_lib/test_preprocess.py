#!/usr/bin/env python3
"""
test_preprocess.py — 板上验证 preprocess_ops.so 预处理性能
============================================================
StarryOS 兼容：只依赖 Python 标准库 + ctypes + PIL（无 cv2，无 numpy）

用法 (在 SG2002 板子上):
    export PYTHONHOME=/
    python3 /test_preprocess.py /images/tennis-ball-close.jpg

也可不传参数，自动生成测试图案验证。
"""

import ctypes
import sys
import os
import time
import struct

LIB_PATH = "/lib/preprocess_ops.so"

print("=" * 55)
print("  preprocess_ops.so v2 Test — StarryOS")
print("=" * 55)

# ═══════════════════════════════════════════════════════════════════
# 检查库是否存在
# ═══════════════════════════════════════════════════════════════════

if not os.path.exists(LIB_PATH):
    print(f"\n  ERROR: {LIB_PATH} not found!")
    print(f"  Copy preprocess_ops.so to SD card first:")
    print(f"    mount /dev/sde2 /mnt/sddata")
    print(f"    cp preprocess_ops.so /mnt/sddata/lib/")
    print(f"    umount /mnt/sddata && sync")
    sys.exit(1)

lib = ctypes.CDLL(LIB_PATH)

# ── 函数签名 ──
# int bgr_resize_planar(uint8* bgr, int sw, int sh, uint8* out, int dw, int dh)
lib.bgr_resize_planar.argtypes = [
    ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
    ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
]
lib.bgr_resize_planar.restype = ctypes.c_int

# int bgr_letterbox_planar(uint8* bgr, int sw, int sh, uint8* out, int dw, int dh)
lib.bgr_letterbox_planar.argtypes = [
    ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
    ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
]
lib.bgr_letterbox_planar.restype = ctypes.c_int

# void compute_letterbox(int sw,int sh, int dw,int dh, float*sc, int*nw,int*nh, int*pl,int*pt)
lib.compute_letterbox.argtypes = [
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.POINTER(ctypes.c_float),
    ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
    ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
]

# void rgb_to_bgr_inplace(uint8* data, int w, int h)
lib.rgb_to_bgr_inplace.argtypes = [
    ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
]

# const char* preprocess_ops_version()
lib.preprocess_ops_version.restype = ctypes.c_char_p

print(f"  Library:  {LIB_PATH}")
print(f"  Version:  {lib.preprocess_ops_version().decode()}")
print(f"  Size:     {os.path.getsize(LIB_PATH)} bytes")

# ═══════════════════════════════════════════════════════════════════
# 获取测试数据
# ═══════════════════════════════════════════════════════════════════

DST_W, DST_H = 640, 640
PLANE_SZ = DST_W * DST_H
OUT_SZ = PLANE_SZ * 3  # 3-channel CHW planar

src_bytes = None
src_w = src_h = 0
image_path = None

if len(sys.argv) > 1:
    image_path = sys.argv[1]

if image_path and image_path.lower().endswith((".jpg", ".jpeg", ".png")):
    # ── 真实图片（需要 PIL）──
    try:
        from PIL import Image
        img = Image.open(image_path).convert("RGB")
        src_w, src_h = img.size
        src_bytes = img.tobytes()  # RGB interleaved
        print(f"\n  Image:    {image_path}")
        print(f"  Size:     {src_w}x{src_h}")
        print(f"  Bytes:    {len(src_bytes)} (RGB interleaved)")
    except ImportError:
        print(f"\n  ERROR: PIL not available. Install Pillow or use test pattern.")
        print(f"  Try: pip install Pillow")
        sys.exit(1)
else:
    # ── 无参数 / 不支持格式 → 生成测试图案 ──
    src_w, src_h = 320, 240
    print(f"\n  Test pattern: {src_w}x{src_h} BGR gradient")
    # 生成 BGR 渐变色块（不需要任何库）
    src_bytes = bytearray(src_w * src_h * 3)
    for y in range(src_h):
        for x in range(src_w):
            off = (y * src_w + x) * 3
            src_bytes[off + 0] = x * 255 // src_w         # B gradient →
            src_bytes[off + 1] = y * 255 // src_h         # G gradient ↓
            src_bytes[off + 2] = (x + y) * 255 // (src_w + src_h)  # R gradient ↘
    src_bytes = bytes(src_bytes)

# ═══════════════════════════════════════════════════════════════════
# 测试 1: bgr_resize_planar — 直接 resize + CHW planar
# ═══════════════════════════════════════════════════════════════════

print(f"\n{'─' * 55}")
print(f"  Test 1: bgr_resize_planar  {src_w}x{src_h} → {DST_W}x{DST_H}")

out_buf = ctypes.create_string_buffer(OUT_SZ)
src_arr = (ctypes.c_uint8 * len(src_bytes)).from_buffer_copy(src_bytes)

# 预热
lib.bgr_resize_planar(src_arr, src_w, src_h, out_buf, DST_W, DST_H)

# 测速 (10 轮)
times = []
for _ in range(10):
    t0 = time.time()
    rc = lib.bgr_resize_planar(src_arr, src_w, src_h, out_buf, DST_W, DST_H)
    ms = (time.time() - t0) * 1000
    times.append(ms)

avg_ms = sum(times) / len(times)
out_raw = bytes(out_buf)

print(f"  rc = {rc}  (0 = success)")
print(f"  Time: avg={avg_ms:.1f}ms  min={min(times):.1f}ms  max={max(times):.1f}ms")
print(f"  Speed: {1000/avg_ms:.0f} fps (preprocess only)")

# 基本完整性检查
planes = [out_raw[i*PLANE_SZ:(i+1)*PLANE_SZ] for i in range(3)]

# 检查每个 plane 第一个/中间/最后一个值
b_plane = planes[0]
g_plane = planes[1]
r_plane = planes[2]

mid = PLANE_SZ // 2 + DST_W // 2
print(f"  B plane:  [0]={b_plane[0]}  [mid]={b_plane[mid]}  [-1]={b_plane[-1]}")
print(f"  G plane:  [0]={g_plane[0]}  [mid]={g_plane[mid]}  [-1]={g_plane[-1]}")
print(f"  R plane:  [0]={r_plane[0]}  [mid]={r_plane[mid]}  [-1]={r_plane[-1]}")

# 检查是否全零（说明输入有问题）
non_zero = sum(1 for b in out_raw if b != 0)
pct = 100.0 * non_zero / OUT_SZ
print(f"  Non-zero pixels: {non_zero}/{OUT_SZ} ({pct:.1f}%)")
if pct < 1.0:
    print(f"  WARNING: output is >99% zeros — input may be black!")

# ═══════════════════════════════════════════════════════════════════
# 测试 2: bgr_letterbox_planar
# ═══════════════════════════════════════════════════════════════════

print(f"\n{'─' * 55}")
print(f"  Test 2: bgr_letterbox_planar  {src_w}x{src_h} → {DST_W}x{DST_H}")

out2 = ctypes.create_string_buffer(OUT_SZ)

t0 = time.time()
rc = lib.bgr_letterbox_planar(src_arr, src_w, src_h, out2, DST_W, DST_H)
ms = (time.time() - t0) * 1000

raw2 = bytes(out2)

# 检查 padding 区域（应该是 114）
# letterbox 情况下，如果 src 不是正方形，四个角应该有 padding
corners_ok = True
for corner_idx in [0, DST_W-1, (DST_H-1)*DST_W, DST_H*DST_W-1]:
    b_val = raw2[corner_idx]
    g_val = raw2[PLANE_SZ + corner_idx]
    r_val = raw2[2*PLANE_SZ + corner_idx]
    if b_val != 114 or g_val != 114 or r_val != 114:
        corners_ok = False
        # Only warn if this isn't 1:1 resize
        if src_w != DST_W or src_h != DST_H:
            print(f"  Corner [{corner_idx}] B={b_val} G={g_val} R={r_val} (expected 114=gray)")

print(f"  rc = {rc}")
print(f"  Time: {ms:.1f}ms")

# ═══════════════════════════════════════════════════════════════════
# 测试 3: compute_letterbox 辅助函数
# ═══════════════════════════════════════════════════════════════════

print(f"\n{'─' * 55}")
print(f"  Test 3: compute_letterbox({src_w},{src_h} → {DST_W},{DST_H})")

scale = ctypes.c_float()
nw, nh = ctypes.c_int(), ctypes.c_int()
pl, pt = ctypes.c_int(), ctypes.c_int()

lib.compute_letterbox(src_w, src_h, DST_W, DST_H,
                       ctypes.byref(scale), ctypes.byref(nw), ctypes.byref(nh),
                       ctypes.byref(pl), ctypes.byref(pt))

print(f"  scale={scale.value:.4f}  new={nw.value}x{nh.value}  "
      f"pad_left={pl.value}  pad_top={pt.value}")

# ═══════════════════════════════════════════════════════════════════
# 测试 4: 与 Python PIL 对比（如果可用）
# ═══════════════════════════════════════════════════════════════════

if image_path:
    try:
        from PIL import Image
        print(f"\n{'─' * 55}")
        print(f"  Test 4: C vs PIL comparison")

        # PIL 参考实现
        t0 = time.time()
        img = Image.open(image_path).convert("RGB")
        scale = min(DST_W / src_w, DST_H / src_h)
        nw = max(1, int(src_w * scale))
        nh = max(1, int(src_h * scale))
        img = img.resize((nw, nh), 2)  # BILINEAR
        canvas = Image.new("RGB", (DST_W, DST_H), (114, 114, 114))
        pl_val = (DST_W - nw) // 2
        pt_val = (DST_H - nh) // 2
        canvas.paste(img, (pl_val, pt_val))
        pil_bytes = canvas.tobytes()  # RGB interleaved HWC
        pil_ms = (time.time() - t0) * 1000

        # C 实现（letterbox 模式）
        t0 = time.time()
        lib.bgr_letterbox_planar(src_arr, src_w, src_h, out2, DST_W, DST_H)
        c_ms = (time.time() - t0) * 1000

        # 对比：C 输出是 CHW planar，PIL 是 HWC interleaved
        # 取中间一行的 G 通道对比
        mid_y = DST_H // 2
        c_row = raw2[PLANE_SZ + mid_y * DST_W : PLANE_SZ + (mid_y + 1) * DST_W]
        pil_row_g = [pil_bytes[(mid_y * DST_W + x) * 3 + 1] for x in range(DST_W)]

        diffs = [abs(c_row[x] - pil_row_g[x]) for x in range(DST_W)]
        max_d = max(diffs)
        avg_d = sum(diffs) / len(diffs)
        within_2 = sum(1 for d in diffs if d <= 2) / len(diffs) * 100

        print(f"  C time:    {c_ms:.1f}ms")
        print(f"  PIL time:  {pil_ms:.1f}ms")
        print(f"  Speedup:   {pil_ms/c_ms:.1f}x")
        print(f"  G-channel mid-row: max_diff={max_d}  avg_diff={avg_d:.2f}")
        print(f"  Within ±2:  {within_2:.1f}%")
        if max_d <= 5:
            print(f"  Status:     PASS (max diff <= 5)")
        else:
            print(f"  Status:     LARGE DIFF — check input pixel format!")
    except ImportError:
        pass  # no PIL, skip comparison

# ═══════════════════════════════════════════════════════════════════
print(f"\n{'=' * 55}")
print(f"  Test complete.")
print(f"")
print(f"  To copy to SD card:")
print(f"    # In WSL:")
print(f"    mount /dev/sde2 /mnt/sddata")
print(f"    cp preprocess_ops.so /mnt/sddata/lib/")
print(f"    umount /mnt/sddata && sync")
print(f"")
print(f"  To use in Python pipeline:")
print(f"    lib = ctypes.CDLL('/lib/preprocess_ops.so')")
print(f"    lib.bgr_resize_planar(bgr_bytes, w, h, out_buf, 640, 640)")
print(f"    # output is CHW planar uint8, directly TPU-compatible")
print(f"{'=' * 55}")
