# ComfyUI Director Agent

<p align="center"><img src="assets/home-chat.png" alt="聊天对话：大佬大佬，怎么生成图片？用显卡显存。也不知道像我这样的穷大学生……有的兄弟，有的。" width="800"></p>

**用中文说想要什么，Agent 补成详细提示词，再把背景、主体、合成和视频安排清楚。**

例如：

> 雨夜赛博朋克城市里，一只橙色机甲猫走向镜头。做成竖版海报，标题是“夜行计划”。

Agent 会给出详细的中英文提示词、共同画风和光照约定、背景任务、各前景任务、放置位置以及独立文字层。你可以只拿提示词去出图，也可以连接 N 台 ComfyUI，把素材任务分配给可用机器，再在运行本工具的电脑上逐层合成。有视频需求时，先把无字合成图交给图生视频工作流，最后精确叠字。

第一版处理 **一个背景 + 一个主要前景 + 最多 31 个附加前景图层 + 本地合成 + 可选图生视频与文字后期**。一个图层也可以包含多个对象。机器池可以是一台、两台或更多台，不要求图层数等于机器数。它不包含集群管理界面，也不会把多台机器的显存加在一起。

## 先选使用方式

| 你想做什么 | 使用方式 | 需要什么 |
|---|---|---|
| 中文需求变成详细提示词 | 安装 Agent Skill，直接对 Codex 说需求 | 你正在使用的 Agent |
| 得到可执行的 JSON 计划 | 让 Agent 按计划格式输出，或用 `plan` 命令 | Agent；用 `plan` 时另需兼容 LLM API |
| 先看完整人工示例 | `demo` | Python；无需 ComfyUI、GPU 或 LLM API |
| N 台机器分工出图、最后合成 | `run` | 可访问的 ComfyUI 机器池、匹配的工作流和模型 |
| 合成图继续生成视频 | 带 `video` 的计划 + 视频工作流配置 | 支持该图生视频工作流的 ComfyUI |

Skill 可以由 Codex 本身理解需求并生成计划，**不需要为本项目单独配置另一个 LLM API**。命令行的 `plan` 是另一条入口，适合脚本调用，需要你自行配置兼容接口。

## 安装命令行工具

需要 Python 3.11 或更新版本。从项目根目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
comfy-director --help
```

仓库示例使用普通 RGB 前景，默认开启本地抠图，需要安装：

```bash
python -m pip install -e '.[cutout]'
```

`rembg` 首次使用需要下载它的抠图模型。抠图质量取决于画面，头发、透明物体和复杂边缘需要检查。自己的前景工作流若提供准确的透明前景或遮罩，可以使用 `alpha_or_mask` 模式，跳过抠图依赖。

## 安装 Agent Skill

将仓库中的技能文件夹复制到 Codex 的技能目录：

```bash
mkdir -p ~/.codex/skills
cp -R skills/comfyui-director ~/.codex/skills/
```

在新会话中使用：

```text
使用 $comfyui-director：雨夜赛博朋克城市里，一只橙色机甲猫走向镜头。
做成竖版海报，标题是“夜行计划”。先给详细提示词与分工，不执行出图。
```

也可以要求生成可执行计划：

```text
使用 $comfyui-director，把上面的需求保存为 plan.json。
背景和主体分别生成，按可用机器分配任务后再合成。标题原样保留，留到最后用字体排版。
按 CreativePlan 1.0 格式输出；缺失的非关键细节作合理假设并列出来。
```

只生成提示词时，无需启动 ComfyUI。技能默认允许自动发现，也可以用 `$comfyui-director` 显式调用。它能指导其他支持 Markdown 指令的 Agent；不同产品的安装目录以其实际配置为准。

## 先看无需 GPU 的计划示例

```bash
comfy-director demo --out-dir demo-output
comfy-director demo --medium video --out-dir demo-output
comfy-director demo --medium multi --out-dir demo-output
```

这些命令导出人工编写的 JSON 计划与对应 Markdown，方便查看提示词、任务分工和验收清单。**它们不会生成素材或合成图片，也不是 LLM 或 ComfyUI 生成结果。**

仓库附带三份人工编写的完整创作计划：

- [竖版海报计划](examples/poster-plan.md)：[JSON](examples/poster-plan.json)。
- [合成图转视频计划](examples/video-plan.md)：[JSON](examples/video-plan.json)。
- [三前景与 N 台机器分工计划](examples/multi-layer-plan.md)：[JSON](examples/multi-layer-plan.json)。

视频例子在需求中明确增加一个机械无人机，并用 `extra_layers` 独立生成和合成，演示多图层分工；海报例子保留单主体需求。

JSON 示例经过计划模型校验，不能据此推断真实 GPU 工作流已经运行通过。

## 从中文需求生成计划

如果通过 Agent 编写，按 [计划格式](docs/plan-format.md) 生成 `plan.json` 即可。

如果通过命令行 `plan`，在本机设置 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`，分别对应密钥、OpenAI-compatible 接口根地址和供应商实际支持的模型名。不要把密钥写入计划或提交到仓库。然后执行：

```bash
comfy-director plan '雨夜赛博朋克城市里橙色机甲猫，竖版海报，标题《夜行计划》' --out plan.json
comfy-director report plan.json --out plan.md
```

如果希望规划时考虑机器数量，可以加 `--machines`：

```bash
comfy-director plan '雨夜机甲猫与一架机械无人机，分别成层生成后合成海报' --machines 4 --out plan.json
```

这个参数是创作分工参考；实际可用机器、角色和容量来自执行配置。没有足够独立素材时，不需要为了用满机器而擅自增加对象。

`plan` 生成并保存 JSON 与同名 Markdown；`report` 可把已有合法计划重新转换成便于阅读的 Markdown。它们都不会启动 GPU 出图。`LLM_BASE_URL` 应包含供应商要求的路径前缀（例如 `/v1`），程序在末尾追加 `/chat/completions`；目标模型需支持聊天补全和 JSON 对象响应。可用以下命令查看本版本的完整计划 schema：

```bash
comfy-director schema
```

计划保留以下信息：

- 整张图的详细中英文提示词，用于单次出图参考。
- 共享视觉约定：风格、色板、光照、镜头、构图及不能改变的要求。
- 背景任务：环境细节、预留位置和验收要点。
- 前景任务：主要主体与可选附加图层各自的外形、姿态、角度、材质和验收要点。
- 合成参数：主体位置、宽高、遮罩羽化和独立文字层。
- 视频任务：合成后的运动描述、时长、帧率、帧数及稳定性要求。

详细提示词需要适配实际模型。画幅、生成尺寸、步数、采样器和模型文件由工作流与配置决定；在提示词里写“8K”不会自动输出 8K 图片。

## 连接 N 台 ComfyUI

在各机器上准备好 ComfyUI，并用各自的界面先运行对应工作流。模型、节点、VAE、LoRA 与显存必须满足该工作流要求。同一角色可以配置多个 worker，同一 worker 也可以承担多个角色；调度器按角色和容量分配任务，容量不足时排队。

复制配置示例：

```bash
cp cluster.example.json cluster.local.json
```

按 [执行配置说明](docs/execution.md) 修改：

1. 将 `workers` 的地址改成调度电脑实际可访问的 ComfyUI 地址。
2. 指定背景、前景各自的 API 格式 workflow、输入绑定和输出节点；附加前景使用同一前景 profile。
3. 示例 `foreground_cutout` 为 `rembg`，需要 cutout 依赖；自己的前景工作流提供透明 alpha 或独立 mask 时，可改为 `alpha_or_mask`。
4. 有中文文字时配置一款支持这些字形的 `font_file`；运行前会检查字体覆盖与文字边界。
5. 有视频时，再配置 `video` worker 和对应图生视频 profile。

示例中的模型文件名与工作流只是起点，需要按本机文件调整。项目不随包分发模型权重、中文字体或第三方自定义节点。

执行人工编写的示例计划：

```bash
comfy-director run examples/poster-plan.json --config cluster.local.json --run-dir runs/poster
```

背景、主要前景和附加前景会作为独立任务提交给机器池，可用容量允许时并行。素材完成后，运行本工具的电脑用 Pillow 缩放、定位、应用透明度或遮罩，按图层顺序合成并排版文字。合成不需要另外准备 GPU，运行工具的电脑也可以是其中一台出图机器。

如果你的方案是“背景生成后，再让另一台机器依据背景局部重绘主体”，它有前后依赖；第一版提供的是分别生成素材再合成的流程，不会自动完成局部重绘或补出接触阴影。

## 从合成图生成视频

```bash
comfy-director run examples/video-plan.json --config cluster.local.json --run-dir runs/video
```

视频任务必须使用你已经验证过的图生视频工作流。仓库提供 [Wan2.2 5B 图生视频 API 工作流](workflows/wan22-i2v.api.json) 作为适配起点，需要对应的 UNet、文本编码器与 VAE 文件，且实际显存和精度满足其要求。调度器会在合成之后提供无字输入图，视频提示词应描述主体运动、环境运动和镜头运动。帧数、帧率和时长还必须满足实际模型与工作流约束；该示例要求帧数为 `4n+1`、宽高为 32 的倍数，计划格式合法不等于所选视频模型支持任意配置。

示例采用 `81` 帧、`16 fps`、约 `5.0625` 秒。有文字层时，先生成干净视频，再用实际字体绘制文字并通过 ffmpeg 叠加到成片，避免视频模型改坏字形。运行前需要配置 `font_file`，并让 `ffmpeg` 和 `ffprobe` 位于 PATH；程序会先检查这些条件。安装方式按你自己的操作系统和环境选择。

## 查看结果与已知边界

每次运行使用独立目录，保存计划与任务结果。具体文件以命令返回为准。不要只根据输出目录里的“最新图片”判断这次任务的结果。

正常图片结果为运行目录中的 `final.png`，`scene.png` 是无字合成底图；有视频时另有 `final.mp4` 或对应视频格式。同一个运行目录用于恢复同一计划时，会依据已记录的任务 ID 处理已有任务；改变计划或配置时使用新目录。网络超时后应先核对 ComfyUI 队列与历史，避免重复提交。

需要检查的实际画面包括：主体完整性、抠图边缘、大小和落点、光照与透视一致性、标题字形、视频中的形变。计划中的 `acceptance` 是验收清单，不代表程序已经自动完成视觉评分或修复。

更多机器可以处理更多独立素材，但传输、模型加载、抠图、合成和视频生成都有开销；只有两个生成任务时，加入更多机器也不会自动加速它们。第一版没有自动故障转移、跨机器共享显存、自动重试换模型或无限生成变体。

`capacity` 只限制同一次 `run` 内的并发任务；同时启动多个运行目录时，没有跨进程的全局槽位配额。ComfyUI worker 请求默认直连配置地址，不继承系统 HTTP/SOCKS 代理。恢复时仍会检查相关 worker 是否可用；它不是一个离线素材管理器。

当前交付状态：提供可安装 skill、计划结构、示例、命令行与可配置的执行链路。真实 ComfyUI、GPU 和视频工作流的联调情况以 [验证记录](docs/validation.md) 为准。

## 开发与许可

```bash
python -m pip install -e '.[dev]'
python -m pytest
```

项目自有部分使用 [MIT License](LICENSE)。第三方代码或资源的许可与来源说明保留在仓库相应文件中；模型、字体和节点分别遵循其自身许可。
