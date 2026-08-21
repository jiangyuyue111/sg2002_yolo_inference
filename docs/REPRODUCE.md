# SG2002 真机管线 — 复现指南（李明涛）

> **目标**：在另一块 SG2002（LicheeRV Nano，StarryOS）板上复现蒋玉月跑通的网球捡球管线：UVC 相机抓帧 → TPU 检测网球 → 舵机夹取 + 电机移动。
> 本指南以 **2026-08-21 板端实测**为准，列全所需资产与获取方式。内核构建细节见 `HANDOVER.md §7`。

---

## 1. 资产清单（复现需要什么）

| # | 资产 | 内容 | 获取方式 |
|---|------|------|---------|
| 1 | **内核** | StarryOS + UART 修复 3 处 | fork `jiangyuyue111/tgoskits` 的 **`my-dev` 分支**（含改动） |
| 2 | **boot.sd / fip.bin** | FIT 内核镜像 + OpenSBI/U-Boot | 按 `HANDOVER §7.2` 构建 / 实验仓库 kernel 分支 |
| 3 | **rootfs 基础** | busybox（1.1MB）+ 基础文件系统 | 从原 rootfs 拷或自建（§7.3） |
| 4 | **Python 3.11.8** | `/bin/python3.11`（27M）+ `/lib/python3.11`（332M） | **太大未打包**，从原 rootfs 拷（见 §5.2） |
| 5 | **动态库 23 个 .so** | musl 运行时 + libffi + TPU SDK 两套 + yolo_ops + preprocess_ops | ✅ Releases：`sg2002_tpu_runtime` → `board-libs-20260821` |
| 6 | **模型** | `yolov8n_tennis_v2.cvimodel`（3.6M，管线核心） | ✅ Releases：`sg2002_yolo_inference` → `models-camera-20260821` |
| 7 | **相机程序** | `/guest/linux/2.camera`（C 二进制，30K） | ✅ 同上 release（板上仅二进制无源码） |
| 8 | **管线代码** | `pipeline/*.py` + **`run.py` v7 主程序** | ✅ GitHub 主仓库（run.py 已同步） |
| 9 | **c_lib** | `preprocess_ops.c` 源码（交叉编译出 `.so`） | ✅ GitHub 主仓库 `c_lib/` |

**两个 Releases 直链：**
- 动态库：https://github.com/jiangyuyue111/sg2002_tpu_runtime/releases/tag/board-libs-20260821
- 模型+相机：https://github.com/jiangyuyue111/sg2002_yolo_inference/releases/tag/models-camera-20260821

---

## 2. 硬件准备

```
SG2002 (LicheeRV Nano) + 8GB+ SD 卡（FAT32 boot + ext4 rootfs）
  │
  ├── USB摄像头 ──→ USB口              （UVC 640×480 YUYV, /dev/video0）
  ├── UART1 (GPIOA18/19) ──→ ESP32-C3 ──→ DRV8833 ──→ N20马达×2  （/dev/ttyS1, 115200, 0xAA 0x55）
  └── UART2 (GPIOA28/29) ──→ 微雪控制板 ──→ ZP10S舵机×3           （/dev/ttyS2, 115200, ASCII #idPpulseTtime!）
```

---

## 3. 分步复现

### ① 内核（含 UART 修复）

```bash
git clone https://github.com/jiangyuyue111/tgoskits.git   # 拉 fork（不是上游 rcore-os/tgoskits）
git fetch origin my-dev && git checkout my-dev            # 含让舵机/电机动起来的 3 处改动
```

构建 + boot.sd + SD 分区 + 部署：**照 `HANDOVER.md §7.1–§7.4` 执行**。

> ⚠️ **§7.5 的 3 处改动必须带**（重建内核时不要回归）：
> - `ns16550.rs`：UART1/2 的 **CLKGEN(0x0300_2000) + RSTC(0x0300_3000) + IOBLK(G7 0x0300_1800 / GRTC 0x0502_7000)** 配置
> - `init.sh`：pinmux `0x64/0x68=6`（UART1 电机）+ `0x70/0x74=2`（UART2 舵机）+ `stty 115200`
> - v4l2 config（相机链路）
>
> 否则电机/舵机"不动"重现。这三处与李明涛的 DWC2/v4l2/uvc 相机链路无关，不冲突。

### ② rootfs 基础 + 动态库

rootfs 用 busybox + ext4 布局（`HANDOVER §7.3`）。动态库直接下包解压：

```bash
# PC 下载 → 免拔卡走串口（serial_push.py）或直接写 SD 卡 ext4 分区
tar xzf sg2002_board_libs-20260821.tar.gz -C /   # 解压出 /lib 与 /akars_tennis/lib
```

### ③ Python 3.11.8（不在 release，见 §5.2）

从蒋玉月板子 rootfs 拷贝 `/bin/python3.11` + `/lib/python3.11`（`sync` 后落盘）；或 `riscv64-linux-musl` 交叉编译 Python 3.11.8。**不能省**——管线是 Python 写的。

### ④ 模型 + 相机程序

```bash
tar xzf sg2002_models_camera-20260821.tar.gz -C /tmp_assets
mkdir -p /akars_tennis/model /guest/linux
cp /tmp_assets/yolov8n_tennis_v2.cvimodel /akars_tennis/model/
cp /tmp_assets/2.camera /guest/linux/ && chmod +x /guest/linux/2.camera
```

### ⑤ 管线代码

```bash
# clone 主仓库，把 pipeline/ 部署到板子 /pipeline/
git clone https://github.com/jiangyuyue111/sg2002_yolo_inference.git
# PC → 板子：serial_push.py 推 pipeline/run.py 与 pipeline/*.py 到 /pipeline/
# preprocess_ops.so 已在动态库包里（/lib/preprocess_ops.so），无需再编译
```

### ⑥ 跑起来（环境变量三要素必带）

```bash
export PYTHONHOME=/
export LD_PRELOAD=/lib/libffi.so
export LD_LIBRARY_PATH=/lib:/akars_tennis/lib

python3 -u /pipeline/run.py        # v7 主程序：Camera→Preprocess→TPU→NMS 自包含
```

期望输出（每 5 帧打印一次）：
```
[0005] det=0.95 @ (320,240)  cam:100ms pre:143ms tpu:40ms nms:2ms total:285ms
```

---

## 4. 验证顺序（先单测后整机）

| 步骤 | 命令 | 通过标准 |
|------|------|---------|
| 相机 | `ls /dev/video0` + 跑 run.py | 帧能出来，det 有值 |
| TPU | run.py 内 TPU init 不报错 | `TPU: 640x640 output→NMS zero-copy` |
| 电机 | `python3 -u /pipeline/motor_test.py`（或 motor_driver 单测） | 轮子转（map: arg1=左轮, 正值=前进） |
| 舵机 | `python3 -u /pipeline/servo_move_test.py` | 夹爪开合（ID 0/1/2，量程 180°，1500=90°） |
| 整机 | `python3 -u /pipeline/run.py` | 稳定 3.5–4.3fps，追球不失控 |

> 追球（state_machine/controller 决策）现在在 `real_pipeline.py`/`hunter` 侧；`run.py` 是纯视觉检测入口（v7，0-copy TPU→NMS）。如需完整追球闭环按 `REAL_MACHINE_GUIDE.md` 的决策表接电机/舵机。

---

## 5. 关键说明

### 5.1 板上无 pyserial、无 ldd/readelf
- 串口操作用 raw fd + termios（管线代码已处理）
- 验证 .so 链接关系在 **PC 端**用 `riscv64-linux-musl-objdump -p` / `readelf -d`

### 5.2 为什么 Python 运行时没打包
- `/bin/python3.11`（27M）+ `/lib/python3.11`（332M stdlib）太大，GitHub Releases 不适合
- 最可靠：**从已跑通的板子 rootfs 拷**（`sync` 后落盘）。需要重建时再补

### 5.3 铁律（板上必守）
1. **每次断电重启**（UVC DMA 挂死后用户态无法恢复，只能断电）
2. **ext4 必须 sync**（否则写盘丢失）
3. **serial_push 用 PowerShell**（非 bash）
4. **Python 三要素**：`LD_PRELOAD=/lib/libffi.so` + `PYTHONHOME=/` + `-u`
5. 内核更新**不要回归** UART1/2 pinmux/时钟（§7.5）

---

## 6. 相关链接

| 资源 | 地址 |
|------|------|
| 主仓库（管线代码） | https://github.com/jiangyuyue111/sg2002_yolo_inference |
| 动态库 Releases | https://github.com/jiangyuyue111/sg2002_tpu_runtime/releases/tag/board-libs-20260821 |
| 模型+相机 Releases | https://github.com/jiangyuyue111/sg2002_yolo_inference/releases/tag/models-camera-20260821 |
| TPU 运行时实验 | https://github.com/jiangyuyue111/sg2002_tpu_runtime |
| StarryOS 平台实验 | https://github.com/jiangyuyue111/sg2002_starryos_experiments |
| tgoskits fork（my-dev） | https://github.com/jiangyuyue111/tgoskits |
| 上游参考（AKA-00） | https://github.com/chenlongos/AKA-00 |
| 交接文档 | `docs/HANDOVER.md` |
| 真机操作指南 | `docs/REAL_MACHINE_GUIDE.md` |
