"""
inference.py — TPU inference + NMS decoding for StarryOS.

Input:  CHW planar uint8 bytes (from Preprocessor)
Output: list[Detection] (NMS-filtered)

Two NMS backends:
    C NMS (fast):  via ctypes → preprocess_ops.so → ~5ms
    Python NMS:    pure Python fallback → ~66ms

Two inference backends:
    Board (StarryOS): sg2002_tpu.TPUEngine → real TPU (~40ms)
    PC:              MockInference (dummy detections for testing)
"""

import struct
import time
import ctypes
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class Detection:
    """A single detection result."""
    class_id: int
    label: str
    confidence: float
    x1: float; y1: float; x2: float; y2: float

    @property
    def center_x(self) -> float: return (self.x1 + self.x2) / 2.0
    @property
    def center_y(self) -> float: return (self.y1 + self.y2) / 2.0
    @property
    def width(self) -> float: return self.x2 - self.x1
    @property
    def height(self) -> float: return self.y2 - self.y1
    @property
    def area(self) -> float: return self.width * self.height

    def __repr__(self):
        return (f"Detection({self.label}, conf={self.confidence:.3f}, "
                f"box=({self.x1:.0f},{self.y1:.0f},{self.x2:.0f},{self.y2:.0f}))")


class InferenceEngine(ABC):
    @abstractmethod
    def infer(self, planar_bytes: bytes) -> list[Detection]: ...
    @property
    @abstractmethod
    def input_size(self) -> tuple: ...  # (w, h)
    def close(self): pass
    def __enter__(self): return self
    def __exit__(self, *args): self.close()


# ═══════════════════════════════════════════════════════════════════════
# TPUInference — real TPU on StarryOS via sg2002_tpu
# ═══════════════════════════════════════════════════════════════════════

class TPUInference(InferenceEngine):
    """Real TPU on SG2002/StarryOS. Forward ~40ms, NMS ~5ms (C) or ~66ms (Python)."""

    def __init__(self, model_path: str = "/akars_tennis/model/yolov8n_tennis_v2.cvimodel",
                 conf_threshold: float = 0.5, nms_iou: float = 0.45,
                 class_labels: dict = None,
                 c_lib_path: str = "/lib/preprocess_ops.so"):
        from sg2002_tpu import TPUEngine
        self._engine = TPUEngine(model_path)
        self._conf_thresh = conf_threshold
        self._iou_thresh = nms_iou
        self._labels = class_labels or {0: "object"}
        self._input_w = self._engine.in_shape[3]
        self._input_h = self._engine.in_shape[2]
        self._expected_size = self._input_w * self._input_h * 3

        # ── C NMS acceleration (optional) ──────────────────────────
        self._c_lib = None
        self._c_nms_fn = None
        self._use_c_nms = False
        if c_lib_path and os.path.exists(c_lib_path):
            try:
                self._c_lib = ctypes.CDLL(c_lib_path)
                # nms_decode(raw, num_anchors, conf_thresh, iou_thresh, max_det, det_out) -> int
                self._c_nms_fn = self._c_lib.nms_decode
                self._c_nms_fn.argtypes = [
                    ctypes.POINTER(ctypes.c_float),  # raw TPU output
                    ctypes.c_int,                     # num_anchors
                    ctypes.c_float,                   # conf_thresh
                    ctypes.c_float,                   # iou_thresh
                    ctypes.c_int,                     # max_det
                    ctypes.POINTER(ctypes.c_float),  # detections output
                ]
                self._c_nms_fn.restype = ctypes.c_int
                self._use_c_nms = True
            except Exception as e:
                import sys
                print(f"[TPUInference] C NMS not available ({e}), using Python fallback",
                      file=sys.stderr)

    def infer(self, planar_bytes: bytes) -> list[Detection]:
        if len(planar_bytes) != self._expected_size:
            raise ValueError(
                f"Expected {self._expected_size} bytes, got {len(planar_bytes)}"
            )

        t0 = time.time()
        out_bytes = self._engine(planar_bytes)
        self._last_tpu_ms = (time.time() - t0) * 1000

        if self._use_c_nms:
            dets = self._decode_c(out_bytes)
        else:
            dets = self._decode_py(out_bytes)

        self._last_nms_ms = (time.time() - t0) * 1000 - self._last_tpu_ms
        return dets

    @property
    def last_timing(self) -> tuple:
        """(tpu_ms, nms_ms) from last infer() call."""
        return (getattr(self, '_last_tpu_ms', 0), getattr(self, '_last_nms_ms', 0))

    @property
    def using_c_nms(self) -> bool:
        return self._use_c_nms

    # ── C NMS (fast, ~5ms) ────────────────────────────────────────

    def _decode_c(self, raw: bytes) -> list[Detection]:
        N = self._engine.out_shape[2]
        C = self._engine.out_shape[1]  # 5 for single-class
        num_floats = C * N
        num_anchors = N  # Each anchor has C values (cx,cy,w,h,conf,...)

        # Convert bytes → ctypes float array
        FloatArray = ctypes.c_float * num_floats
        raw_floats = FloatArray.from_buffer_copy(raw)

        max_det = 20
        DetArr = ctypes.c_float * (max_det * 5)
        det_out = DetArr()

        n = self._c_nms_fn(raw_floats, num_anchors,
                           ctypes.c_float(self._conf_thresh),
                           ctypes.c_float(self._iou_thresh),
                           max_det, det_out)

        results = []
        for i in range(n):
            off = i * 5
            results.append(Detection(
                class_id=0, label=self._labels.get(0, "object"),
                confidence=float(det_out[off + 4]),
                x1=float(det_out[off + 0]), y1=float(det_out[off + 1]),
                x2=float(det_out[off + 2]), y2=float(det_out[off + 3]),
            ))
        return results

    # ── Python NMS (fallback, ~66ms) ──────────────────────────────

    def _decode_py(self, raw: bytes) -> list[Detection]:
        C = self._engine.out_shape[1]; N = self._engine.out_shape[2]
        vals = struct.unpack(f"<{C*N}f", raw)

        candidates = []
        for i in range(N):
            conf = vals[4 * N + i]
            if conf < self._conf_thresh: continue
            cx, cy, w, h = vals[0*N+i], vals[1*N+i], vals[2*N+i], vals[3*N+i]
            x1 = max(0.0, cx - w/2); y1 = max(0.0, cy - h/2)
            x2 = cx + w/2; y2 = cy + h/2
            if x2 - x1 < 2 or y2 - y1 < 2: continue
            candidates.append((x1, y1, x2, y2, conf))

        candidates.sort(key=lambda x: x[4], reverse=True)

        suppressed = [False] * len(candidates)
        results = []
        for i, (x1_i, y1_i, x2_i, y2_i, conf_i) in enumerate(candidates):
            if suppressed[i]: continue
            results.append(Detection(
                class_id=0, label=self._labels.get(0, "object"),
                confidence=float(conf_i),
                x1=float(x1_i), y1=float(y1_i),
                x2=float(x2_i), y2=float(y2_i),
            ))
            a_i = max(0, x2_i - x1_i) * max(0, y2_i - y1_i)
            for j in range(i+1, len(candidates)):
                if suppressed[j]: continue
                x1_j, y1_j, x2_j, y2_j, _ = candidates[j]
                ix = max(0.0, min(x2_i, x2_j) - max(x1_i, x1_j))
                iy = max(0.0, min(y2_i, y2_j) - max(y1_i, y1_j))
                a_j = max(0, x2_j - x1_j) * max(0, y2_j - y1_j)
                if ix * iy / (a_i + a_j + 1e-6) > self._iou_thresh:
                    suppressed[j] = True
        return results

    @property
    def input_size(self) -> tuple:
        return (self._input_w, self._input_h)

    def close(self):
        if hasattr(self, '_engine') and self._engine:
            self._engine.close()

    def benchmark(self, rounds: int = 10) -> dict:
        dummy = b'\x80' * self._expected_size
        self._engine(dummy)  # warmup
        times = []
        for _ in range(rounds):
            t0 = time.time(); self._engine(dummy)
            times.append((time.time() - t0) * 1000)
        avg = sum(times) / len(times)
        return {"rounds": rounds, "avg_ms": round(avg, 1),
                "min_ms": round(min(times), 1), "max_ms": round(max(times), 1),
                "fps": round(1000 / avg, 1)}

    def __repr__(self):
        return f"TPUInference({self.input_size}, conf={self._conf_thresh})"


# ═══════════════════════════════════════════════════════════════════════
# MockInference — PC testing (no TPU needed)
# ═══════════════════════════════════════════════════════════════════════

class MockInference(InferenceEngine):
    """Returns synthetic detections for PC pipeline testing."""

    def __init__(self, input_w: int = 640, input_h: int = 640,
                 class_labels: dict = None):
        self._input_w, self._input_h = input_w, input_h
        self._labels = class_labels or {0: "object"}
        self._custom: Optional[list[Detection]] = None
        self._call_count = 0

    def set_detections(self, dets: list[Detection]):
        self._custom = list(dets)

    def infer(self, planar_bytes: bytes) -> list[Detection]:
        self._call_count += 1
        time.sleep(0.040)  # simulate TPU latency

        if self._custom is not None:
            r, self._custom = self._custom, None
            return r

        # Default mock: center, medium size
        cx, cy, sz = self._input_w * 0.5, self._input_h * 0.5, 80.0
        return [Detection(class_id=0, label=self._labels.get(0, "object"),
                confidence=0.92,
                x1=cx - sz/2, y1=cy - sz/2,
                x2=cx + sz/2, y2=cy + sz/2)]

    @property
    def input_size(self) -> tuple: return (self._input_w, self._input_h)
    def close(self): pass
    def __repr__(self): return f"MockInference({self.input_size})"
