# 执行已经配置的计划

用户只要提示词或计划时不执行本节。用户已要求出图，且本地可用 `comfy-director`、服务器与工作流配置已备好时，使用已有接口，不临时发明远程工作流或凭证。

从仓库根目录运行：

```bash
comfy-director doctor --config cluster.local.json --plan plan.json
comfy-director run plan.json --config cluster.local.json --run-dir runs/my-image
```

执行前检查：

- 配置有 background 与 foreground 的 worker/profile，URL 是调度电脑能访问的 ComfyUI。
- workflow 是目标服务器手工跑通的 API JSON；需要的模型、VAE、LoRA、自定义节点存在。
- 前景输出有真实 alpha 或独立 mask；普通 RGB 则明确安装 cutout 依赖并配置 rembg。
- 存在文字层时配置覆盖全部字形的字体，位置与字号能放进画布。
- 视频计划有 video worker/profile，能够接收上传的无字合成图；帧数、尺寸和精度适合其模型。有文字层时还需要 ffmpeg 和 ffprobe，以便视频生成后精确叠字。

同仓库的 `docs/execution.md` 和根目录 `cluster.example.json` 说明具体配置。工作流的输入输出节点须按实际导出文件填写；不要仅靠节点名称猜测 ID。机器池可任意扩展，同一角色可有多台 worker；所有前景任务按角色和容量分配。

每次运行使用可区分的目录。根据返回的任务记录定位本次素材与最终结果，不使用“最新文件”替代任务绑定。出图失败或超时先检查队列、历史和已保存的结果，避免无依据重复提交。不要自动生成无限变体。

确认某个图层执行失败后，可在原 `run` 命令末尾加 `--retry TASK` 重试该任务，沿用原计划、配置和目录。只需调整排版或图层位置时，使用 `compose` 读取缓存素材，将新图片写到原运行目录以外。

```bash
comfy-director compose adjusted-plan.json --run-dir runs/my-image \
  --font-file /path/to/font.ttc --out adjusted.png
```

报告背景和各前景的完成状态、合成图片路径、视频完成状态及未检查的画面要求。`demo` 导出人工计划与报告。`demo --compose` 用 Pillow 绘制演示素材并在本地合成，无需 LLM 或 GPU。

执行器不提供跨机共享显存、自动故障转移或自动局部重绘。用户另有这些需求时先说明可交付的实际范围，再规划补充步骤。
