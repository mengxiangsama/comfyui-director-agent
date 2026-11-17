# CreativePlan 1.0

用户要保存 JSON 或执行任务时使用。计划不包含服务器地址、密钥、任意代码或工作流定义。仓库安装后以 `comfy-director schema` 和 `src/comfyui_director/models.py` 为权威；示例位于 `examples/poster-plan.json`、`examples/video-plan.json` 与 `examples/multi-layer-plan.json`。

必须提供这些顶层字段：

```text
schema_version: "1.0"
title: 非空字符串
request: 原始需求
medium: "image" 或 "video"
canvas: {width, height}
visual: {style, palette, lighting, camera, composition, constraints: []}
full_image_prompt: PromptSpec
background: PromptSpec
foreground: PromptSpec
placement: {x, y, width, height, feather_px, instructions, acceptance: []}
extra_layers: [{id, prompt: PromptSpec, placement: Placement}]
text_layers: [{text, x, y, font_size, color}]
video: null 或 VideoSpec
assumptions: []
```

各提示词对象 `PromptSpec`：

```json
{
  "positive_zh": "完整详细中文描述",
  "positive_en": "Detailed English description of the same picture.",
  "negative": "",
  "acceptance": ["至少一条可检查要求"]
}
```

`VideoSpec`：

```json
{
  "positive_zh": "合成图作为首帧，主体缓慢抬头，雨落下，镜头轻推。",
  "positive_en": "Use the composited image as the starting frame. The subject slowly raises its head, rain falls, and the camera gently pushes forward.",
  "negative": "",
  "duration_seconds": 5.0625,
  "fps": 16,
  "frames": 81,
  "acceptance": ["主体外观稳定，运动连续"]
}
```

结构要求：

- 不能添加未定义字段。
- `canvas.width/height` 在 64–8192 之间且为 8 的倍数。
- `placement` 左上角不能为负，宽高为正且不超过 8192，整个矩形必须在画布内。`feather_px` 为 0–32。
- `extra_layers` 可为空，最多 31 项。每项 `id` 以英文字母开头，最多 48 位，只用字母、数字、下划线或连字符；不能重名或使用 `background`、`foreground`、`compose`、`video`。每项的 `placement` 同样受画布约束。
- 合成先放主要前景，再按 `extra_layers` 数组顺序叠放，最后叠文字。后面的图层覆盖前面的图层。
- `text_layers` 可为空。文字不为空，`font_size` 为 8–512，坐标原点位于画布内；还需在执行前检查渲染后的文字边界。
- `medium=image` 时 `video=null`；`medium=video` 时必须有 VideoSpec。
- 视频时长大于 0 且不超过 60 秒，fps 为 1–120，frames 为 1–1441；`frames/fps` 与秒数差距不超过约一帧。实际视频模型限制另行满足。
- `acceptance` 至少一项；它是验收要求，不是已通过的结论。

附加图层只描述创意，不绑定某台机器。N 台 worker 由执行配置提供，所有前景使用配置的 `foreground` profile，按照角色和可用容量分配。不能为凑机器数擅自增加图层或内容。

保存后可运行：

```bash
comfy-director report plan.json --out plan.md
```

如果仓库或 CLI 不可用，仍可完成提示词与符合以上结构的计划，但要如实说明未做程序校验，不能为了校验而安装无关工具。
