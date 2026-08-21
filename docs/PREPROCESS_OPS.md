# preprocess_ops 预处理库 — 来源与 YUYV 格式说明

> 面向李明涛的说明:回答两个问题——`preprocess_ops.c` 从哪来的、以及 UVC 原生 YUYV 是 packed 非 planar 时管线如何处理。

## 1. 它是什么

`preprocess_ops.c` → `preprocess_ops.so` 是**蒋玉月手写的 C 图像预处理库**,给 SG2002 TPU 管线做加速,非第三方库、无上游。核心动机写死在文件头:

> Python pure-loop resize takes 11s on RISC-V

纯 Python 在板子上 resize 一帧要 11 秒,所以用 C 重写。当前版本 `v3.0.0`(`preprocess_ops_version()` 返回,注释注明 `v3: added nms_decode`)。

包含函数(见 `c_lib/preprocess_ops.c`):

| 函数 | 作用 |
|------|------|
| `bgr_resize_planar` | BGR HWC → CHW planar uint8 + bilinear resize |
| `bgr_letterbox_planar` | 同上 + YOLO letterbox(114 灰填充) |
| `yuyv_resize_planar` | **packed YUYV422 → CHW planar BGR**,融合 YUV→BGR + resize |
| `nms_decode` / `nms_decode_compact` | TPU 输出 NMS 后处理 |
| `resize_letterbox_planar` | 旧 TGOSKits 接口兼容(`tpu_infer_v4.py` 用) |

## 2. 来源链

```
7月中旬 ── 写 preprocess_ops.c 配 tpu_infer_v4.py 用
   └─ 位置: tgoskits_starryos/python/ (starryos_experiments 仓库)
   └─ 编译: build_c_lib.sh → riscv64-unknown-linux-musl-gcc -shared → preprocess_ops.so
   └─ 部署注释: /lib/ 或 /akars_tennis/lib/ (与板上实际路径一致)

8-21 三仓库重组
   ├─ starryos_experiments commit 0c2ef79: 仓库初始即含 tgoskits_starryos/python/preprocess_ops.c
   └─ 主仓库 commit 3ecf8a7: 收进 c_lib/preprocess_ops.c 并迭代到 v3
        (新增 yuyv_resize_planar + nms_decode)
```

板上现有两份 `.so` 就是这个库的两个迭代:

| 位置 | 大小 | 版本对应 | 说明 |
|------|------|---------|------|
| `/lib/preprocess_ops.so` | 9.7KB | 7/23 新版 = 主仓库 `c_lib/` | 含 `yuyv_resize_planar` + `nms_decode`,run.py 用这份 |
| `/akars_tennis/lib/preprocess_ops.so` | 5.9KB | 7/15 旧版 = `tgoskits_starryos/python/` | 纯 resize 版,`tpu_infer_v4.py` 时代 |

## 3. UVC 原生 YUYV 是 packed,不是 planar —— 这正是管线设计输入

**UVC/V4L2 原生 YUYV = packed(打包交错)**:每 4 字节 `Y0 U Y1 V` 表示 2 个像素,帧长 `W*H*2` 字节。**不是** planar(Y / U / V 各占一个平面)。

**管线不需要内核输出 planar。** `yuyv_resize_planar()`(`c_lib/preprocess_ops.c:257`)就是专门消费 packed YUYV422 的:

```
入参: yuyv = W*H*2 字节, packed YUYV (Y0,U,Y1,V 每 macropixel 4 字节)
  ① 逐 macropixel 整数 YUV→BGR   (yuyv_to_bgr_pair, preprocess_ops.c:209, ITU-R BT.601 定点 8.8)
  ② bilinear resize → CHW planar BGR uint8 [3][640][640]
输出: 直接裸 uint8 [0,255], 不量化 —— 模型校准用裸值, 硬件 reinterpret 为 int8
```

全 C 整数运算,一次到位。preprocess 阶段板测 ~143ms(camera 100ms + pre 143ms + tpu 40ms + nms 2ms,run.py v7)。

## 4. 接口对接(2.camera ↔ preprocess_ops)

```
2.camera (UVC, V4L2, 640×480 YUYV)
  └─ stdout 输出 packed YUYV (W*H*2 = 614400 字节/帧)
       ↓  RawYUYVSource 按 W*H*2 读帧        (pipeline/image_source.py:271)
       ↓  run.py:96  pp.run_direct(yuv, sw, sh, tpu_in_ptr)   # 0-copy 直写 TPU 输入缓冲
  yuyv_resize_planar → CHW planar BGR → TPU
```

**结论:2.camera 输出格式不用改。** 两边接口(帧长 `W*H*2`、packed 布局)正好对上,`yuyv_resize_planar` 吃的就是 2.camera 输出的 packed YUYV。

## 5. 相关代码位置

| 位置 | 说明 |
|------|------|
| `c_lib/preprocess_ops.c` | 源码(v3,主用) |
| `tgoskits_starryos/python/preprocess_ops.c` | 旧版源码(starryos_experiments 仓库) |
| `tgoskits_starryos/python/build_c_lib.sh` | 交叉编译脚本 |
| `pipeline/run.py` | 板端 v7 主程序,0-copy 调 `yuyv_resize_planar` |
| `pipeline/board_camera.py` / `real_pipeline.py` | 同款 C 预处理调用 |
| `pipeline/preprocessor.py` | ctypes 封装(含 PC numpy fallback) |
