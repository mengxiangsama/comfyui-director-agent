# 创作计划格式

计划使用 `CreativePlan 1.0`。它描述画面和任务，不携带 API 密钥、服务器地址、任意代码或工作流内容。运行环境另放执行配置。权威字段定义为 `src/comfyui_director/models.py`，可运行 `comfy-director schema` 获取当前 JSON Schema。

## 把一句话补成可见画面

先保留用户明确提出的主体、数量、风格、画幅、用途和文字。补充细节时，应写观众能看到的形状、材质、姿态和环境关系，例如“橙色金属外壳沿边缘留下擦痕，右后方青色灯光勾出轮廓”，比“高级、震撼”更可执行。

分层前先约定共享视觉规则。独立生成的背景和主体应当使用一致的视线高度、透视、光源方向、色温、风格和细节密度。相同 seed 不构成跨模型一致性的保证。

| 字段 | 作用与要求 |
|---|---|
| `schema_version` | 固定为字符串 `"1.0"`。 |
| `title` / `request` | 计划名与原始中文需求。 |
| `medium` | `image` 或 `video`。 |
| `canvas` | `width`、`height` 均为 64–8192 的 8 的倍数，单位像素。 |
| `visual` | `style`、`palette`、`lighting`、`camera`、`composition` 为非空文字；`constraints` 是硬要求列表。 |
| `full_image_prompt` | 整张图的出图参考提示词。 |
| `background` | 只生成环境，明确主体和文字预留区。 |
| `foreground` | 生成主体，明确完整外轮廓、姿态和共享视觉条件。 |
| `placement` | 主体的放置矩形、羽化值、合成说明与验收清单。 |
| `extra_layers` | 最多 31 个附加前景，每项有唯一 `id`、`prompt`、`placement`。 |
| `text_layers` | 精确文案与位置；空列表表示不叠字。 |
| `video` | `medium=video` 时必须提供；`image` 时必须为 `null`。 |
| `assumptions` | 对用户未指定内容作出的合理假设。 |

## 提示词对象

`full_image_prompt`、`background`、`foreground` 都使用同一个格式：

```json
{
  "positive_zh": "详细中文画面描述",
  "positive_en": "A detailed English description of the visible scene.",
  "negative": "与目标模型兼容的负向条件，不需要时为空字符串",
  "acceptance": ["可检查的画面要求"]
}
```

中文与英文应表达同一幅画面，不能在翻译时增减主体或改动文案。不要把某种模型的权重语法、固定负向词或“质量咒语”当作所有模型通用规则。

`full_image_prompt` 用于完整构图参考；分层运行使用 `background`、`foreground` 和每个 `extra_layers[].prompt`。如果有标题，提示词说明留白，文字通过 `text_layers` 排版，避免各图层重复生成标题。

## 主体位置与遮罩

```json
{
  "x": 144,
  "y": 440,
  "width": 480,
  "height": 640,
  "feather_px": 1,
  "instructions": "将完整主体放在预留区域，脚部落在湿路面上。",
  "acceptance": ["耳朵与尾巴完整，标题区未被遮挡"]
}
```

坐标原点为画布左上角，`x` 向右、`y` 向下。矩形须完整落在画布内；羽化范围为 0–32 像素。它是前景图层的目标范围，生成主体时应尽量减少四周无关留白，避免缩放后主体过小。

前景必须有实际透明度或独立遮罩。提示词中写“透明背景”不能保证输出 RGBA。普通 RGB 图片需要抠图或由工作流输出 mask；执行器不能凭文字描述推断哪个区域应透明。合成时先按 alpha 范围裁切透明留白，再保持比例缩放到目标框，水平居中且底部对齐；超出实际 alpha 的有色背景不会因此自动消失。

`instructions` 供人或 Agent 理解，不能代替数值参数或工作流。第一版合成器不会因为这段文字自动重打光、生成倒影、添加接触阴影或改变姿势。需要这些效果时，将可生成部分设计进素材，或追加明确的后期流程。

## 多个前景图层

有独立素材需求时，把主要主体保留在 `foreground`/`placement`，其余写入 `extra_layers`。每个附加图层结构如下：

```json
{
  "id": "scout_drone",
  "prompt": {
    "positive_zh": "一架青色侦察无人机，三分之四侧面，完整螺旋桨轮廓，简单对比背景。",
    "positive_en": "One cyan scout drone in three-quarter view, complete rotor silhouette, on a simple contrasting background.",
    "negative": "cropped rotor, text",
    "acceptance": ["完整轮廓，只有一架无人机"]
  },
  "placement": {
    "x": 520,
    "y": 280,
    "width": 160,
    "height": 160,
    "feather_px": 0.5,
    "instructions": "放在主体右上方，不遮标题。",
    "acceptance": ["无人机处于指定区域"]
  }
}
```

`id` 必须以英文字母开头，最多 48 位，后续只用字母、数字、下划线或连字符，且不能重复或使用 `background`、`foreground`、`compose`、`video`。图层依次叠放：背景→主要前景→按数组顺序的附加前景→文字；后面的图层覆盖前面的图层。

同一个 `foreground` profile 处理所有前景任务。机器数量独立于图层数量：N 个 worker 按其角色和容量承接任务，不需要一层绑定一台。不能为用满机器擅自增加用户未要求的对象。

## 文字层

```json
{
  "text": "夜行计划",
  "x": 104,
  "y": 96,
  "font_size": 80,
  "color": "#F7E6C4"
}
```

`text` 原样保存用户要求的文字，不能转成英文或擅自改名。`x`、`y` 表示左上角放置位置，`font_size` 为像素字号。输入换行需使用 JSON 的 `\n`。字体文件由执行配置的 `font_file` 提供；需要覆盖全部字形，且文字渲染后不得超出画布。只有坐标原点在画布内，仍不足以保证整段文字放得下。

## 视频任务

```json
{
  "positive_zh": "以合成图为首帧，猫缓慢抬头，雨线持续下落，镜头轻微推进。",
  "positive_en": "Use the composited image as the starting frame. The cat slowly raises its head while rain falls. The camera gently pushes forward.",
  "negative": "identity drift, deformed limbs, sudden scene change",
  "duration_seconds": 5.0625,
  "fps": 16,
  "frames": 81,
  "acceptance": ["主体外观稳定，镜头运动连续"]
}
```

视频提示词关注在既有合成图上发生的运动，不再重新发明主体外观与构图。优先说明主体运动、环境运动、镜头运动、节奏和必须稳定的部分。

格式校验要求 `frames/fps` 与 `duration_seconds` 相差不超过约一帧。实际模型可能另有 `4n+1` 帧数、固定尺寸或最大时长限制，需要执行配置与工作流共同满足。

视频使用无字合成图作为输入。存在文字层时，实际字体绘制的文字会在视频生成后通过 ffmpeg 叠加；执行前需要检查字体、ffmpeg 和 ffprobe。

## 验证与阅读计划

```bash
comfy-director report examples/poster-plan.json --out poster-review.md
```

`report` 会先读取和校验计划，再生成 Markdown。字段合法只证明计划结构正确；要确认创意、画面质量和实际工作流兼容性，仍需查看报告和真实素材。
