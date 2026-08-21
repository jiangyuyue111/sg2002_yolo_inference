# SG2002 真机运行指南

## 📦 硬件接线

```
SG2002 (LicheeRV Nano)
  │
  ├── USB摄像头 ──→ USB口 (视频输入)
  │
  ├── UART1 (GPIOA18/19) ──→ ESP32-C3 ──→ DRV8833 ──→ N20马达×2 (左右轮)
  │    设备: /dev/ttyS1  波特率: 115200
  │    协议: 二进制帧 0xAA 0x55 <cmd> <len> <payload> <chk>
  │
  └── UART2 (GPIOA28/29) ──→ 微雪控制板 ──→ ZP10S舵机×3 (手臂+夹爪)
       设备: /dev/ttyS2  波特率: 115200
       协议: ASCII #<id>P<pulse>T<time>!
```

## 📁 SD 卡文件

### FAT32 分区 (boot)

| 文件 | 说明 | 来源 |
|------|------|------|
| `boot.sd` | StarryOS FIT 内核 (CrabUsb + Isoch) | 李明涛构建 |
| `fip.bin` | OpenSBI + U-Boot | Sipeed官方 |

### ext4 分区 (rootfs)

| 路径 | 说明 | 类型 |
|------|------|------|
| `/pipeline/real_pipeline.py` | **★ 真机主程序** | Python |
| `/pipeline/motor_driver.py` | ESP32-C3 电机UART驱动 | Python |
| `/pipeline/servo_driver.py` | ZP10S 舵机UART驱动 | Python |
| `/pipeline/image_source.py` | 摄像头帧源 (调用2.camera) | Python |
| `/pipeline/preprocessor.py` | YUYV→CHW C加速预处理 | Python |
| `/pipeline/inference.py` | TPU推理 + C NMS | Python |
| `/pipeline/position.py` | 网球位置/距离分析 | Python |
| `/guest/linux/2.camera` | V4L2摄像头采集 (640×480 YUYV) | C static |
| `/lib/preprocess_ops.so` | YUYV resize C加速库 | C .so |
| `/akars_tennis/model/yolov8n_tennis_v2.cvimodel` | YOLOv8n 网球检测模型 | cvimodel |
| `/akars_tennis/lib/libcviruntime.so` | TPU运行时库 | .so |

## 🚀 启动流程

### 1. 上电进 U-Boot
```
按任意键中断 → 输入:
fatload mmc 0:1 0x82200000 boot.sd && bootm 0x82200000
```

### 2. 设置环境
```bash
export PYTHONHOME=/
export LD_PRELOAD=/lib/libffi.so
export LD_LIBRARY_PATH=/lib:/akars_tennis/lib
```

### 3. 先单独测试电机和舵机
```bash
# 测电机 (前进→刹车→左转→停止)
PYTHONPATH=/ python3 -c "
from pipeline.motor_driver import create_motor_driver, forward
import time
m = create_motor_driver(mode='tt_pid', port='/dev/ttyS1')
forward(m, 60); time.sleep(1)
m.brake(); m.close()
print('motor OK')
"

# 测舵机 (夹爪开→关)
PYTHONPATH=/ python3 -c "
from pipeline.servo_driver import create_servo
import time
s = create_servo(driver='zp10s', port='/dev/ttyS2')
s.release(); time.sleep(1)
s.grab(); time.sleep(1)
s.close()
print('servo OK')
"
```

### 4. 启动全自动管线
```bash
cd /pipeline && PYTHONPATH=/ python3 /pipeline/real_pipeline.py
```

## 🧠 决策逻辑 (real_pipeline.py)

```
Camera抓帧 (100ms)
  └→ TPU检测网球 (40ms)
       ├─ 没有球 → 原地右转搜索
       ├─ 球很远 → 快速前进+方向修正
       ├─ 球中等 → 慢速接近+方向修正
       └─ 球很近 → 停车 → 舵机夹爪抓球
```

### 电机速度决策表

| 距离 | 基准速度 | 转向修正 | 动作 |
|------|:---:|:---:|------|
| 无目标 | 60 / -60 | — | 搜索旋转 |
| far (远) | 60 | error×0.2 | 快速追踪 |
| mid (中) | 40 | error×0.15 | 慢速接近 |
| near (近) | 0 | — | 停车夹取 |

## ⚠️ 安全提示

1. **先架高轮子** — 第一次跑不要让轮子着地，防止失控
2. **夹爪先不放球** — 空夹测试，确认 servo ID 正确
3. **Ctrl+C 会刹车** — 紧急停止会自动刹车+关舵机
4. **Python依赖** — 板上已有 pySerial，如果缺则 fallback 到 raw file I/O

## 🔧 故障排查

| 现象 | 原因 | 解决 |
|------|------|------|
| 电机不转 | ESP32没上电/UART1没配 | 检查接线，`ls /dev/ttyS1` |
| 舵机不动 | 控制板没电/ID不对 | 检查供电，servo ID=0/1/2 |
| TPU报错 | 模型路径错 | `ls /akars_tennis/model/` |
| Camera报错 | 摄像头没插 | `ls /dev/video0` |
| 串口无法打开 | pyserial没装 | 自动fallback到raw I/O |

## 📊 性能预期

| 阶段 | 耗时 |
|------|:---:|
| Camera 抓帧 | ~100ms |
| 预处理 (C) | ~143ms |
| TPU Forward | ~40ms |
| NMS (C) | ~2ms |
| 控制决策 | <1ms |
| **总计/帧** | **~287ms (3.5fps)** |
