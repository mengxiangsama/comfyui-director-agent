# 执行配置与工作流适配

执行配置与创作计划分开：同一份计划可以交给不同机器和模型。配置示例见 [cluster.example.json](../cluster.example.json)。profile 的工作流文件相对配置文件所在目录解析；字体同样应按配置使用正确路径，首次适配可使用绝对路径。先用独立运行目录试跑。

## N 台机器和调度电脑

```text
中文需求 → Agent 详细计划
                  ├─ 背景任务 ──────────┐
                  ├─ 主要前景任务 ──────┤ → 按角色/容量分发给 N 台 ComfyUI
                  └─ 各附加前景任务 ────┘                 ↓
                                             调度电脑逐层抠图/遮罩/合成
                                                       ↓
                              图片：字体排版 ← 无字合成图 → 视频工作流
                                                               ↓
                                                      视频生成后字体叠加
```

调度电脑运行 `comfy-director`，可以是机器池中的一台。背景、主要前景与附加前景独立调度，可用容量允许时并行；合成必须等待所需素材，视频必须等待无字合成图。给同一台单容量 worker 分配多个角色时，任务需要排队，不会凭配置增加 GPU 能力。

## `workers`

每个 worker 有以下字段：

| 字段 | 含义 |
|---|---|
| `id` | 1–64 位的英文字母、数字、下划线或连字符；不同 worker 不能重名。 |
| `url` | 调度电脑能访问的 ComfyUI HTTP(S) 根地址。 |
| `roles` | 允许执行的角色：`background`、`foreground`、`video`。 |
| `capacity` | 该 worker 可同时接受的本工具任务数量，默认 1。 |
| `api_key_env` | 可选，保存访问密钥的本机环境变量名；不是密钥内容。 |

例如把内网 `192.168.1.101` 作为背景机器，`192.168.1.102`、`192.168.1.103` 同时承担前景角色。继续向 `workers` 数组追加机器即可扩展机器池；同一机器也可以承担多种角色。这里的地址仅用于说明，应替换成真实地址。不要把密钥塞进 URL；配置校验会拒绝带用户名、密码、查询串或 fragment 的 worker URL。

`capacity` 是本工具的调度额度，不是对 ComfyUI 或显卡的硬性资源隔离。多个独立调度进程、其他网页提交的任务以及 ComfyUI 自身队列，仍需要按实际负载考虑。

## `profiles`

每个角色关联一个 profile，所有主要及附加前景使用 `foreground` profile。profile 使用以下字段：

| 字段 | 含义 |
|---|---|
| `file` | 已验证的 ComfyUI API 格式 workflow JSON 文件。 |
| `bindings` | 语义输入名到 `[节点 ID, input 名]` 的映射。 |
| `outputs` | 输出类型到节点、历史字段和序号的选择。 |
| `defaults` | 工作流需要的可配置默认参数，例如实际 checkpoint 名。 |

先在目标 ComfyUI 中配置模型与节点，手动跑通，再导出 **API 格式** JSON。普通可视化编辑器 workflow JSON 与 API 格式不同。节点 ID 用 JSON 字符串保存，input 名必须和目标节点一致。

绑定的基本形式如下，具体支持的语义名以仓库示例和当前运行器为准：

```json
{
  "bindings": {
    "prompt": ["6", "text"],
    "negative": ["7", "text"],
    "seed": ["3", "seed"]
  },
  "outputs": {
    "image": {"node": "9", "field": "images", "index": 0}
  }
}
```

API `history` 的输出字段由实际节点决定。图片通常是 `images`，视频节点可能返回 `images`、`gifs` 或 `videos`；仓库 Wan2.2 工作流的原生 `SaveVideo` 使用 `images`。不要只根据文件扩展名猜测输出字段。`OutputSelector.field` 支持的 schema 值还包括 `audio`，这不意味着本版提供音频生成产品流程。

工作流不会由 LLM 任意构造后直接执行；它来自你的本地配置。每个工作流涉及的 checkpoint、VAE、LoRA、分割模型、放大器和自定义节点，都应在承担对应角色的 worker 上存在并兼容。

## 透明前景和本地合成

配置模型的默认 `foreground_cutout` 为 `alpha_or_mask`，仓库示例为方便接普通文生图工作流，明确设为 `rembg`。使用 `alpha_or_mask` 时，前景需要是真实带透明像素的图片，或由前景 profile 指定一个单独的 mask 输出。白色画布的 RGB PNG 不等于透明 PNG；图片带 alpha 通道但全部不透明，也不能代替抠图。

mask 应当与前景图对齐，通常白色表示保留主体，黑色表示去掉背景。接 ComfyUI 节点时应检查它的实际遮罩约定；若节点将 alpha 转成反向 mask，需要在工作流中反转后再输出。

如果使用普通文生图工作流输出主体，可把 `foreground_cutout` 改为 `rembg`，并安装：

```bash
python -m pip install -e '.[cutout]'
```

首次运行会按 rembg 的机制下载分割模型。它不改变原有主体姿态，无法保证复杂边缘完美，也不会把背景里的环境光、阴影和反射自动迁移到新背景。

合成器按 alpha 范围裁切透明留白，保持主体比例缩放到 `placement` 框内，水平居中、底部对齐，并可应用 `feather_px`。只有经过实际抠图或遮罩，有色背景才会移除。附加前景按 `extra_layers` 数组顺序叠放，后面的图层覆盖前面的图层。

## 中文字体

有 `text_layers` 时，设置 `font_file` 为你有权使用且覆盖目标文字的字体文件路径。安装字体和字体许可由使用者自行准备；仓库不打包字体。执行前会检查字体覆盖和文字边界；实际字形仍应由你查看确认。

示例海报的标题为“夜行计划”，由字体精确绘制，不交给图片模型临摹。改变标题后要重新检查字号、位置和画布边界。

## 视频 profile

视频 worker 可以和已有 worker 共用，也可以单独指定。视频 profile 必须使用图生视频 workflow，并把上传后的无字合成图文件名绑定到加载图片的节点；同时把视频正向提示词、帧数和其他可调参数绑定到对应节点。

仓库示例选用 `workflows/wan22-i2v.api.json`，使用 `wan2.2_ti2v_5B_fp16.safetensors`、`umt5_xxl_fp8_e4m3fn_scaled.safetensors`、`wan2.2_vae.safetensors`。需要你按 ComfyUI 的实际模型目录准备这些文件；本项目不会自动下载这些权重。不同视频模型的节点和输入差异很大，替换时按已跑通的工作流配置绑定。视频的帧数、fps、尺寸、模型精度和显存需求，由实际工作流决定；该 Wan2.2 示例要求 `4n+1` 帧和 32 倍数的宽高。没有配置 video profile 时，只能运行图片计划。

存在文字层时，图生视频使用无字合成图，完成后由 Pillow 绘制文字层，再通过 ffmpeg 叠加到成片。调度电脑需要 `ffmpeg`、`ffprobe`，且 ffmpeg 需要支持本工具使用的视频编码器。程序会在提交 GPU 任务前检查所需工具。字体排版后仍应复查视频的字形、缩放和颜色。

## 输出、超时和复查

每次执行都指定一个专用目录：

```bash
comfy-director run examples/poster-plan.json --config cluster.local.json --run-dir runs/poster-001
```

配置可以调整 `poll_interval_seconds`、`task_timeout_seconds` 和 `max_artifact_mb`。检查命令返回的任务记录与本次文件，区分“已提交”“执行完成”“素材下载完成”和“最终合成完成”。

网络超时不一定表示 ComfyUI 已取消任务。失败后先核对其队列和历史，再决定是否手动重跑，避免重复出图。程序只对已经拿到的素材执行后续步骤；不应根据上次运行的文件替代本次结果。

画面验收仍由你或 Agent 完成。`acceptance` 是验收清单。要生成新主体或改变视频动作时，编辑计划并使用新运行目录。

## 诊断节点与依赖

```bash
comfy-director doctor --config cluster.local.json --plan examples/poster-plan.json
```

工作流检查使用各机器的 `/system_stats` 和 `/object_info`，报告节点缺失、必填输入缺失和静态选项不匹配。安装在该机器上的 checkpoint、UNet、CLIP、VAE 和 LoRA 等模型名通常由节点选项提供。工作流的自定义动态选项及运行时行为由 ComfyUI 实际提交时验证。

指定计划时只检查所需角色，并检查计划涉及的本机排版和视频工具。省略计划时检查配置涉及的角色。每个所需角色至少有一个合格节点，且本地检查无错误时，`doctor` 返回退出码 0。某台机器缺项时仍会逐台报告，其他合格机器可用。

## 重试已失败的任务

```bash
comfy-director run examples/poster-plan.json --config cluster.local.json \
  --run-dir runs/poster-001 --retry foreground
```

`--retry` 接受运行记录中的任务名，如 `background`、`foreground`、附加层 `id` 或 `video`。它适用于服务器明确拒绝提交，或历史明确显示执行失败／中断的任务，重新使用该任务的输入和 seed。重复指定 `--retry` 可重试多个失败任务。保持原计划与配置，先修好节点端依赖或恢复可用机器。提交结果未知时先在原节点查队列；已知 prompt ID 的轮询超时可用同一目录直接续查。

## 离线复用素材

```bash
comfy-director compose adjusted-plan.json --run-dir runs/poster-001 \
  --font-file /path/to/font.ttc --out ./poster-adjusted.png
```

从原运行下载的背景、主前景和其他前景可直接重新合成。保留图层 ID，可改位置、尺寸、顺序和文字。图片输出放到原运行目录之外，计划如果使用普通 RGB 前景，传 `--cutout rembg` 应用本地分割模型。有 alpha 或遮罩时，用默认值即可。修改生成提示词后要重画素材时，请使用 `run` 启动新任务。
