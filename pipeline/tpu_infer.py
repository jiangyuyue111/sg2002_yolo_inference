#!/usr/bin/env python3
"""TPU inference module for AKA-00 Linux — replace ONNX with SG2002 TPU."""
import ctypes, struct, os, time, sys
import cv2
import numpy as np

# Must set LD path BEFORE any CDLL call
os.environ["LD_LIBRARY_PATH"] = "/usr/bin/lib:" + os.environ.get("LD_LIBRARY_PATH", "")

_CVI = None
_PRE = None

def _get_cvi():
    global _CVI
    if _CVI is None:
        _CVI = ctypes.CDLL("libcviruntime.so")
    return _CVI

def _get_pre():
    global _PRE
    if _PRE is None:
        _PRE = ctypes.CDLL("/root/preprocess_ops.so")
    return _PRE

# ══ TPU Engine ══
def _r64(addr, off=0):
    return struct.unpack("<Q", ctypes.string_at(addr + off, 8))[0]
def _r32(addr, off=0):
    return struct.unpack("<i", ctypes.string_at(addr + off, 4))[0]

class TPUEngine:
    def __init__(self, path="/root/yolov8n_tennis_v2.cvimodel"):
        if isinstance(path, str): path = path.encode()
        self.model = ctypes.c_void_p(0)
        self.in_ts = ctypes.c_void_p(0);  self.in_n  = ctypes.c_int32(0)
        self.out_ts = ctypes.c_void_p(0); self.out_n  = ctypes.c_int32(0)

        _get_cvi().CVI_RT_Init()
        _get_cvi().CVI_NN_RegisterModel(path, ctypes.byref(self.model))
        _get_cvi().CVI_NN_GetInputOutputTensors(
            self.model, ctypes.byref(self.in_ts), ctypes.byref(self.in_n),
            ctypes.byref(self.out_ts), ctypes.byref(self.out_n))

        ip = self.in_ts.value
        self.in_shape = [_r32(ip, 8+i*4) for i in range(6)]
        self.in_data  = _r64(ip, 64)
        self.in_size  = int(_r64(ip, 48))

        op = self.out_ts.value
        self.out_shape = [_r32(op, 8+i*4) for i in range(6)]
        self.out_data  = _r64(op, 64)
        self.out_count = int(_r64(op, 48))
        self.out_bytes = self.out_count * 4

        # Pre-allocated buffers
        self._planar_buf = ctypes.create_string_buffer(self.in_size)

    def __call__(self, planar_bytes):
        ctypes.memmove(ctypes.c_void_p(self.in_data), planar_bytes, self.in_size)
        _get_cvi().CVI_NN_Forward(self.model, self.in_ts, self.in_n, self.out_ts, self.out_n)
        return ctypes.string_at(self.out_data, self.out_bytes)

    def close(self):
        if self.model and self.model.value:
            _get_cvi().CVI_NN_CleanupModel(self.model)

# ══ Preprocessor (C accelerated) ══
_get_pre().bgr_resize_planar.argtypes = [
    ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
    ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
_get_pre().bgr_resize_planar.restype = ctypes.c_int
_get_pre().bgr_letterbox_planar.argtypes = [
    ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
    ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
_get_pre().bgr_letterbox_planar.restype = ctypes.c_int

# ══ NMS (C accelerated) ══
_get_pre().nms_decode.argtypes = [
    ctypes.POINTER(ctypes.c_float), ctypes.c_int,
    ctypes.c_float, ctypes.c_float, ctypes.c_int,
    ctypes.POINTER(ctypes.c_float)]
_get_pre().nms_decode.restype = ctypes.c_int

# ══ Unified Inference Pipeline ══
class TPUPipeline:
    def __init__(self, model_path="/root/yolov8n_tennis_v2.cvimodel"):
        os.environ.setdefault("LD_LIBRARY_PATH", "/usr/bin/lib")
        self.engine = TPUEngine(model_path)
        self.conf = 0.3   # lower threshold for real-world use
        self.iou = 0.45
        self.N = self.engine.out_shape[2]
        self.C = self.engine.out_shape[1]
        # Pre-allocated NMS arrays
        self._float_arr = (ctypes.c_float * (self.C * self.N))()
        self._det_arr = (ctypes.c_float * (20 * 5))()

    def infer_bgr(self, bgr_frame):
        """BGR numpy array → list of detection dicts."""
        h, w = bgr_frame.shape[:2]
        bgr_bytes = bgr_frame.tobytes()

        # Preprocess: BGR → CHW planar via C
        planar = ctypes.create_string_buffer(self.engine.in_size)
        _get_pre().bgr_resize_planar(bgr_bytes, w, h, planar, 640, 640)

        # TPU Forward
        raw = self.engine(bytes(planar))

        # NMS decode
        self._float_arr = (ctypes.c_float * (self.C * self.N)).from_buffer_copy(raw)
        det = (ctypes.c_float * (20 * 5))()
        n = _get_pre().nms_decode(self._float_arr, self.N,
                           ctypes.c_float(self.conf), ctypes.c_float(self.iou),
                           20, det)
        dets = []
        for i in range(n):
            o = i * 5
            x1, y1, x2, y2, conf = float(det[o]), float(det[o+1]), float(det[o+2]), float(det[o+3]), float(det[o+4])
            # Scale back to original frame
            scale_x = w / 640.0
            scale_y = h / 640.0
            dets.append({
                "x": int(x1 * scale_x),
                "y": int(y1 * scale_y),
                "w": int((x2 - x1) * scale_x),
                "h": int((y2 - y1) * scale_y),
                "conf": conf,
            })
        return dets

    def close(self):
        self.engine.close()


# ══ Standalone test ══
if __name__ == "__main__":
    print("=" * 55)
    print("  AKA-00 TPU Pipeline Test")
    print("=" * 55)

    pipe = TPUPipeline()
    print("  TPU Engine: OK")
    print("  Model: {}x{}  {} anchors".format(
        pipe.engine.in_shape[3], pipe.engine.in_shape[2], pipe.N))

    # Test with camera
    print("  Opening camera...")
    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    if not cap.isOpened():
        print("  ERROR: Cannot open camera")
        exit(1)

    print("  Camera: OK ({}x{})".format(
        int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))))
    print("=" * 55)
    print("  Running... Ctrl+C to stop.\n")

    fid = 0
    try:
        while True:
            ret, frame = cap.read()
            if not ret: continue
            t0 = time.time()
            dets = pipe.infer_bgr(frame)
            elapsed = (time.time() - t0) * 1000
            fid += 1
            if fid % 10 == 0 or dets:
                d = "{:.2f} @ ({},{})".format(dets[0]["conf"], dets[0]["x"], dets[0]["y"]) if dets else "none"
                print("  [{:04d}] det={}  {:.0f}ms".format(fid, d, elapsed))
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        pipe.close()
        if fid:
            print("\n  {} frames processed".format(fid))
