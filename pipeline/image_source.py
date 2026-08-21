"""
image_source.py — Frame acquisition abstraction layer.

This is the ONLY module that will change when switching from local images
to Li Mingtao's real-time camera. Every source implements get_frame() →
returns (bgr_bytes, width, height).

No numpy dependency — works on bare StarryOS Python 3.11.
"""

import sys
import os
import struct
import time
import subprocess
import signal
from abc import ABC, abstractmethod
from typing import Optional, Tuple


# ═══════════════════════════════════════════════════════════════════════
# Frame header struct (agreed format with Li Mingtao)
# ═══════════════════════════════════════════════════════════════════════

FRAME_HEADER_SIZE = 24
FRAME_MAGIC = 0xC0C0C0C0


class FrameHeader:
    __slots__ = ("width", "height", "channels", "timestamp_ms", "pixel_count")

    @classmethod
    def parse(cls, data: bytes) -> Optional["FrameHeader"]:
        if len(data) < FRAME_HEADER_SIZE:
            return None
        magic, width, height, channels, timestamp_ms = struct.unpack(
            "<IIIId", data
        )
        if magic != FRAME_MAGIC:
            return None
        hdr = cls()
        hdr.width = width
        hdr.height = height
        hdr.channels = channels
        hdr.timestamp_ms = timestamp_ms
        hdr.pixel_count = width * height * channels
        return hdr


# ═══════════════════════════════════════════════════════════════════════
# Abstract interface: get_frame() → (bgr_bytes, width, height)
# ═══════════════════════════════════════════════════════════════════════

class ImageSource(ABC):
    @abstractmethod
    def get_frame(self) -> Tuple[bytes, int, int]:
        """Return (bgr_bytes, width, height). Raises EOFError on end."""
        ...

    def close(self): pass
    def __enter__(self): return self
    def __exit__(self, *args): self.close()


# ═══════════════════════════════════════════════════════════════════════
# JPEG decode — PIL on StarryOS, cv2 on PC
# ═══════════════════════════════════════════════════════════════════════

def _jpeg_to_bgr(path: str) -> Tuple[bytes, int, int]:
    """Decode image file → (bgr_bytes, width, height)."""
    try:
        import cv2
        import numpy as np
        bgr = cv2.imread(path)
        if bgr is None:
            raise RuntimeError(f"Cannot read: {path}")
        h, w = bgr.shape[:2]
        return bgr.tobytes(), w, h
    except ImportError:
        pass

    # StarryOS path: PIL
    try:
        from PIL import Image
        img = Image.open(path).convert("RGB")
        w, h = img.size
        rgb = img.tobytes()
        # RGB → BGR in-place
        bgr = bytearray(len(rgb))
        for i in range(w * h):
            bgr[i*3 + 0] = rgb[i*3 + 2]
            bgr[i*3 + 1] = rgb[i*3 + 1]
            bgr[i*3 + 2] = rgb[i*3 + 0]
        return bytes(bgr), w, h
    except ImportError:
        raise RuntimeError(
            "No image decoder available. Install opencv-python (PC) or Pillow (StarryOS)."
        )


# ═══════════════════════════════════════════════════════════════════════
# LocalImageSource — static image file
# ═══════════════════════════════════════════════════════════════════════

class LocalImageSource(ImageSource):
    """Read a single image. loop=True → repeat forever."""

    def __init__(self, image_path: str, loop: bool = True):
        self.image_path = image_path
        self.loop = loop
        self._data: Optional[Tuple[bytes, int, int]] = None
        self._sent = False

    def get_frame(self) -> Tuple[bytes, int, int]:
        if self._data is None:
            self._data = _jpeg_to_bgr(self.image_path)

        if self.loop:
            return self._data

        if self._sent:
            raise EOFError("No more frames (loop=False)")
        self._sent = True
        return self._data

    def __repr__(self):
        return f"LocalImageSource({self.image_path!r}, loop={self.loop})"


# ═══════════════════════════════════════════════════════════════════════
# Int8ImageSource — pre-quantized .int8 files (no decode needed)
# ═══════════════════════════════════════════════════════════════════════

class Int8ImageSource(ImageSource):
    """
    Read PC-generated .int8 files.
    Format: [ch:4B][h:4B][w:4B][raw CHW planar uint8 bytes]
    Returns (planar_bytes, 0, 0) — pipeline skips preprocessing.
    """

    def __init__(self, path: str, loop: bool = True):
        with open(path, "rb") as f:
            c = struct.unpack("<I", f.read(4))[0]
            h = struct.unpack("<I", f.read(4))[0]
            w = struct.unpack("<I", f.read(4))[0]
            self._data = f.read(c * h * w)
        self._size = len(self._data)
        self.loop = loop
        self._sent = False

    def get_frame(self) -> Tuple[bytes, int, int]:
        # (0, 0) signals "INT8 CHW planar, skip preprocessing"
        if self.loop:
            return (self._data, 0, 0)
        if self._sent:
            raise EOFError("No more frames (loop=False)")
        self._sent = True
        return (self._data, 0, 0)

    def __repr__(self):
        return f"Int8ImageSource({self._size}B CHW planar, loop={self.loop})"


# ═══════════════════════════════════════════════════════════════════════
# CameraImageSource — Li Mingtao's C++ camera subprocess
# ═══════════════════════════════════════════════════════════════════════

class CameraImageSource(ImageSource):
    """
    Launch camera binary as subprocess, read binary frames from stdout.

    Protocol: [magic:4B][w:4B][h:4B][c:4B][ts:8B][pixels: W*H*C]
    """

    def __init__(self, binary_path: str, args: list = None,
                 pixel_format: str = "BGR"):
        self.pixel_format = pixel_format.upper()
        cmd = [binary_path] + (args or [])
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
        )
        self._buf = b""

    def get_frame(self) -> Tuple[bytes, int, int]:
        # Ensure we have at least a header
        while len(self._buf) < FRAME_HEADER_SIZE:
            chunk = self._proc.stdout.read(4096)
            if not chunk:
                rc = self._proc.poll()
                if rc is not None:
                    err = self._proc.stderr.read().decode(errors="replace")
                    raise EOFError(f"Camera exited ({rc}): {err}")
                continue
            self._buf += chunk

        # Sync to magic
        while True:
            hdr = FrameHeader.parse(self._buf[:FRAME_HEADER_SIZE])
            if hdr is not None:
                break
            self._buf = self._buf[1:]
            while len(self._buf) < FRAME_HEADER_SIZE:
                chunk = self._proc.stdout.read(4096)
                if not chunk:
                    raise EOFError("Stream ended during sync")
                self._buf += chunk

        frame_bytes = FRAME_HEADER_SIZE + hdr.pixel_count
        while len(self._buf) < frame_bytes:
            chunk = self._proc.stdout.read(max(4096, frame_bytes - len(self._buf)))
            if not chunk:
                raise EOFError("Stream ended mid-frame")
            self._buf += chunk

        pixel_data = self._buf[FRAME_HEADER_SIZE:frame_bytes]
        self._buf = self._buf[frame_bytes:]

        # RGB → BGR if needed
        if self.pixel_format == "RGB":
            bgr = bytearray(len(pixel_data))
            n = hdr.width * hdr.height
            for i in range(n):
                bgr[i*3+0] = pixel_data[i*3+2]
                bgr[i*3+1] = pixel_data[i*3+1]
                bgr[i*3+2] = pixel_data[i*3+0]
            pixel_data = bytes(bgr)

        return pixel_data, hdr.width, hdr.height

    def close(self):
        if self._proc and self._proc.poll() is None:
            self._proc.send_signal(signal.SIGTERM)
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill(); self._proc.wait()

    def __repr__(self):
        return f"CameraImageSource({self.binary_path!r})"


# ═══════════════════════════════════════════════════════════════════════
# RawYUYVSource — pure YUYV frames from Li Mingtao's 2.camera
# ═══════════════════════════════════════════════════════════════════════

class RawYUYVSource(ImageSource):
    """
    Read raw YUYV frames from camera binary (no magic header).

    2.camera outputs diagnostic text lines on stderr, then continuous
    raw YUYV422 frames (W*H*2 bytes each) on stdout.

    This source skips initial text lines, then reads fixed-size YUYV frames.
    Returns (yuyv_bytes, width, height) — Preprocessor auto-detects YUYV.

    Reads are plain BLOCKING reads (2026-08-21): the only mode verified to run
    at real-time (~210ms/frame) on the current StarryOS kernel. Non-blocking
    fds, select() and reader-threads all regressed badly on the board (kernel
    still being optimised). If the camera's UVC DMA hangs, get_frame() blocks —
    the motor watchdog in real_pipeline.py then brakes the car (safety net) and
    the board is power-cycled. Camera self-recovery is deferred until the
    kernel's pipe semantics stabilise.
    """

    def __init__(self, binary_path: str, width: int = 640, height: int = 480,
                 args: list = None):
        self.binary_path = binary_path
        self.width = width
        self.height = height
        self._frame_size = width * height * 2  # YUYV422 = 2 bytes/pixel
        self._args = args
        self._header_skipped = False
        self._proc = None
        self._buf = b""
        self._launch()

    def _launch(self):
        cmd = [self.binary_path] + (self._args or [])
        # Default buffered Popen (no bufsize=0) — BufferedReader.read() below is
        # the plain blocking read that runs at ~210ms/frame on the board.
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        self._buf = b""
        self._header_skipped = False

    def _skip_text_header(self):
        """Skip initial diagnostic lines (e.g. 'Device: 640x480 YUYV...')."""
        while len(self._buf) < self._frame_size:
            chunk = self._proc.stdout.read(min(4096, self._frame_size))
            if not chunk:
                rc = self._proc.poll()
                raise EOFError(f"Camera exited ({rc}) before first frame")
            self._buf += chunk

        # Scan for first plausible YUYV frame: skip ASCII header, keep raw pixels.
        for i in range(len(self._buf) - 8):
            chunk = self._buf[i:i+8]
            non_ascii = sum(1 for b in chunk if b < 0x20 or b > 0x7E)
            if non_ascii >= 4:  # mostly non-ASCII → pixel data
                self._buf = self._buf[i:]
                self._header_skipped = True
                return

        # Fallback: consume ~100 bytes (typical header size) and treat rest as frames
        self._buf = self._buf[100:]
        self._header_skipped = True

    def get_frame(self) -> Tuple[bytes, int, int]:
        if not self._header_skipped:
            self._skip_text_header()

        # Ensure we have a full frame (plain blocking read)
        while len(self._buf) < self._frame_size:
            chunk = self._proc.stdout.read(max(4096, self._frame_size - len(self._buf)))
            if not chunk:
                rc = self._proc.poll()
                if rc is not None:
                    raise EOFError(f"Camera exited ({rc})")
                continue
            self._buf += chunk

        frame = self._buf[:self._frame_size]
        self._buf = self._buf[self._frame_size:]
        return bytes(frame), self.width, self.height

    def close(self):
        if self._proc and self._proc.poll() is None:
            self._proc.send_signal(signal.SIGTERM)
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()

    def __repr__(self):
        return f"RawYUYVSource({self.width}x{self.height}, {self._frame_size}B/frame)"


# ═══════════════════════════════════════════════════════════════════════
# StdinImageSource — pipe from mock_camera.py
# ═══════════════════════════════════════════════════════════════════════

class StdinImageSource(ImageSource):
    """Read binary frames from stdin (pipe mode)."""

    def __init__(self, pixel_format: str = "BGR"):
        self.pixel_format = pixel_format.upper()
        self._stdin = sys.stdin.buffer
        self._buf = b""

    def get_frame(self) -> Tuple[bytes, int, int]:
        while len(self._buf) < FRAME_HEADER_SIZE:
            chunk = self._stdin.read(4096)
            if not chunk: raise EOFError("Stdin ended")
            self._buf += chunk

        while True:
            hdr = FrameHeader.parse(self._buf[:FRAME_HEADER_SIZE])
            if hdr is not None: break
            self._buf = self._buf[1:]
            while len(self._buf) < FRAME_HEADER_SIZE:
                chunk = self._stdin.read(4096)
                if not chunk: raise EOFError("Stdin ended during sync")
                self._buf += chunk

        fb = FRAME_HEADER_SIZE + hdr.pixel_count
        while len(self._buf) < fb:
            chunk = self._stdin.read(max(4096, fb - len(self._buf)))
            if not chunk: raise EOFError("Stdin ended mid-frame")
            self._buf += chunk

        pixel_data = self._buf[FRAME_HEADER_SIZE:fb]
        self._buf = self._buf[fb:]

        if self.pixel_format == "RGB":
            bgr = bytearray(len(pixel_data))
            n = hdr.width * hdr.height
            for i in range(n):
                bgr[i*3+0] = pixel_data[i*3+2]
                bgr[i*3+1] = pixel_data[i*3+1]
                bgr[i*3+2] = pixel_data[i*3+0]
            pixel_data = bytes(bgr)

        return pixel_data, hdr.width, hdr.height

    def __repr__(self):
        return "StdinImageSource()"


# ═══════════════════════════════════════════════════════════════════════
# MockCameraSource — simulate camera from local images (PC testing)
# ═══════════════════════════════════════════════════════════════════════

class MockCameraSource(ImageSource):
    """Cycle through local images, throttled to simulate camera FPS."""

    def __init__(self, images: list, fps: float = 15, loop: bool = True):
        self._frames = []
        for path in (images if isinstance(images, list) else [images]):
            data, w, h = _jpeg_to_bgr(path)
            self._frames.append((data, w, h))
        self._idx = 0
        self._interval = 1.0 / fps
        self._last_time = 0.0
        self.loop = loop

    def get_frame(self) -> Tuple[bytes, int, int]:
        now = time.time()
        elapsed = now - self._last_time
        if elapsed < self._interval:
            time.sleep(self._interval - elapsed)
        self._last_time = time.time()

        frame = self._frames[self._idx]
        self._idx += 1
        if self._idx >= len(self._frames):
            if self.loop:
                self._idx = 0
            else:
                raise EOFError("Mock camera: all frames sent")
        return frame

    def __repr__(self):
        return f"MockCameraSource({len(self._frames)} images, loop={self.loop})"
