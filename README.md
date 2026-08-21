# SG2002 网球捡球机器人 — 真机管线

[![Platform](https://img.shields.io/badge/platform-SG2002%20RISC--V-blue)]()
[![TPU](https://img.shields.io/badge/TPU-0.5%20TOPS-orange)]()
[![Python](https://img.shields.io/badge/Python-3.11-green)]()

**在 SG2002（LicheeRV Nano, StarryOS 内核）上跑通的端到端真机管线：UVC 相机抓帧 → TPU 检测网球 → 舵机夹取 + 电机移动。**

> 本仓库只保留**真机可运行的用户态管线**。TPU 推理实验代码（C/C++/Python/Rust 四语言 40ms）在 [`sg2002_tpu_runtime`](https://github.com/jiangyuyue111/sg2002_tpu_runtime)；内核/boot/相机采集实验在 [`sg2002_starryos_experiments`](https://github.com/jiangyuyue111/sg2002_starryos_experiments)（main/kernel/camera 三分支）。

---

## 目录

- [运行状态](#运行状态)
- [快速开始](#快速开始)
- [目录结构](#目录结构)
- [管线数据流](#管线数据流)
- [铁律（板上必守）](#铁律板上必守)
- [相关仓库](#相关仓库)
- [贡献者](#贡献者)
- [文档索引](#文档索引)

---

## 运行状态

| 模块 | 状态 | 说明 |
|------|------|------|
| **相机** | ✅ 4.2fps | UVC 640×480 YUYV，**阻塞读**是唯一实时模式（~210ms/帧） |
| **TPU 检测** | ✅ 40ms | YOLOv8n 网球检测（`pipeline/inference.py`，355× 加速） |
| **电机** | ✅ 已跑通 | UART1 `/dev/ttyS1` → ESP32-C3（0xAA 0x55 协议）；映射 **arg1=左轮，正值=前进** |
| **舵机** | ✅ 已跑通 | UART2 `/dev/ttyS2` → ZP10S（ASCII `#000P...`）；角度量程 180°（1500=90°） |
| **追球** | ⚠️ 待复测 | 降增益后真机复测，球放近 **50cm–1m** |

> 现状以 [`docs/HANDOVER.md`](docs/HANDOVER.md) 为准（交接给李明涛的最新文档）。

---

## 快速开始

### PC 侧一键跑真机管线（相机挂死自动断电提示）

```powershell
# Windows PowerShell, COM6 = 板载串口
python board_tools/pipeline_run.py COM6
```

脚本内含**挂死检测**（8 秒无输出 → Ctrl+C + 断电提示）。UVC DMA 挂死是内核态问题，用户态无法恢复，只能断电重启。

### 板端 Python 三要素（必加）

```bash
# StarryOS 上跑任何 Python 都必须带这三样
LD_PRELOAD=/lib/libffi.so PYTHONHOME=/ LD_LIBRARY_PATH=/lib:/akars_tennis/lib python3 -u 脚本.py
```

### 板端手工单测

```bash
# 电机（轮子转=正常）
python3 -u /guest/linux/pipeline/motor_test.py
# 舵机
python3 -u /guest/linux/pipeline/servo_move_test.py
# 摄像头
python3 -u /guest/linux/pipeline/board_camera.py
```

---

## 目录结构

```
sg2002_yolo_inference/
│
├── pipeline/                👈 真机管线主代码（板上 /guest/linux/pipeline）
│   ├── real_pipeline.py         五段式主线: Camera→Preprocess→TPU→NMS→Control
│   ├── image_source.py          相机源（RawYUYVSource 阻塞读回退）
│   ├── inference.py             TPU 推理封装（当前在用）
│   ├── board_camera.py          摄像头采集
│   ├── motor_driver.py          UART1 电机（0xAA 0x55 → ESP32-C3）
│   ├── servo_driver.py          UART2 舵机（ZP10S ASCII）
│   ├── state_machine.py         状态机（hunter 追球逻辑，移植自 AKA-00）
│   ├── hunter.py                hunter 主逻辑
│   ├── config.py / position.py / controller.py ...
│   └── servo_calibrate.py       舵机量程校准
│
├── board_tools/             PC 端 + 板上工具
│   ├── pipeline_run.py          一键跑真机管线（含挂死检测）👈 入口
│   ├── serial_push.py           PowerShell 串口推脚本到板
│   ├── deploy_sd.sh             SD 卡部署
│   └── servo_*.py / motor_*.py  PC 侧单测脚本
│
├── c_lib/                   板端 C 加速库（preprocess_ops，ctypes 调用）
├── tests/                   协议测试（esp32_protocol_test.py）
├── images/                  网球测试图
├── docs/
│   ├── HANDOVER.md              👈 交接文档（李明涛接手必读）
│   ├── REAL_MACHINE_GUIDE.md    真机操作指南
│   └── MANBO1234_ANALYSIS.md    ACT 方案对比分析
├── CHANGELOG.md
└── README.md
```

---

## 管线数据流

```
UVC 相机 ──阻塞读──> 640×480 YUYV ──preprocess(so)──> 640×640 int8
        ──> TPU Forward (40ms) ──> NMS ──> 目标框
        ──> state_machine 决策 ──> 电机(UART1) 移动 + 舵机(UART2) 夹取
```

### 电机/舵机不是内核驱动

电机/舵机由**用户态 Python 管线走 UART 驱动**，但要能动，内核侧必须带前期修好的 pinmux+时钟配置：

- **UART1 电机**：pinmux `0x64/0x68=6` + CLKGEN/RSTC/IOBLK 三层修复
- **UART2 舵机**：pinmux `0x70/0x74=2` + 115200

这些改在 StarryOS 内核构建里，**不在 tgoskits**。tgoskits（李明涛的 DWC2/v4l2/uvc）是摄像头链路，与电机舵机无关。接手时内核更新不要回归 UART1/2 的 pinmux/时钟配置。

---

## 铁律（板上必守）

1. **每次断电重启**（相机 DMA 挂死后用户态无法恢复）
2. **ext4 必须 sync**（否则写盘丢失）
3. **serial_push 用 PowerShell**（非 bash）
4. **Python 三要素**：`LD_PRELOAD=/lib/libffi.so` + `PYTHONHOME=/` + `-u`
5. 板上**无 pyserial**——用 raw fd + termios

---

## 相关仓库

| 仓库 | 内容 |
|------|------|
| [`sg2002_yolo_inference`](https://github.com/jiangyuyue111/sg2002_yolo_inference) | 本仓库：真机管线 |
| [`sg2002_tpu_runtime`](https://github.com/jiangyuyue111/sg2002_tpu_runtime) | TPU 推理运行时（四语言 40ms 实验 + benchmark） |
| [`sg2002_starryos_experiments`](https://github.com/jiangyuyue111/sg2002_starryos_experiments) | StarryOS 平台实验（`main`=StarryOS，`kernel`=内核/boot，`camera`=相机） |
| [chenlongos/AKA-00](https://github.com/chenlongos/AKA-00) | 上游参考（hunter/电机/舵机代码移植来源，本地副本在 `_staging/aka00`） |
| [rcore-os/tgoskits](https://github.com/rcore-os/tgoskits) | StarryOS 内核（李明涛 dev 分支） |

---

## 贡献者

| 姓名 | 负责模块 | 成果 |
|------|------|------|
| **李明涛** | StarryOS 内核 USB 后端 | DWC2 重构（PR #2066 已合并）、CrabUsb、v4l2/uvc |
| **蒋玉月** | 用户态管线 | 五段式管线、TPU 40ms、电机/舵机 UART、相机阻塞读 |

---

## 文档索引

| 文档 | 内容 |
|------|------|
| [`docs/HANDOVER.md`](docs/HANDOVER.md) | 👈 交接文档（蒋玉月→李明涛，最新现状） |
| [`docs/REAL_MACHINE_GUIDE.md`](docs/REAL_MACHINE_GUIDE.md) | 真机操作指南 |
| [`docs/MANBO1234_ANALYSIS.md`](docs/MANBO1234_ANALYSIS.md) | manbo1234 ACT 方案对比分析 |
| [`CHANGELOG.md`](CHANGELOG.md) | 变更记录 |
