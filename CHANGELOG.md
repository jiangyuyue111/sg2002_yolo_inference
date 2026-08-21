# Changelog

本文件记录 sg2002_yolo_inference 项目的功能变更，日期倒序。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [2026-08-21] 板端动态库发布到 tpu_runtime Releases

### 新增
- **`sg2002_board_libs-20260821.tar.gz`（10MB）发布** → `sg2002_tpu_runtime` 仓库 Releases（tag `board-libs-20260821`）：含板端 rootfs `/lib` 与 `/akars_tennis/lib` 全部 23 个 `.so`（musl 运行时组 + libffi + TPU SDK 7/10 与 7/14 两套 + yolo_ops + preprocess_ops），符号链接完整保留，李明涛可直接下载解压 `tar xzf ... -C /`。
- 包内容为 **2026-08-21 真机 SD 卡 rootfs 逐字节拷贝**（`usbipd attach` → WSL2 挂载 ext4 直接读），23 个文件大小全部与板端实测清单 1:1 校验一致；sha256 `4aa2a0ea894da7317a2bef55841393a78dcb4b30417f6582f5b4abea8f64ac0f`。
- `docs/HANDOVER.md` §7.6「获取方式」补 release 下载链接（首选直接下载打包）。

## [2026-08-21] HANDOVER §7.6 板端动态库清单改为串口实测

### 变更
- `docs/HANDOVER.md` §7.6 **改为 COM6 串口实测数据**（`ls -l` 直读板端 `/lib`、`/akars_tennis/lib`），替代原先的推断表。实测要点：
  - **TPU SDK 库在 `/lib` 与 `/akars_tennis/lib` 各一份**，版本不同（7/10 版 vs 7/14 版）：`libcviruntime.so` 466KB/574KB、`libcvikernel.so`、`libcvimath.so`；`libcvi_ive_tpu.so`、`yolo_ops.so`、`yolo_ops_minimal.so` 仅 `/lib` 有（旧表未列）。
  - `/akars_tennis/lib` 自备 4MB `libc.so`（非 musl）、`libcnpy.so`、`libz.so.1.2.11`。
  - `preprocess_ops.so` 两处各一份（7/23 版 9.7KB / 7/15 版 5.9KB）。
  - Python 3.11.8 实测确认（`/bin/python3.11` 28MB，`/bin/python3` 为 symlink）；板端无 `ldd`/`readelf`，验证链接关系需 PC 端工具链。
  - 新增 musl 运行时组 `libatomic.so.1`（旧表未列）。

### 新增
- `docs/HANDOVER.md` 新增 **§7.6 板端 Python 动态库清单**：rootfs `/lib` 与 `/akars_tennis/lib` 下的关键 `.so`（musl ld/libc、libstdc++/libgcc、`libffi.so`、`libcviruntime.so`、`preprocess_ops.so`）的位置/作用/来源，附环境变量三要素（PYTHONHOME/LD_PRELOAD/LD_LIBRARY_PATH）。明确这些库在 rootfs 层、不在 tgoskits 内核仓库（李明涛在内核仓库里看不到是正常的）。

## [2026-08-21] HANDOVER 构建工作流补成果归属说明

### 变更
- `docs/HANDOVER.md` §7 引言补**成果归属说明**：内核构建方案 / `boot.sd` / FIT image（`mk-boot-sd.sh`、`build_fit.sh`）/ 启动方案笔记等**内核构建相关文档与成果均为李明涛的工作产出**（蒋玉月仅整理引用）；rootfs 布局与用户态管线搭建来自蒋玉月。§7.2 同步标注 boot.sd 构建方案归属李明涛。

## [2026-08-21] HANDOVER 补充构建工作流(§7)

### 新增
- `docs/HANDOVER.md` 新增 **§7 内核+根文件系统构建工作流**：内核构建（`cargo starry` + 产物 `starryos.bin`）、boot.sd FIT image 生成（build_fit.sh/mk-boot-sd.sh）、SD 卡双分区布局（FAT32 boot + ext4 rootfs）、部署与启动、**§7.5 重建内核必须带的 3 处 UART 改动**（ns16550.rs CLKGEN/RSTC/IOBLK + init.sh pinmux/115200 + v4l2 config）。
- `my-dev` 分支已推 GitHub fork（`jiangyuyue111/tgoskits` 的 `my-dev` 分支，tip `a006a5380`）——李明涛可 `git fetch mine my-dev` 直接拿到含 UART 改动的内核源码。

## [2026-08-21] 仓库重组:3仓库拆分(本仓库=真机管线)

### 变更
- **本仓库重建为「真机管线」专用**：移除已搬迁的 TPU 实验/内核/相机采集内容——
  `act_runtime/`、`sg2002_tpu_pkg/`、`tools/`、`benchmark_report.md`、`ACT_*.md` → 新仓库 `sg2002_tpu_runtime`；
  `tgoskits_starryos/`、`sipeed_linux*`、`kernel/`、`bare_metal_starryos/`、`v4l2-test/` + 相关 docs → 新仓库 `sg2002_starryos_experiments`（main/kernel/camera 三分支）。
- **内容未删**：全部搬到新仓库；`AKA-00`（上游参考，含独立 .git）保留在 `_staging/aka00`（135MB），README 指向在线 `chenlongos/AKA-00`。
- **README 重写**：聚焦真机管线（pipeline/board_tools/c_lib/docs），含运行状态、快速开始、目录结构、数据流、铁律、相关仓库。
- **.gitignore 更新**：排除 `AKA-00/`、`archive/`、`__pycache__/`、`*.so` 等。
- **git 历史重建**：删旧 `.git` 重新 init（旧历史完整备份在 `C:/Users/蒋玉月/sg2002_yolo_inference_legacy.git`，HEAD 4014569c5）。
- 保留：`pipeline/`、`board_tools/`、`c_lib/`、`tests/`、`images/`、`docs/HANDOVER.md`、`docs/REAL_MACHINE_GUIDE.md`、`docs/MANBO1234_ANALYSIS.md`。

## [2026-08-21] 交接文档(蒋玉月→李明涛)

### 新增
- `docs/HANDOVER.md`：交接文档——当前可运行状态定论（相机阻塞读 4.2fps / 电机映射 arg1=左轮正值=前进 / TPU 40ms 四语言全通）、**内核侧依赖清单**（UVC DMA 挂死=DWC2 内核态待重构、pipe select 不生效、v4l2/uvc 私有分支）、**铁律**（每次断电重启 / ext4 必须 sync / serial_push 用 PowerShell / Python 三要素 / 板上无 pyserial）、待办（追球摆动球放近 50cm-1m 复测、夹取标定、near 阈值）与关键文件入口。
- **交接核心要点（§0）**：电机/舵机**不是内核驱动**，是用户态 Python 管线（`motor_driver.py`/`servo_driver.py` 走 UART）驱动；要让它们动，内核侧必须带前期修好的 pinmux+时钟配置（UART1 电机 `0x64/0x68=6` + CLKGEN/RSTC/IOBLK；UART2 舵机 `0x70/0x74=2` + 115200），这些改在 StarryOS 内核构建里、不在 tgoskits。李明涛的 tgoskits 代码（DWC2/v4l2/uvc）是摄像头链路，与电机舵机无关——**直接用他的内核跑，电机舵机不会动是正常的**；接手时内核更新不要回归 UART1/2 的 pinmux/时钟配置。
- 后续由李明涛更新内核/驱动并边更新边调试小车；用户态管线现状以 HANDOVER.md 为准。

## [2026-08-21] 相机挂死收尾:阻塞读+PC端检测提示断电

### 结论(真机 08-21)
- 相机 UVC DMA 挂死是**内核态**(DWC2),用户态重启相机子进程无法恢复——
  08-21 多次真机验证(重启后仍挂)。真正的恢复要等李明涛 DWC2 重构合入内核。
- **select() 在 StarryOS 对 pipe 不生效**(真机 0.3fps,select 每块恒超时吃满
  STALL_TIMEOUT;串口 fd 能用 ≠ pipe 能用)。线程 reader 更糟(0.1fps)。
- **唯一验证可跑的是朴素阻塞读**:`stdout.read()`,~210ms/帧,4.2fps(与 08-20
  基线一致)。

### 变更
- `pipeline/image_source.py`:回退为**纯阻塞读**(RawYUYVSource 恢复原始
  `stdout.read()` 实现,去掉 select/线程/非阻塞 fd)。不再尝试挂死自恢复——
  挂死由电机看门狗刹车兜底,PC 端提示断电。
- `board_tools/pipeline_run.py`:PC 端**挂死检测**——检测到板端 `[WATCHDOG]`
  标记(3s 无帧)或管线静默超时(8s 无输出,正常搜索最长 6.3s 不误报)即
  立即 Ctrl+C 干净退出(触发管线 SIGINT 处理器刹车+关设备),并明确提示
  "相机挂死,请断电重启"。不再留下误导性的 0.x fps 结果。

## [2026-08-21] 相机读法改 select(板端高效读,不再轮询)

### 修复
- `pipeline/image_source.py`：`_read_chunk` 板端(POSIX)改 **select()** 等待管道可读——
  数据到达才返回,os.read 立即出数据,CPU 零轮询,恢复旧阻塞读的 ~210ms/帧(轮询版实测
  761→404ms 仍慢 2×)。StarryOS 对 fd 的 select 已由 motor_driver 验证支持;Windows
  管道不能 select,保留非阻塞轮询兜底(仅 PC 开发用)。

## [2026-08-21] 相机读法优化 + 首帧超时放宽

### 修复
- `pipeline/image_source.py`：`_read_chunk` 轮询效率——数据流动时 `os.read` 立即
  返回（零人为延迟），仅空管道才 10ms 轮询。真机实测修复前每帧 761ms（旧阻塞读
  210ms，轮询读慢 3.5× 给相机施压），修复后应回到 ~210ms 档位。
- `pipeline/image_source.py`：新增 `FIRST_FRAME_TIMEOUT=8.0s`——重启后的新相机
  进程初始化慢（板端 spawn 进程 ~4s），旧代码首帧超时仅 2s，会把还在初始化的
  相机误判挂死杀掉。首帧放宽后，慢启动的相机不再被误杀；稳态仍用 `STALL_TIMEOUT=2.0s`。

## [2026-08-21] 相机 DQBUF 挂死自恢复

### 新增
- `pipeline/image_source.py`：`RawYUYVSource` **挂死自恢复**——相机 UVC DMA 偶发
  DQBUF 挂死时旧代码 `stdout.read()` 永远阻塞，整条管线冻结（电机看门狗只能刹车、
  无法恢复视觉）。改为非阻塞 fd + 轮询超时（`STALL_TIMEOUT=2.0s`，Windows 管道与
  StarryOS 都适用，比 select 更稳）：超时无字节 → 杀进程重启相机子进程（最多
  `MAX_RESTARTS=3`），重启后继续采帧；连续 3 次仍挂则 `EOFError` 干净退出。
  本地假相机冒烟验证：5帧→挂→重启→再5帧，3次重启全恢复、20帧全拿到。
- 真机首次验证：`[WATCHDOG] 3s with no completed frame` 在相机挂死时正确触发并刹车
  （08-21 第二轮 chase-only，第11帧后相机挂死）。

## [2026-08-21] 电机看门狗防相机挂死失控

### 新增
- `pipeline/real_pipeline.py`：**电机安全看门狗**（08-20 真机发现的漏洞：相机偶发
  DQBUF 挂死时 `get_frame()` 阻塞，主循环停住，电机停在最后的原地转指令上不停车）。
  守护线程监测主循环距离上次 `set_speeds()` 的时间，超过 `WATCHDOG_TIMEOUT=3.0s`
  即紧急刹车并打印 `[WATCHDOG]` 日志；主循环每帧完成后 re-arm（`grabbed` 保持态、
  `grab()` 阻塞期因电机本就已刹车，看门狗触发也无害）。`shutdown()` 时 `_stop_flag`
  停止守护线程。逻辑已本地冒烟验证：健康期 0 误触发、stall 期持续刹车。

## [2026-08-20] 车跟球跑（追球）真机测试准备

### 新增
- `pipeline/real_pipeline.py`：新增 `--chase-only` 模式——追球到 near 时停车、
  不触发 `grab()`（servo0/1 底座/肩未标定，夹取会干扰观察追球行为）。追球逻辑
  抽出命名常量（`BASE_FAR/BASE_MID/TURN_GAIN_FAR/TURN_GAIN_MID/DEAD_ZONE`），
  加死区抑制中心区微调方向，加 `clamp` 限幅。日志新增 `cx`（归一化中心）与
  `err`（水平误差像素）字段，便于真机调参。
- `board_tools/motor_direction_test.py`：电机方向标定脚本（PC 端 COM6）——
  pinmux `0x64/0x68=6` + `stty 115200` 后依次驱动 4 段定时动作 `(++,--,-+,+-)`，
  供观察确认 `set_speeds` 的物理前进/转向方向。
- `board_tools/pipeline_run.py`：支持 `--chase-only` 透传给 real_pipeline.py。
- `board_tools/deploy_sd.sh`：WSL 上卡脚本——挂载 SD ext4 rootfs → 拷文件 → sync → 卸载。

### 修复
- `pipeline/motor_driver.py`：`_recv_frame()` 里 `if self._bytes_available() < 4: continue`
  在 raw fd 路径恒成立（`_bytes_available()` 返回二值 0/1 而非字节数），板端永远读不到
  ESP32 响应、握手报 `INIT: no response`。删除该 gate，改为直接 `_read_exact` 逐字节找
  帧头（与 `motor_debug.py` 一致）。PC 端 pyserial 的 `in_waiting` 不受影响，故此前只在真机暴露。
- `pipeline/real_pipeline.py`：电机方向映射最终敲定（08-20 三组客观数据交叉验证，唯一解）——
  `set_speeds(arg1,arg2)` **arg1 驱动物理左轮、arg2 驱动物理右轮**（与逻辑名一致），正值=前进；
  追球指令 `arg1=+fwd+turn, arg2=+fwd-turn`（fwd>0 前进，turn>0 右转/顺时针）。
  证据：①rot_probe `set(-15,+15)`车左转、`set(+15,-15)`车右转；②追球 `set(-25,-25)`
  车后退（球变远）→负值=后退；③追球球在右→车边退边右转（朝球）。三者唯一解 arg1=左轮、
  正值=前进。此前的 "arg1=右轮、正值=后退"（commit feefe9a）是误判——rot_probe 的 cx 漂移
  数据噪声极大（球跳动 0.18~0.9），不可据以定转向；且 motor_direction_test 的"正值=后退"
  是操作员站车前方观察的视角陷阱。据其反转导致真机追球全程倒车离球。
- `board_tools/pipeline_run.py`：Windows GBK 控制台收到板端 `✓`(U+2713) 打印崩溃，
  且崩溃在发 Ctrl+C 之前，会留下孤儿管线在板端继续驱动电机；强制
  `stdout.reconfigure(encoding="utf-8", errors="replace")` 兜底。
- `pipeline/real_pipeline.py`：**摄像头画面没有镜像**（08-20 TPU 探测决定性验证：球放车左方
  → cx=0.336/0.369/0.400，画面左侧）——早前误判"画面镜像"、加的 err 取反(`320-int(cx*640)`)
  是错的，已回滚为 `error = int(cx*640)-320`（画面左=车左）。

### 新增
- `board_tools/serial_push.py`：免拔卡推送——本地文件 base64 走 COM6 串口 → 板端
  `base64 -d` 还原为 `.new` → 板端 Python 校验（字节数/null/字节和）→ `mv && sync`。
  SD ext4 必须显式 sync，否则重启后页缓存丢失导致文件成 null 字节。

### 真机验证（2026-08-20）
- `motor_direction_test.py`：段1=后退、段3=左旋、段4=右旋 → 正值=后退敲定；
  但段3/4 的左右与操作员视角（站车前）相关，无法据此定 arg1=哪侧轮，只能定符号（视角陷阱）。
- `cx_probe2.py`（TPU 探测）：球放**车左方** → cx=0.336/0.369/0.400，画面**未镜像**，
  推翻早前"镜像"误判。
- `rot_probe.py`（转向探针）：电机原地慢转 + 摄像头盯球逐帧报 cx。`set(-15,+15)`→车左转、
  `set(+15,-15)`→车右转。但 cx 漂移数据噪声极大（球跳动 0.18~0.9），单凭它无法定 arg1=哪侧轮
  （存在「arg1=右轮且正值=前进」的等价解，需结合追球翻译方向才能唯一确定）。
- `pipeline_run.py --chase-only` 复测（chase_test_4，13帧，arg1=右轮+正值=后退 错误期间）：
  操作员观察到"车离球更远、向后边退边转"——追球指令全是负值=后退，车全程倒车。据此（负值=后退）
  结合 rot_probe 转向方向，唯一解出 **arg1=左轮、正值=前进**，已改为 `arg1=+fwd+turn`。
- `pipeline_run.py --chase-only` 复测（chase_test_5，134帧/4.4fps）**确认成功**：电机全正、
  球 size 0.036→0.145（放大4倍）、~3.5s 到 near 停车，操作员确认"离球更近"。映射定论。
  遗留：cx 0.09↔0.91 摆动，操作员确认"来回向前逼近"（车头左右摆，非球滚动）= 转向过冲。
- 降转向增益消除蛇形（chase_test_5 后）：`TURN_GAIN_MID 0.08→0.05`、`TURN_GAIN_FAR 0.10→0.06`、
  `BASE_MID 25→22`、`DEAD_ZONE 20→25`，待真机复测确认不再摆。
- 检测稀疏调参（真机观察后）：球放 1m 处 size≈0.009 太小 + conf 0.5 偏严 + SEARCH 30 过快一扫而过。
  `conf_threshold 0.5→0.35`、`SEARCH_SPEED 30→20`，并建议球放近些(~50cm)复测。
- 速度档位整体下调约一半（真机追球太快）：`SEARCH 60→30, BASE_FAR 60→35,
  BASE_MID 40→25, TURN_GAIN_FAR 0.20→0.10, TURN_GAIN_MID 0.15→0.08`，待复测确认手感。

## [Unreleased]

### 修复
- `pipeline/servo_calibrate.py`：`KEY_ACTIONS` 里夹爪（servo2）角度与
  `servo_driver.py` 对齐——`张开(prepare)` 100°→135°、`闭合(grab)` 60°→88°，
  原 60°（1167 脉冲）低于全闭极限会堵转，校准对照表误导读数已纠正。
- `pipeline/servo_driver.py`：夹爪（servo2）角度校准为真机实测——机械行程约
  1462（全闭）~2023（全开）脉冲，远窄于标准 500~2500；高脉冲=开、低脉冲=闭。
  原 `servo2_grab=60°`（1167 脉冲）低于全闭极限会堵转，`servo2_prepare=100°`
  （1611 脉冲）只算半开；改为 `servo2_prepare/approach=135°`（2000）、
  `servo2_grab/lift=88°`（1478），留余量不顶死。

### 调试发现（真机 08-18）
- 夹爪数据线插反会短路单线总线，底座/肩/夹爪全部失声（重启也无法恢复）；
  拔掉夹爪信号线 ID0/1 即恢复，插对方向后 ID2 上线。
- ZP10S 时间字段必须 4 位：`T800`（3 位）被舵机忽略，`T1000`（4 位）才生效。
  板端脚本发位置指令务必用 `T{:04d}` 格式（`servo_driver.set_angle` 已正确）。

### 新增
- `board_tools/servo_scan_readback.py`：读回式 ID 扫描（PVER 确定真实舵机 ID）。
- `board_tools/servo_gripper_cal.py` / `servo_gripper_diag.py` /
  `servo_gripper_openclose.py` / `servo_gripper_timefmt.py`：夹爪开合标定与诊断工具。

## [2026-08-18] 整机抓球闭环联调准备

### 修复
- `pipeline/servo_driver.py`：修正 `_angle_to_pulse` 量程 270°→180°
  （手册 1500≈90°，原 `/270` 公式把所有角度只转到 2/3，`servo2_grab=90°`
  实际只到 1166 而非 1500）。`_angles` 角度值同步换算为 180° 量程，
  保持实际 pulse 与 AKA-00 参考一致。
- `pipeline/servo_driver.py`：`grab()` 补全多步序列（张开→下探→闭合→抬臂），
  参考 AKA-00 `uart_control.py grab()`，原实现只动夹爪单步。
- `pipeline/real_pipeline.py`：修正夹取后逻辑——原「丢目标即 release」会在夹取
  瞬间手臂挡球、检测丢失，导致球当场掉落；改为 grabbed 状态保持刹车+夹住，
  不因检测丢失而松开。

### 新增
- `pipeline/servo_driver.py`：`restore_torque()`（`#000/#001/#002 PULR!`）防御
  释力状态，构造时自动调用。
- `pipeline/servo_calibrate.py`：真机舵机校准工具（单点 / 全量程扫描 / 关键动作对照）。

### 待办（真机）
- 用 `servo_calibrate.py --sweep` 确认真机量程是 180° 还是 270°。
- 按实测校准 `_angles`（夹爪开/闭、下探/抬臂）。
- `real_pipeline.py` 的 near 阈值（size_ratio>0.05）按真机网球框大小调。

## [2026-08-14] 舵机联调验证 + 板端串口工具链

### 新增
- `board_tools/serial_shell.py`：COM6 串口控制台交互（发命令 / 读回显 / raw 模式）
- `board_tools/servo_probe.py`、`servo_driver_test.py`：板端舵机查询 / 管线代码路径测试
- `board_tools/baud_probe.py`、`baud_probe2.py`：波特率重置决定性测试
- `board_tools/pipeline_run.py`：全管线运行器（流式读输出 + Ctrl+C 干净停止）

### 验证
- 舵机（ZP10S，UART2）联调通过：pinmux `0x70/0x74=2` + `stty 115200` + `#000P...` 指令，舵机实际转动
- `servo_driver.py` 管线路径（`create_servo`→`_open_raw`→`set_angle`）板上验证通过

### 结论（纠正 08-13 认知）
- Python `tcsetattr(B115200)` 能正确设波特率（非 no-op）；板上无 pyserial，raw fd 路径有效
- 偶发「舵机不转」非波特率，疑为释力状态，`#000PULR!` 恢复扭力即可

## [2026-08-13] 舵机/电机驱动修复，打通闭环

### 修复
- `pipeline/servo_driver.py`：`ZP10S` 增加 raw fd + termios 波特率 fallback，
  板端（无 pyserial）不再静默失败；`set_angle` 去掉多余换行（`!` 即结束符）。
  根因：舵机严格 115200，而板端 tty 默认 38400 且缺 pyserial，之前 `servo.grab()`
  在板端一直是 no-op。
- `pipeline/real_pipeline.py`：修正 `center_x` 单位——`PositionResult.center_x`
  是归一化 [0,1]，原代码误当像素（`cx - 320`）计算方向误差，导致车恒偏转。

### 新增
- `pipeline/motor_driver.py` v2：完整帧解析 + 4 步握手校验 + CMD_DIAG(0x30) 诊断 + NACK 错误码
- `pipeline/motor_debug.py`：板端 5 层诊断（串口→握手→状态→转动→刹车，15/15 通过）
- `pipeline/uart_loopback.py`：UART 回环测试（短接 TX/RX 自发自收）
- `pipeline/motor_test.py`、`pipeline/motor_raw_test.py`：最小电机转动测试
- `pipeline/regdump.py`：UART 寄存器诊断
- `tests/esp32_protocol_test.py`：PC 端 ESP32 全协议测试（26/26）
- `docs/REAL_MACHINE_GUIDE.md`：真机操作指南（接线/测试/故障排查）

## [2026-07-28] 项目初始化 + 真机管线

### 新增
- Initial commit：SG2002 StarryOS 全栈项目（TPU 推理 + 检测管线 + 控制层）
- `pipeline/real_pipeline.py`：真机全自动管线 Camera → TPU → 状态机 → 电机/舵机
- 贡献者说明（区分李明涛 / 蒋玉月成果）
