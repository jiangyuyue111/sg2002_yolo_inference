"""
model_compare.py — Compare two cvimodel files on the board.
Usage (on SG2002):
    python3 model_compare.py \
        --model-a /akars_tennis/model/yolov8n_tennis_v2.cvimodel \
        --model-b /akars_tennis/model/aka00_yolo_model.cvimodel \
        --camera /guest/linux/2.camera \
        --frames 100
Output:
    Per-frame: detections from both models side by side
    Summary: avg confidence, avg count, avg time
"""
import sys, os, time, signal, struct, ctypes, subprocess, argparse

# ── Camera (same as run.py) ──
class Cam:
    FMT = "<IIIIIIQ"; FSZ = struct.calcsize(FMT); MAGIC = 0xC0C0C0C0
    def __init__(self, path, w=640, h=480):
        self.w, self.h = w, h
        self._p = subprocess.Popen([path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=open(os.devnull, "r"))
        self._b, self._s = b"", False
    def _sync(self):
        while len(self._b) < self.FSZ + 128:
            c = self._p.stdout.read(65536)
            if not c: raise EOFError
            self._b += c
        for i in range(len(self._b) - 4):
            if struct.unpack("<I", self._b[i:i+4])[0] == self.MAGIC: self._b = self._b[i:]; self._s = True; return
        raise EOFError("no magic")
    def get(self):
        if not self._s: self._sync()
        while len(self._b) < self.FSZ:
            c = self._p.stdout.read(65536)
            if not c: raise EOFError
            self._b += c
        if struct.unpack("<I", self._b[:4])[0] != self.MAGIC: self._b = self._b[1:]; self._s = False; return self.get()
        h = struct.unpack(self.FMT, self._b[:self.FSZ]); pl = h[4]; tot = self.FSZ + pl
        while len(self._b) < tot:
            c = self._p.stdout.read(max(65536, tot - len(self._b)))
            if not c: raise EOFError
            self._b += c
        f = self._b[self.FSZ:tot]; self._b = self._b[tot:]; return bytes(f), self.w, self.h
    def close(self):
        if self._p and self._p.poll() is None: self._p.send_signal(signal.SIGTERM)
        try: self._p.wait(timeout=3)
        except: self._p.kill()

# ── TPU Engine ──
class TPUEngine:
    def __init__(self, model_path):
        self._cvi = ctypes.CDLL("libcviruntime.so")
        self.m = ctypes.c_void_p(0)
        self.it = ctypes.c_void_p(0); self.in_n = ctypes.c_int32(0)
        self.ot = ctypes.c_void_p(0); self.on_n = ctypes.c_int32(0)
        path = model_path.encode() if isinstance(model_path, str) else model_path
        rc = self._cvi.CVI_RT_Init()
        if rc: raise RuntimeError(f"RT_Init: {rc}")
        rc = self._cvi.CVI_NN_RegisterModel(path, ctypes.byref(self.m))
        if rc: raise RuntimeError(f"RegisterModel: {rc}")
        rc = self._cvi.CVI_NN_GetInputOutputTensors(self.m, ctypes.byref(self.it), ctypes.byref(self.in_n), ctypes.byref(self.ot), ctypes.byref(self.on_n))
        if rc: raise RuntimeError(f"GetTensors: {rc}")
        ip = self.it.value
        self.ish = [struct.unpack("<i", ctypes.string_at(ip + 8 + i*4, 4))[0] for i in range(6)]
        self.idata = struct.unpack("<Q", ctypes.string_at(ip + 64, 8))[0]
        self.isz = struct.unpack("<Q", ctypes.string_at(ip + 48, 8))[0]
        op = self.ot.value
        self.osh = [struct.unpack("<i", ctypes.string_at(op + 8 + i*4, 4))[0] for i in range(6)]
        self.odata = struct.unpack("<Q", ctypes.string_at(op + 64, 8))[0]
        self.ocnt = struct.unpack("<Q", ctypes.string_at(op + 48, 8))[0]
        self.in_ptr = ctypes.c_void_p(self.idata)
        self.out_ptr = ctypes.c_void_p(self.odata)
    def forward(self):
        rc = self._cvi.CVI_NN_Forward(self.m, self.it, self.in_n, self.ot, self.on_n)
        if rc: raise RuntimeError(f"Forward: {rc}")
    def close(self):
        if self.m and self.m.value: self._cvi.CVI_NN_CleanupModel(self.m); self.m.value = 0

# ── Preprocessor ──
class Preprocessor:
    def __init__(self, tw=640, th=640, lib="/lib/preprocess_ops.so"):
        self.tw, self.th = tw, th
        self._l = ctypes.CDLL(lib)
        self._l.yuyv_resize_planar.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        self._l.yuyv_resize_planar.restype = ctypes.c_int
    def run(self, yuv, sw, sh, target_ptr):
        self._l.yuyv_resize_planar(ctypes.c_char_p(yuv), sw, sh, target_ptr, self.tw, self.th)

# ── NMS ──
class NMSDecoder:
    def __init__(self, lib="/lib/preprocess_ops.so"):
        self._l = ctypes.CDLL(lib)
        self._l.nms_decode.argtypes = [ctypes.POINTER(ctypes.c_float), ctypes.c_int, ctypes.c_float, ctypes.c_float, ctypes.c_int, ctypes.POINTER(ctypes.c_float)]
        self._l.nms_decode.restype = ctypes.c_int
        self._buf = (ctypes.c_float * (20 * 5))()
    def decode(self, out_ptr, n_anchors, conf=0.5, iou=0.45):
        out_f = ctypes.cast(out_ptr, ctypes.POINTER(ctypes.c_float))
        n = self._l.nms_decode(out_f, n_anchors, ctypes.c_float(conf), ctypes.c_float(iou), 20, self._buf)
        dets = []
        for i in range(n):
            o = i * 5
            dets.append((float(self._buf[o+4]), float(self._buf[o+0]), float(self._buf[o+1]), float(self._buf[o+2]), float(self._buf[o+3])))
        return dets

# ── Main comparison ──
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-a", required=True, help="Baseline model path")
    parser.add_argument("--model-b", required=True, help="Comparison model path")
    parser.add_argument("--camera", default="/guest/linux/2.camera")
    parser.add_argument("--frames", type=int, default=100)
    parser.add_argument("--conf", type=float, default=0.5)
    parser.add_argument("--iou", type=float, default=0.45)
    parser.add_argument("--lib", default="/lib/preprocess_ops.so")
    args = parser.parse_args()

    print("=" * 60)
    print("  Model Comparison")
    print(f"  A: {args.model_a}")
    print(f"  B: {args.model_b}")
    print(f"  Frames: {args.frames}")
    print("=" * 60)

    cam = Cam(args.camera)
    pp = Preprocessor(640, 640, args.lib)
    nms = NMSDecoder(args.lib)

    engine_a = TPUEngine(args.model_a)
    engine_b = TPUEngine(args.model_b)

    print(f"\n  Model A input:  {engine_a.ish[2]}x{engine_a.ish[3]}  output: {engine_a.osh[2]}x{engine_a.osh[1]}")
    print(f"  Model B input:  {engine_b.ish[2]}x{engine_b.ish[3]}  output: {engine_b.osh[2]}x{engine_b.osh[1]}")
    print()

    stats = {"a_time": [], "b_time": [], "a_count": [], "b_count": [], "a_conf": [], "b_conf": []}

    for fid in range(args.frames):
        yuv, sw, sh = cam.get()

        # Model A
        t0 = time.time()
        pp.run(yuv, sw, sh, engine_a.in_ptr)
        engine_a.forward()
        dets_a = nms.decode(engine_a.out_ptr, engine_a.osh[2], args.conf, args.iou)
        ta = (time.time() - t0) * 1000

        # Model B
        t0 = time.time()
        pp.run(yuv, sw, sh, engine_b.in_ptr)
        engine_b.forward()
        dets_b = nms.decode(engine_b.out_ptr, engine_b.osh[2], args.conf, args.iou)
        tb = (time.time() - t0) * 1000

        stats["a_time"].append(ta); stats["b_time"].append(tb)
        stats["a_count"].append(len(dets_a)); stats["b_count"].append(len(dets_b))
        if dets_a: stats["a_conf"].append(max(d[0] for d in dets_a))
        if dets_b: stats["b_conf"].append(max(d[0] for d in dets_b))

        if fid % 10 == 0:
            print(f"  [{fid:03d}] A:{ta:6.0f}ms {len(dets_a)}dets | B:{tb:6.0f}ms {len(dets_b)}dets")

    cam.close(); engine_a.close(); engine_b.close()

    # Summary
    def avg(lst): return sum(lst) / len(lst) if lst else 0
    print(f"\n{'='*60}")
    print(f"  SUMMARY ({args.frames} frames)")
    print(f"{'='*60}")
    print(f"  {'':20s} {'Model A':>15s} {'Model B':>15s}")
    print(f"  {'Avg time':20s} {avg(stats['a_time']):14.0f}ms {avg(stats['b_time']):14.0f}ms")
    print(f"  {'Avg detections':20s} {avg(stats['a_count']):14.1f} {avg(stats['b_count']):14.1f}")
    print(f"  {'Avg confidence':20s} {avg(stats['a_conf']):14.3f} {avg(stats['b_conf']):14.3f}")
    print(f"  {'Frames with det':20s} {sum(1 for c in stats['a_count'] if c>0):14d} {sum(1 for c in stats['b_count'] if c>0):14d}")

if __name__ == "__main__":
    main()
