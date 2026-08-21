# SG2002 网球捡球小车 — 交接文档

> 日期：2026-08-21
> 交接方：蒋玉月（用户态管线 / 调试）
> 接手方：李明涛（内核 / 驱动 / 后续边更新边调试小车）
>
> 面向读者：接手方。本仓库是**用户态管线**（PC 端开发 + 板端运行），内核与驱动在 StarryOS / tgoskits，由接手方持续更新。此文档给出接手时**当下可运行状态、内核侧依赖、铁律与待办**，避免重复踩坑。

---

## 0. 交接核心要点（先读）

> ⚠️ **电机和舵机不是内核在驱动，是用户态 Python 管线在驱动。**
> 李明涛接手后直接用他的内核跑，电机/舵机**不会动**——这是正常现象，不是他的驱动坏了。

- **驱动链**：电机（`/dev/ttyS1`）和舵机（`/dev/ttyS2`）由用户态 Python 管线（`motor_driver.py` / `servo_driver.py`）走 UART 驱动，内核只提供 UART 设备节点。
- **要让电机/舵机动，内核侧还必须带上前期修好的 pinmux + 时钟配置**（这些改在 **StarryOS 内核构建**里，**不在 tgoskits**）：
  - UART1 电机：pinmux `0x64/0x68=6`（JTAG 脚复用，`0x68` 是当初遗漏的关键）+ CLKGEN/RSTC/IOBLK 三层时钟复位修复
  - UART2 舵机：pinmux `0x70/0x74=2` + 115200 波特率
- **李明涛的 tgoskits 代码（DWC2 重构 / v4l2 / uvc）是摄像头链路，与电机舵机无关**。他的内核本身不含上述 pinmux 配置，所以"他的代码不能驱动电机舵机"——用户态这边已经用绕行方案（用户态驱动 + 这些内核配置）把整链路跑通了。
- **接手时注意**：内核更新（尤其 pinmux / CLKGEN / RSTC 相关）**不要回归** UART1/UART2 的时钟与引脚复用配置，否则电机舵机"不能驱动"的现象会重新出现。

---

## 1. 当前可运行状态（2026-08-21 真机定论）

| 环节 | 状态 | 结论 |
|------|------|------|
| 相机抓帧 | ✅ 可跑 | `/guest/linux/2.camera` 流式输出 raw YUYV422（640×480×2B），**~210ms/帧、4.2~4.4fps** |
| 相机读法 | ⚠️ 唯一可行是阻塞读 | 见 §2 内核依赖 |
| 预处理 | ✅ C 加速 | `preprocess_ops.so` YUYV→CHW，~143ms |
| TPU 推理 | ✅ 全通 | `yolov8n_tennis_v2.cvimodel`，Forward ~40ms；Python/C/C++/Rust 四语言都验证过 |
| NMS | ✅ C 版 | ~2ms |
| 电机 | ✅ 方向定论 | `set_speeds(arg1,arg2)` **arg1=物理左轮、arg2=物理右轮、正值=前进**；追球跑通（chase_test_5：134帧/4.4fps，球 size 0.036→0.145，~3.5s 到 near 停车） |
| 舵机 | ✅ 联调通过 | ZP10S ×3（底座/肩/夹爪），UART2，180° 量程；夹爪 135°(开)/88°(闭)（脉冲高=开） |
| 管线总链路 | ✅ 可跑 | 阻塞读 → 预处理 → TPU → NMS → 决策 → 电机/舵机，4.2~4.4fps |

**关键历史纠正（已定论，勿再反转）**：电机映射曾误判「arg1=右轮/正值=后退」（commit `feefe9a`），导致真机追球全程倒车。三组客观数据（rot_probe 转向 + 追球平移方向）唯一解出 arg1=左轮/正值=前进（`3ca00e8`、`28d6ff8`）。

---

## 2. 内核侧依赖（接手方关键清单）

用户态在**等内核修好**的几件事，修好前用户态只能绕行：

### 2.1 相机 UVC DMA 挂死（最痛）
- **现象**：相机偶发 DQBUF 挂死，`get_frame()` 永久阻塞，整条管线冻结，电机停在最后的原地转指令上。
- **根因**：**内核态** DWC2 DMA 挂死，用户态重启相机子进程**无法恢复**（多次真机验证，重启后仍挂）。
- **当前收尾方案**（用户态不自恢复）：
  1. 板端电机看门狗（3s 无完成 loop → `[WATCHDOG]` 紧急刹车）；
  2. PC 端 `board_tools/pipeline_run.py` 检测到 `[WATCHDOG]` 或 8s 静默超时 → 立即 Ctrl+C 干净退出 + 提示**断电重启**。
- **真修复**：等接手方的 **DWC2 重构**合入生效（PR [#2066](https://github.com/rcore-os/tgoskits/pull/2066) 已于 2026-08-19 合入官方 `rcore-os:dev`，merge commit `54adcc294`，但未在 SG2002/StarryOS 上验证生效）。合入后用户态可恢复「挂死→重启子进程自恢复」。

### 2.2 StarryOS pipe 的 select() 不生效
- **现象**：对相机管道 fd 做 `select()`，每块恒超时吃满 STALL_TIMEOUT → 0.3fps；线程 reader 更糟（0.1fps）。注意：**串口 fd 的 select 能用 ≠ pipe fd 能用**（电机驱动已验证串口 select 有效）。
- **当前绕行**：用户态只能纯阻塞 `stdout.read()`（210ms/帧，唯一实时模式）。
- **真修复**：内核 pipe 语义稳定后，用户态可回 select/非阻塞高效读（参考 `image_source.py` 里 RawYUYVSource 的注释，旧实现还在 git 历史里）。

### 2.3 v4l2 / uvc 驱动
- v4l2-core / videobuffer crate + uvc 驱动 + vivid test driver 在接手方私有 `dev/v4l2` 分支，尚未进入官方 dev。
- 用户本地 tgoskits `my-dev` 分支（HEAD `b6af56161`）把接手方分支合并进来，但**残留 57 文件 / 477 个冲突标记**——两套互斥的 UVC capture 架构（HEAD: IRQ completion callback model vs 接手方: IsoBatchPipeline worker model）。冲突集中在 media/uvc(6)、media/v4l2-core(20)、videobuffer(3)、media/vivid(6)、dwc2(6)。接手方自己处理即可，用户态不依赖该合并。

---

## 3. 铁律（操作约束，接手方必读）

1. **每次跑管线必须断电重启板子**——相机 UVC DMA 状态会随运行退化，连续跑第二次相机大概率挂死。
2. **SD ext4 必须显式 `sync`**——拷完文件不 sync，重启后页缓存丢失，文件变全 null 字节。
3. **`serial_push.py` 免拔卡推送**：transfer 步可靠，但 verify 步会被内核 debug 日志淹没、总是"失败"——不要据此判定推送失败；用长窗口 raw probe + 正则 `VFY_RESULT\s+(\d+)\s+(\d+)\s+(\d+)` 手动验证，然后 `mv -f && sync`。
4. **PC 端必须用 PowerShell 跑 `serial_push.py`**——Git Bash 的 MSYS path mangling 会把板端绝对路径（`/pipeline/...`）转成 `D:/Git/Git/pipeline/...`，推送失败。
5. **console 多次串口操作后卡死**（echo only）——断电恢复，不是驱动问题。
6. **Python 三要素**（板端跑 Python 必须带）：`LD_PRELOAD=/lib/libffi.so`、`PYTHONHOME=/`、`-u`（否则 stdout 缓冲导致看不到实时日志）。板上**无 pyserial**，串口走 raw fd + termios。
7. **追球电机方向**：不要用 `motor_direction_test.py` 的"正值=后退"（操作员站车前方的视角陷阱）；以 §1 定论为准。

---

## 4. 待办清单（接手后按优先级）

| # | 事项 | 说明 |
|---|------|------|
| 1 | **追球摆动复测** | 已降增益（`f0b7690`：TURN_GAIN_MID 0.08→0.05、TURN_GAIN_FAR 0.10→0.06、BASE_MID 25→22、DEAD_ZONE 20→25，已推板）。08-21 复测 cx 仍 0.02↔0.91 跳、size 对静止球 0.009-0.031——但球放太远（1.5-2m）检测噪声大。**下次把球放近 50cm–1m 复测**，分离"检测噪声"和"转向过冲"，再定是否继续降增益或加微分阻尼。 |
| 2 | **夹取序列真机标定** | `servo_calibrate.py --sweep` 确认真机量程 180° 还是 270°；按实测校准 `_angles`（夹爪开/闭、下探/抬臂）。当前 full 模式（chase+grab）夹取还不可靠，追球调参用 `--chase-only`。 |
| 3 | **near 阈值** | `real_pipeline.py` 的 near 判定 `size_ratio>0.05` 按真机网球框大小调。 |
| 4 | **DWC2 重构合入后** | 用户态重新启用：①相机挂死自恢复（重启子进程）；②pipe select 高效读。`image_source.py` 旧实现都在 git 历史可回。 |

---

## 5. 关键文件与入口

**PC 端（Windows，COM6 / UART0 console 115200-8N1）**
| 文件 | 作用 |
|------|------|
| `board_tools/pipeline_run.py` | 跑管线运行器：串口发启动命令 + 流式读输出 + 挂死检测 + Ctrl+C 干净停止。用法 `python pipeline_run.py [秒数] [--chase-only]` |
| `board_tools/serial_push.py` | 免拔卡推送文件到板端（base64 走串口 → 板端还原校验 → mv+sync） |
| `board_tools/deploy_sd.sh` | WSL 下挂载 SD ext4 → 拷文件 → sync → 卸载 |
| `board_tools/motor_direction_test.py` | 电机方向标定（注意 §3.7 视角陷阱） |
| `board_tools/servo_calibrate.py` | 舵机校准工具 |

**板端（ext4 rootfs）**
| 路径 | 作用 |
|------|------|
| `/pipeline/real_pipeline.py` | ★ 真机主程序（状态机 + 看门狗 + 决策） |
| `/pipeline/image_source.py` | 相机帧源（RawYUYVSource，纯阻塞读） |
| `/pipeline/motor_driver.py` | ESP32-C3 电机 UART 驱动（二进制帧协议） |
| `/pipeline/servo_driver.py` | ZP10S 舵机 UART 驱动（ASCII 协议） |
| `/guest/linux/2.camera` | V4L2 摄像头采集（640×480 YUYV，raw 流输出） |
| `/akars_tennis/model/yolov8n_tennis_v2.cvimodel` | 网球检测模型 |

**硬件连接**：USB 摄像头→USB口；UART1(/dev/ttyS1)→ESP32-C3→DRV8833→N20×2；UART2(/dev/ttyS2)→微雪控制板→ZP10S×3。

---

## 6. 变更纪律

- 本仓库工作流：改动须 commit + 更新 `CHANGELOG.md`（日期倒序，格式见文件头），本地提交即可不强制 push。
- 时间线要点（CHANGELOG 里都有）：08-20 电机映射定论→追球跑通；08-21 相机挂死收尾（`abd339b63`）、降增益待复测（`f0b7690`）。

---

## 7. 内核 + 根文件系统构建工作流（接手方）

> 2026-08-21 补充：接手方若需重建内核 / rootfs，按本节流程。**让舵机/电机动的那 3 处改动在接手方 fork 的 `my-dev` 分支**（`https://github.com/jiangyuyue111/tgoskits`），重建内核必须带上，见 §7.5。
>
> **成果归属说明**：本节所涉内核构建方案、`boot.sd`/FIT image 生成（`mk-boot-sd.sh`、`build_fit.sh`）、启动方案笔记等**内核构建相关文档与成果均来自李明涛**（此前由李明涛构建/提供，本交接方仅整理引用，便于接手方使用）；rootfs 布局与用户态管线搭建来自蒋玉月。

### 7.1 内核构建（tgoskits / StarryOS）

```bash
# 源码：建议拉接手方 fork（含 UART 改动）
git remote add mine https://github.com/jiangyuyue111/tgoskits.git
git fetch mine my-dev

# 编译（Linux-musl PIE，产物在 target 目录）
cd /path/to/tgoskits
cargo starry --config os/StarryOS/configs/board/licheerv-nano-sg2002.toml
# 产物: target/riscv64gc-unknown-linux-musl/release/starryos.bin (~13MB)
```

> 依赖：Rust nightly + `riscv64gc-unknown-linux-musl` 目标；交叉编译器 `riscv64-linux-musl-gcc`。

### 7.2 生成 boot.sd（FIT image：内核 + 设备树）

> 本小节构建方案（FIT ITS、`mk-boot-sd.sh`、`build_fit.sh`）为**李明涛内核构建工作成果**（见 §7 引言归属说明）。

```bash
# 需 mkimage（apt install u-boot-tools）
./build_fit.sh   # 或 tgoskits 自带 scripts/mk-boot-sd.sh
# 输入: starryos.bin + os/StarryOS/configs/board/licheerv-nano-sg2002.dtb
# 产物: boot.sd (12.8MB, SD 卡 boot 分区里的那个文件)
```

> ⚠️ 内核是 Linux-musl PIE，**必须 FIT + `bootm`**，不能 `go 0x80200000`。

### 7.3 SD 卡布局（rootfs）

| 分区 | 文件系统 | 内容 |
|------|---------|------|
| 分区1 `sde1` | FAT32 ~512MB | `boot.sd`、`fip.bin`(OpenSBI+U-Boot 固件)、`ext4_100m.img` |
| 分区2 `sde2` | ext4 ~7GB | **rootfs**：busybox + Python 3.11 (riscv64-musl) + 用户态文件 |

rootfs 结构（已构建好，**增量更新即可，无需从零做**）：
```
/ (ext4)
├── bin/python3.11          # RISC-V Python 3.11 (27.9MB)
├── lib/                    # musl 动态库 + libpython3.11 + stdlib
├── pipeline/               # ★ 真机管线 (real_pipeline.py 等)
├── guest/linux/2.camera    # v4l2 采集 (640×480 YUYV)
└── akars_tennis/model/     # yolov8n_tennis_v2.cvimodel + .so 库
```

### 7.4 部署 & 启动

```bash
# 更新内核：只换 boot.sd
mount /dev/sde1 /mnt/sde1
cp /mnt/sde1/boot.sd /mnt/sde1/boot.sd.old_$(date +%Y%m%d)  # 备份
cp new_boot.sd /mnt/sde1/boot.sd
sync && umount /mnt/sde1

# rootfs 增量更新（免拔卡，走串口）：PC 端 PowerShell 跑 serial_push.py
python board_tools/serial_push.py <文件> <板端路径>

# 启动
# U-Boot: fatload mmc 0:1 0x82200000 boot.sd && bootm 0x82200000
# root@starry:/ #
```

### 7.5 ★ 重建内核必须带的 3 处改动（`my-dev` 分支，让舵机/电机动）

| 文件 | 改动 | 作用 |
|------|------|------|
| `drivers/ax-driver/src/serial/ns16550.rs` | 加 `sg2002_uart_enable_clock()` / `sg2002_uart_ioblk_init()` | CLKGEN 时钟使能 + RSTC 复位解除 + IOBLK 上拉（UART1/2） |
| `os/StarryOS/starryos/src/init.sh` | `/dev/pinmux` 写 `0x64 6` `0x68 6` `0x70 2` `0x74 2` + `stty 115200` | UART1 电机 / UART2 舵机引脚复用 + 波特率 |
| `os/StarryOS/configs/board/licheerv-nano-sg2002-v4l2.toml` | 新增 v4l2 配置（含 sg2002-dwc2） | 相机链路 |

> 新内核 update 时**不要回归**这些，否则电机/舵机"不动"会重现。这些改在 tgoskits，与摄像头链路（DWC2/v4l2/uvc）无关。

### 7.6 板端 Python 动态库清单（rootfs `/lib` 与 `/akars_tennis/lib`）

> 这些库在**板端 rootfs（SD 卡 ext4）**，**不在 tgoskits 内核仓库**——内核源码树只产出 `starryos.bin`/`boot.sd` 并对外提供设备节点（`/dev/ttyS1` 电机、`/dev/ttyS2` 舵机、`/dev/video0` 相机、`/dev/tpu`）；Python 解释器 + 动态库属 rootfs 层，内核仓库里看不到是正常的。

环境变量三要素（任何板端 Python 都必须带，见 §3.6）：
```bash
export PYTHONHOME=/
export LD_PRELOAD=/lib/libffi.so
export LD_LIBRARY_PATH=/lib:/akars_tennis/lib
```

下表为 **2026-08-21 实测**（COM6 串口直读板端 `ls -l`），与旧推断表有出入，以此为准。

**A. musl 运行时组**（来源：`riscv64-linux-musl-cross` 工具链 + Python 3.11 musl 运行时）—— 全在 `/lib`：

| 库 | 实测大小 | 作用 |
|----|---------|------|
| `ld-musl-riscv64.so.1 → libc.so` | symlink | musl 动态链接器（所有 .so 的加载入口） |
| `libc.so` | 875KB | musl C 库 |
| `libstdc++.so → libstdc++.so.6` | 16MB | C++ 运行时（TPU SDK / c_lib 依赖） |
| `libgcc_s.so.1` | 598KB | GCC 运行时 |
| `libatomic.so.1` | 142KB | 原子操作运行时（RISC-V 无原生 CAS 指令时必需） |
| **`libffi.so → libffi.so.8`** | 37KB | **Python ctypes 必需**（`LD_PRELOAD=/lib/libffi.so`） |

**B. TPU SDK 组**（来源：TPU SDK `tpu-sdk-sg200x`，CVI_NN 运行时）—— **`/lib` 与 `/akars_tennis/lib` 各一份**，版本不同：

| 库 | `/lib`（7/10 版） | `/akars_tennis/lib`（7/14 版） | 作用 |
|----|---------|---------|------|
| `libcviruntime.so` | 466KB | 574KB | TPU CVI_NN 推理运行时（核心） |
| `libcvikernel.so` | 279KB | 308KB | TPU kernel |
| `libcvimath.so` | 56KB | 66KB | TPU 数学库 |
| `libcvi_ive_tpu.so` | 577KB | —（仅 `/lib` 有） | IVE 图像处理 TPU 库 |
| `yolo_ops.so` | 82KB | —（仅 `/lib` 有） | TPU YOLO 算子 |
| `yolo_ops_minimal.so` | 12KB | —（仅 `/lib` 有） | TPU YOLO 精简算子 |

> `/akars_tennis/lib` 还自备一套：`libc.so`（**4MB，非 musl，TPU SDK 自带完整 C 库**）、`libcnpy.so`（读 .npz）、`libz.so.1.2.11`（zlib）、`libstdc++.so.6`/`libgcc_s.so.1`。`LD_LIBRARY_PATH=/lib:/akars_tennis/lib` 顺序即解析优先级：**musl 组和 cvi 组先命中 `/lib`**，akars 里的 4MB libc 只在 TPU 库自身需要时兜底。

**C. 仓库自产**（来源：本仓库 `c_lib/` 交叉编译 `make riscv`）：

| 库 | 位置 | 实测大小 | 作用 |
|----|------|---------|------|
| `preprocess_ops.so` | `/lib` 与 `/akars_tennis/lib` **各一份**（7/23 版 9.7KB / 7/15 版 5.9KB） | — | YUYV→CHW C 加速预处理（~143ms → 更快） |

**Python 运行时本体**：解释器 `/bin/python3.11`（28MB，实测 `Python 3.11.8`，`/bin/python3` 为 symlink）+ 标准库 `/lib/python3.11/`；板端 `site-packages` 目前仅 `README.txt`，无 pip 包。

> ⚠️ **板端 busybox 没有 `ldd`/`readelf`**（`ldd: not found`），验证 .so 链接关系需在 PC 端用 `riscv64-linux-musl-objdump -p` / `readelf -d` 查。

**获取方式**：
1. **直接下载打包（推荐）**：`sg2002_board_libs-20260821.tar.gz`（10MB，含上表全部 23 个 `.so`，符号链接完整保留）→
   `https://github.com/jiangyuyue111/sg2002_tpu_runtime/releases/tag/board-libs-20260821`，板端解压：`tar xzf sg2002_board_libs-20260821.tar.gz -C /`。此包为 2026-08-21 从真机 SD 卡 rootfs 逐字节拷贝（与串口实测清单 1:1 一致）。
2. 从已跑通的板子 rootfs 直接拷 `/lib`、`/akars_tennis/lib`（`sync` 后落盘）；或按来源列从交叉工具链 / TPU SDK / 仓库 `c_lib/` 重新获取。
