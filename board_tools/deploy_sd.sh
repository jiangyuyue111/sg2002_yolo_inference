#!/usr/bin/env bash
# deploy_sd.sh — 把 repo 里的文件拷进 SD 卡 ext4 rootfs（在 WSL 里用 sudo 跑）。
#
# 前提：SD 卡已通过 usbipd 透传到 WSL（`usbipd attach --wsl --busid 1-3`），
#       并已识别为 /dev/sde（sde1=boot FAT32, sde2=rootfs ext4）。
#
# 用法（在 WSL Ubuntu 里）：
#   sudo bash /mnt/c/Users/蒋玉月/sg2002_yolo_inference/board_tools/deploy_sd.sh pipeline/real_pipeline.py
#   sudo bash .../deploy_sd.sh pipeline/real_pipeline.py pipeline/motor_driver.py
#
# 环境变量覆盖：
#   DEPLOY_PART=/dev/sdf2   rootfs 分区（默认 /dev/sde2）
#   DEPLOY_MP=/mnt/sdroot   挂载点（默认 /mnt/sdroot）
set -euo pipefail

PART="${DEPLOY_PART:-/dev/sde2}"
MP="${DEPLOY_MP:-/mnt/sdroot}"
REPO="/mnt/c/Users/蒋玉月/sg2002_yolo_inference"

[ $# -ge 1 ] || { echo "用法: sudo bash $0 <repo相对路径> [更多路径...]"; exit 1; }

if ! mountpoint -q "$MP" 2>/dev/null; then
    echo "[deploy] 挂载 $PART → $MP"
    mkdir -p "$MP"
    mount "$PART" "$MP"
    MOUNTED=1
else
    echo "[deploy] $PART 已挂载在 $MP（复用一个已存在的挂载）"
    MOUNTED=0
fi

for f in "$@"; do
    src="$REPO/$f"
    dst="$MP/$f"
    if [ -f "$src" ]; then
        mkdir -p "$(dirname "$dst")"
        cp "$src" "$dst"
        echo "[deploy] ✓ $f  →  $(ls -l "$dst" | awk '{print $5" bytes"}')"
    else
        echo "[deploy] ✗ 源文件不存在: $src"
    fi
done

sync
echo "[deploy] 已 sync 刷盘"

if [ "$MOUNTED" = 1 ]; then
    umount "$MP"
    echo "[deploy] 已卸载 $MP"
fi
echo "[deploy] 完成 — 可以拔卡上机了"
