#!/usr/bin/env python3
"""
board_camera.py — SG2002 板端实时摄像头 TPU 推理

用法:  python3 /pipeline/board_camera.py

管道:  2.camera (YUYV) → Preprocess (C加速) → TPU → NMS → Position → Controller
"""

import sys
import time
import signal
import os

# Ensure /pipeline is on path
sys.path.insert(0, "/pipeline")

from pipeline.config import Config
from pipeline.image_source import RawYUYVSource
from pipeline.preprocessor import Preprocessor
from pipeline.inference import TPUInference
from pipeline.position import PositionAnalyzer
from pipeline.controller import Controller


def main():
    # ── Config ────────────────────────────────────────────────────
    cfg = Config(
        mode="board",
        dry_run=True,                      # 串口控制先关着
        model_path="/akars_tennis/model/yolov8n_tennis_v2.cvimodel",
        preprocess_lib_path="/lib/preprocess_ops.so",
        conf_threshold=0.5,
        nms_iou_threshold=0.45,
    )

    print(f"{'='*55}")
    print(f"  SG2002 Live Camera + TPU Pipeline")
    print(f"  Camera:  640x480 YUYV → C yuyv_resize_planar → 640x640 CHW")
    print(f"  Model:   {cfg.model_path}")
    print(f"  TPU + C NMS (preprocess_ops.so)")
    print(f"  Control: {'DRY RUN' if cfg.dry_run else 'LIVE'}")
    print(f"{'='*55}\n")

    # ── Components ─────────────────────────────────────────────────
    pp = Preprocessor(
        target_w=640, target_h=640,
        lib_path=cfg.preprocess_lib_path,
        use_c=True,
    )
    print(f"  Preprocessor: {pp}")

    infer = TPUInference(
        model_path=cfg.model_path,
        conf_threshold=cfg.conf_threshold,
        nms_iou=cfg.nms_iou_threshold,
        class_labels=cfg.class_labels,
        c_lib_path=cfg.preprocess_lib_path,
    )
    print(f"  Inference: {infer}  C NMS: {infer.using_c_nms}")

    pos = PositionAnalyzer(
        frame_w=640, frame_h=640,
        left_boundary=cfg.zone_left_boundary,
        right_boundary=cfg.zone_right_boundary,
        top_boundary=cfg.zone_top_boundary,
        bottom_boundary=cfg.zone_bottom_boundary,
        near_threshold=cfg.near_size_threshold,
        mid_threshold=cfg.mid_size_threshold,
    )

    ctrl = Controller(
        dry_run=cfg.dry_run,
        serial_port=cfg.serial_port,
        serial_baud=cfg.serial_baud,
    )
    print(f"  Controller: {ctrl}")
    print(f"{'='*55}\n")

    # ── Camera source ──────────────────────────────────────────────
    camera = RawYUYVSource(
        binary_path="/guest/linux/2.camera",
        width=640, height=480,
    )
    print(f"  Camera: {camera}")
    print(f"\nStarting pipeline... Press Ctrl+C to stop.\n")

    fid, t0, fps_window = 0, time.time(), []

    def shutdown(signum=None, frame=None):
        nonlocal fid, t0
        elapsed = time.time() - t0
        if fid > 0:
            avg_fps = fid / elapsed
            print(f"\n\n{'='*55}")
            print(f"  {fid} frames in {elapsed:.1f}s  avg {avg_fps:.1f} fps")
            if fps_window:
                print(f"  recent {len(fps_window)} frames: {sum(fps_window)/len(fps_window):.1f} fps")
            print(f"{'='*55}")
        camera.close()
        infer.close()
        ctrl.close()
        print("Shutdown complete.")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        while True:
            t_frame = time.time()

            # 1. Grab: YUYV bytes from camera
            yuyv_bytes, w, h = camera.get_frame()

            # 2. Preprocess: YUYV → CHW planar (C yuyv_resize_planar)
            planar = pp.process_bgr_bytes(yuyv_bytes, w, h)

            # 3. TPU Inference + NMS
            dets = infer.infer(planar)
            tpu_ms, nms_ms = infer.last_timing

            # 4. Position
            result = pos.analyze(dets)

            # 5. Control
            ctrl.execute(result)

            # ── Log ──────────────────────────────────────────
            total_ms = (time.time() - t_frame) * 1000
            fps_window.append(1000 / total_ms if total_ms > 0 else 0)
            if len(fps_window) > 30:
                fps_window.pop(0)

            fid += 1
            if fid % 10 == 0 or dets:
                avg_fps = sum(fps_window) / len(fps_window)
                det_str = f"{dets[0].confidence:.2f} @ ({dets[0].center_x:.0f},{dets[0].center_y:.0f})" if dets else "none"
                print(f"  [{fid:04d}] det={det_str}  "
                      f"tpu:{tpu_ms:.0f}ms  nms:{nms_ms:.0f}ms  "
                      f"total:{total_ms:.0f}ms  fps:{avg_fps:.1f}"
                      + (f"  → {result.summary()}" if result.has_target else ""))

    except EOFError as e:
        print(f"\nCamera ended: {e}")
    finally:
        shutdown()


if __name__ == "__main__":
    main()
