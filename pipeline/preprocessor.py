"""
preprocessor.py — BGR bytes → CHW planar uint8 for TPU.

StarryOS:  C acceleration lib (preprocess_ops.so) via ctypes — ~70ms
PC:        numpy + OpenCV fallback for testing

No hard numpy dependency — only imported in PC fallback path.
"""

import ctypes
import time
import os
from typing import Optional


# ═══════════════════════════════════════════════════════════════════════
# C library interface — StarryOS / SG2002 board
# ═══════════════════════════════════════════════════════════════════════

class _CPreprocessor:
    """ctypes wrapper for preprocess_ops.so on StarryOS."""

    def __init__(self, lib_path: str):
        if not os.path.exists(lib_path):
            raise FileNotFoundError(f"preprocess_ops.so not found: {lib_path}")
        self._lib = ctypes.CDLL(lib_path)

        # bgr_resize_planar(uint8* bgr, int sw, int sh, uint8* out, int dw, int dh) → int
        self._lib.bgr_resize_planar.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int, ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_int, ctypes.c_int,
        ]
        self._lib.bgr_resize_planar.restype = ctypes.c_int

        # bgr_letterbox_planar (same signature)
        self._lib.bgr_letterbox_planar.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int, ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_int, ctypes.c_int,
        ]
        self._lib.bgr_letterbox_planar.restype = ctypes.c_int

        # compute_letterbox
        self._lib.compute_letterbox.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
        ]

        # rgb_to_bgr_inplace
        self._lib.rgb_to_bgr_inplace.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
        ]

    def resize_planar(self, bgr_bytes: bytes, src_w: int, src_h: int,
                      dst_w: int, dst_h: int) -> bytes:
        """
        BGR interleaved bytes → CHW planar uint8 bytes.

        Args:
            bgr_bytes: raw BGR interleaved, length = src_w * src_h * 3
            src_w, src_h: source dimensions
            dst_w, dst_h: target dimensions (640×640 for TPU)

        Returns:
            bytes of length dst_w * dst_h * 3, CHW planar uint8
        """
        out_size = dst_w * dst_h * 3
        out_buf = ctypes.create_string_buffer(out_size)
        src_arr = (ctypes.c_uint8 * len(bgr_bytes)).from_buffer_copy(bgr_bytes)

        rc = self._lib.bgr_resize_planar(
            src_arr, src_w, src_h, out_buf, dst_w, dst_h
        )
        if rc != 0:
            raise RuntimeError(f"bgr_resize_planar failed: rc={rc}")

        return bytes(out_buf)

    def letterbox_planar(self, bgr_bytes: bytes, src_w: int, src_h: int,
                         dst_w: int, dst_h: int) -> bytes:
        """BGR → CHW planar with letterbox padding."""
        out_size = dst_w * dst_h * 3
        out_buf = ctypes.create_string_buffer(out_size)
        src_arr = (ctypes.c_uint8 * len(bgr_bytes)).from_buffer_copy(bgr_bytes)

        rc = self._lib.bgr_letterbox_planar(
            src_arr, src_w, src_h, out_buf, dst_w, dst_h
        )
        if rc != 0:
            raise RuntimeError(f"bgr_letterbox_planar failed: rc={rc}")

        return bytes(out_buf)

    def yuyv_resize_planar(self, yuyv_bytes: bytes, src_w: int, src_h: int,
                            dst_w: int, dst_h: int) -> bytes:
        """
        YUYV422 raw bytes → CHW planar BGR uint8.
        Fused: YUV→BGR conversion + bilinear resize, single C call.
        """
        out_size = dst_w * dst_h * 3
        out_buf = ctypes.create_string_buffer(out_size)
        src_arr = (ctypes.c_uint8 * len(yuyv_bytes)).from_buffer_copy(yuyv_bytes)

        rc = self._lib.yuyv_resize_planar(
            src_arr, src_w, src_h, out_buf, dst_w, dst_h
        )
        if rc != 0:
            raise RuntimeError(f"yuyv_resize_planar failed: rc={rc}")

        return bytes(out_buf)


# ═══════════════════════════════════════════════════════════════════════
# JPEG decode helper — StarryOS uses PIL, PC can use cv2
# ═══════════════════════════════════════════════════════════════════════

def _jpeg_to_bgr_bytes(path: str) -> tuple:
    """
    Decode JPEG/PNG to BGR interleaved bytes.

    On StarryOS: uses PIL (Pillow)
    On PC:       uses cv2 (faster)

    Returns: (bgr_bytes, width, height)
    """
    try:
        import cv2
        bgr = cv2.imread(path)
        if bgr is None:
            raise RuntimeError(f"Cannot read: {path}")
        h, w = bgr.shape[:2]
        return bgr.tobytes(), w, h
    except ImportError:
        # StarryOS path: PIL
        from PIL import Image
        img = Image.open(path).convert("RGB")
        w, h = img.size
        rgb_bytes = img.tobytes()
        # RGB → BGR: swap every 3 bytes
        bgr = bytearray(len(rgb_bytes))
        for i in range(w * h):
            bgr[i * 3 + 0] = rgb_bytes[i * 3 + 2]
            bgr[i * 3 + 1] = rgb_bytes[i * 3 + 1]
            bgr[i * 3 + 2] = rgb_bytes[i * 3 + 0]
        return bytes(bgr), w, h


# ═══════════════════════════════════════════════════════════════════════
# Numpy fallback (PC mode only)
# ═══════════════════════════════════════════════════════════════════════

def _yuyv_to_bgr_numpy(yuyv: bytes, w: int, h: int):
    """PC fallback: YUYV422 → BGR numpy array (ITU-R BT.601)."""
    import numpy as np
    yuv = np.frombuffer(yuyv, dtype=np.uint8).reshape((h, w, 2))
    # YUYV: [Y0, U, Y1, V] per 2 pixels
    y0 = yuv[:, 0::2, 0].astype(np.float32)           # even columns, channel 0 = Y
    u  = yuv[:, 0::2, 1].astype(np.float32) - 128.0    # U from even columns
    y1 = yuv[:, 1::2, 0].astype(np.float32)            # odd columns, channel 0 = Y
    v  = yuv[:, 0::2, 1].astype(np.float32) - 128.0    # V from even columns (same as U position)

    # Replicate U,V to match Y resolution
    u_wide = np.repeat(u, 2, axis=1)
    v_wide = np.repeat(v, 2, axis=1)
    yy = yuv[:, :, 0].astype(np.float32)  # all Y values, shape (h, w)

    c = yy - 16.0
    r = np.clip((298*c + 409*v_wide + 128) / 256, 0, 255).astype(np.uint8)
    g = np.clip((298*c - 100*u_wide - 208*v_wide + 128) / 256, 0, 255).astype(np.uint8)
    b = np.clip((298*c + 516*u_wide + 128) / 256, 0, 255).astype(np.uint8)

    return np.stack([b, g, r], axis=2)  # HWC BGR


def _resize_planar_numpy(bgr_bytes: bytes, src_w: int, src_h: int,
                         dst_w: int, dst_h: int) -> bytes:
    """PC numpy fallback — functionally equivalent to C version."""
    import numpy as np
    import cv2
    bgr = np.frombuffer(bgr_bytes, dtype=np.uint8).reshape((src_h, src_w, 3))
    resized = cv2.resize(bgr, (dst_w, dst_h), interpolation=cv2.INTER_LINEAR)
    planar = np.transpose(resized, (2, 0, 1))  # (3, H, W)
    return planar.astype(np.uint8).tobytes()


# ═══════════════════════════════════════════════════════════════════════
# Preprocessor — unified interface
# ═══════════════════════════════════════════════════════════════════════

class Preprocessor:
    """
    Convert BGR frame → CHW planar uint8 bytes for TPU.

    On StarryOS: uses C preprocess_ops.so (~5ms)
    On PC:       uses numpy/opencv fallback

    Usage:
        pp = Preprocessor()
        planar_bytes = pp.process_frame(bgr_bytes, src_w, src_h)
        # → 640*640*3 bytes, CHW planar uint8, direct TPU input

        pp = Preprocessor()
        planar_bytes = pp.process_jpeg("/images/ball.jpg")
        # → decode + resize in one call
    """

    def __init__(self, target_w: int = 640, target_h: int = 640,
                 lib_path: str = "/lib/preprocess_ops.so",
                 use_c: bool = None):
        self.target_w = target_w
        self.target_h = target_h
        self._c: Optional[_CPreprocessor] = None
        self._using_c = False
        self._out_size = target_w * target_h * 3

        if use_c is not False:
            for path in [lib_path, "/akars_tennis/lib/preprocess_ops.so",
                         "./preprocess_ops.so"]:
                try:
                    self._c = _CPreprocessor(path)
                    self._using_c = True
                    break
                except (FileNotFoundError, OSError):
                    continue

    def process_bgr_bytes(self, bgr_bytes: bytes, src_w: int, src_h: int) -> bytes:
        """
        Raw image bytes → CHW planar uint8.
        Auto-detects format: BGR (len=W*H*3) or YUYV (len=W*H*2).
        """
        expected_bgr = src_w * src_h * 3
        expected_yuyv = src_w * src_h * 2

        if len(bgr_bytes) == expected_yuyv:
            # YUYV422 format
            if self._using_c and self._c:
                return self._c.yuyv_resize_planar(
                    bgr_bytes, src_w, src_h, self.target_w, self.target_h)
            else:
                # PC fallback: convert YUYV to BGR, then numpy resize
                import numpy as np
                bgr = _yuyv_to_bgr_numpy(bgr_bytes, src_w, src_h)
                return _resize_planar_numpy(
                    bgr.tobytes(), src_w, src_h, self.target_w, self.target_h)

        elif len(bgr_bytes) == expected_bgr:
            # BGR interleaved
            if self._using_c and self._c:
                return self._c.resize_planar(
                    bgr_bytes, src_w, src_h, self.target_w, self.target_h)
            else:
                return _resize_planar_numpy(
                    bgr_bytes, src_w, src_h, self.target_w, self.target_h)

        else:
            raise ValueError(
                f"Expected {expected_bgr}B (BGR) or {expected_yuyv}B (YUYV), "
                f"got {len(bgr_bytes)}B")

    def process_jpeg(self, path: str) -> tuple:
        """
        JPEG/PNG file → CHW planar bytes.

        Returns:
            (planar_bytes, elapsed_seconds)
        """
        t0 = time.time()
        bgr_bytes, w, h = _jpeg_to_bgr_bytes(path)
        planar = self.process_bgr_bytes(bgr_bytes, w, h)
        elapsed = time.time() - t0
        return planar, elapsed

    @property
    def using_c(self) -> bool:
        return self._using_c

    def __repr__(self):
        backend = "C" if self._using_c else "numpy"
        return f"Preprocessor({self.target_w}x{self.target_h}, backend={backend})"
