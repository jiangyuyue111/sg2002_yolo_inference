"""
collect_data.py — Auto-capture tennis ball images for model training.
Usage (on SG2002):
    python3 collect_data.py --camera /guest/linux/2.camera --out /sdcard/dataset/ --interval 10

This captures every Nth frame and saves raw YUYV + label placeholder.
Collect data in different scenarios:
    - Different distances (near/mid/far)
    - Different angles (left/center/right)
    - Different lighting conditions
    - With/without tennis ball (negative samples)

Output structure:
    dataset/
      images/
        00001.yuv     <- raw YUYV frame
        00002.yuv
        ...
      labels/
        00001.txt     <- YOLO format: class cx cy w h (normalized)
        00002.txt
        ...
      dataset.yaml     <- dataset config for training
"""
import sys, os, time, signal, struct, subprocess, argparse, json

# ── Camera ──
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

def yuyv_to_rgb(yuv, w, h):
    """Convert YUYV to RGB bytes (for PC labeling tools)."""
    import numpy as np
    yuv_arr = np.frombuffer(yuv, dtype=np.uint8).reshape(h, w, 2)
    Y = yuv_arr[:,:,0].astype(np.float32)
    U = yuv_arr[:,:,1].astype(np.float32)
    V = yuv_arr[:,:,3].astype(np.float32) if w % 2 == 0 else yuv_arr[:,:,1]
    U = np.repeat(U.reshape(h, w//2), 2, axis=1)
    V = np.repeat(V.reshape(h, w//2), 2, axis=1)
    R = Y + 1.402 * (V - 128)
    G = Y - 0.344136 * (U - 128) - 0.714136 * (V - 128)
    B = Y + 1.772 * (U - 128)
    rgb = np.stack([R, G, B], axis=-1).clip(0, 255).astype(np.uint8)
    return rgb.tobytes()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera", default="/guest/linux/2.camera")
    parser.add_argument("--out", default="/sdcard/dataset", help="Output directory")
    parser.add_argument("--interval", type=int, default=10, help="Capture every Nth frame")
    parser.add_argument("--prefix", default="tennis", help="Filename prefix")
    parser.add_argument("--convert-rgb", action="store_true", help="Also save RGB PNG (needs numpy)")
    args = parser.parse_args()

    os.makedirs(f"{args.out}/images", exist_ok=True)
    os.makedirs(f"{args.out}/labels", exist_ok=True)

    print("=" * 50)
    print("  Data Collector")
    print(f"  Output: {args.out}")
    print(f"  Interval: every {args.interval} frames")
    print(f"  Format: raw YUYV")
    print(f"  Empty label files created — label them on PC")
    print("=" * 50)
    print()
    print("  INSTRUCTIONS:")
    print("  1. Hold tennis ball at various distances/angles")
    print("  2. Move slowly — you'll see 'SAVED' every N frames")
    print("  3. Press Ctrl+C to stop")
    print("  4. Copy dataset to PC for labeling")
    print()
    print("  Label format (per image .txt):")
    print("    class cx cy w h")
    print("    e.g. 0 0.5 0.5 0.2 0.2")
    print("  (all values normalized to [0,1], origin top-left)")
    print()
    print("  Starting in 3 seconds...")
    time.sleep(3)

    cam = Cam(args.camera)
    count, saved = 0, 0
    t0 = time.time()

    try:
        while True:
            yuv, sw, sh = cam.get()
            count += 1

            if count % args.interval == 0:
                name = f"{args.prefix}_{saved:05d}"
                img_path = f"{args.out}/images/{name}.yuv"

                # Save raw YUYV
                with open(img_path, "wb") as f:
                    f.write(yuv)

                # Create empty label placeholder
                label_path = f"{args.out}/labels/{name}.txt"
                with open(label_path, "w") as f:
                    f.write("# label me: class cx cy w h\n")

                saved += 1
                elapsed = time.time() - t0
                fps = count / elapsed if elapsed > 0 else 0
                print(f"  [{saved:05d}] frame={count} fps={fps:.1f}")

    except KeyboardInterrupt:
        pass
    finally:
        cam.close()

    elapsed = time.time() - t0
    print(f"\n  Done: {saved} images in {elapsed:.0f}s")
    print(f"  Copy to PC: scp -r root@<board>:{args.out} ./dataset/")
    print(f"\n  Next steps:")
    print(f"  1. Convert YUYV to PNG: python convert_dataset.py")
    print(f"  2. Label images with labelImg or similar tool")
    print(f"  3. Train in Colab with train_yolo.ipynb")

if __name__ == "__main__":
    main()
