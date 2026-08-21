# manbo1234 ACT 方案分析 — 与我们的对比

## 仓库概览

**manbo1234/proj57-starryos-sg2002-act**
- 在 SG2002 StarryOS 上跑 ACT（机器人动作）模型
- 两条路径: CPU (ONNX Runtime) + TPU (cvimodel)
- feat/mixed-int8-bf16 分支: TPU 推理已验证通过

## 我们的方案 vs manbo1234

| 维度 | 我们 (YOLO TPU) | manbo1234 (ACT TPU) |
|------|----------------|---------------------|
| **模型** | YOLOv8n 网球检测 | ACT 机器人动作 |
| **输入** | 单图 [1,3,640,640] | 三输入 (图像+状态+隐变量) |
| **量化** | 全 INT8 | 混合 INT8/BF16 |
| **推理引擎** | 手写 C/C++/Python CVI_NN | `model_runner` (预编译工具) |
| **Forward** | **40ms** | **68ms** |
| **fps** | 25 | 14.7 |
| **构建方式** | 手动工具链 | Docker tpu-mlir |

## 值得借鉴的点

### 1. Docker TPU-MLIR

```dockerfile
FROM sophgo/tpuc_dev:latest
# 一键模型转换，无需手动装工具链
```

我们目前是手动下载 Xuantie + TPU SDK，可以加 Dockerfile 让复现更简单。

### 2. 混合精度量化

纯 INT8 精度不够时，部分层保留 BF16：
```
纯 INT8: cos_sim = 0.985  ← 不够
混合:    cos_sim = 0.9999 ← 恢复精度
```

我们的 YOLO 全 INT8 精度足够（检出 0.969），不需要。但如果以后跑其他模型，这个方法可以救命。

### 3. model_runner

板端直接用 `/usr/bin/model_runner` 跑 cvimodel，不需要自己写推理代码：
```bash
model_runner --model m.cvimodel --input x.npz --count 10 --enable-timer
```

比我们的 C/C++/Python 代码更简单——但仅限于标准测试，不支持自定义预处理/NMS。

### 4. npz 输入格式

TPU-MLIR 原生 `.npz` 格式，比我们的自定义 `.int8` 更标准。

## 不足

| 问题 | 说明 |
|------|------|
| 无自定义推理代码 | `model_runner` 只能跑标准模型，不能加 NMS/自定义前后处理 |
| 无 Python/C++ API | 不能像我们的包一样 `from sg2002_tpu import TPUEngine` |
| 无摄像头管线 | 只做单图推理 |
| 无性能对比 | 只有 68ms，没有 CPU baseline、多语言对比 |

## 总结

```
manbo1234 的优势:
  Docker 构建 → 更易复现
  混合精度 → 精度更高
  model_runner → 开箱即用

我们的优势:
  四语言 API → 灵活集成
  NMS/前后处理 → 端到端
  性能报告 → 全维度对比
  Python 包 → 开发友好
```

两个项目互补——他们证明了混合精度可行，我们提供了完整的运行时 SDK。
