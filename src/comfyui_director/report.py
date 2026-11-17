"""Readable production handoff from the same validated plan used by the runner."""

import re

from .models import CreativePlan, PromptSpec


def _block(text: str) -> str:
    longest = max((len(m.group()) for m in re.finditer(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}text\n{text}\n{fence}"


def _table(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _checks(items: list[str]) -> list[str]:
    return [f"- [ ] {item}" for item in items]


def _prompt(title: str, spec: PromptSpec) -> list[str]:
    lines = [
        f"## {title}",
        "",
        "### 中文正向提示词",
        "",
        _block(spec.positive_zh),
        "",
        "### English positive prompt",
        "",
        _block(spec.positive_en),
        "",
        "### 负向提示词",
        "",
        _block(spec.negative) if spec.negative else "未单独指定；按目标模型和工作流决定。",
        "",
        "负向提示词仅在下游模型/工作流支持时使用。",
        "",
        "### 验收条件",
        "",
        *_checks(spec.acceptance),
        "",
    ]
    return lines


def render_markdown(plan: CreativePlan) -> str:
    """Render all prompts, the DAG, assignment, layout and acceptance criteria in Chinese."""
    v = plan.visual
    p = plan.placement
    lines = [
        f"# {plan.title}",
        "",
        "**状态：这是创作计划，尚未执行或验收任何 ComfyUI 任务。**",
        "",
        "## 原始需求",
        "",
        _block(plan.request),
        "",
        "## 成片规格与统一视觉约定",
        "",
        f"- 类型：{'视频（先生成合成首帧，再图生视频）' if plan.medium == 'video' else '图片'}",
        f"- 最终画布：{plan.canvas.width} × {plan.canvas.height} 像素",
        f"- 风格：{v.style}",
        f"- 色彩：{v.palette}",
        f"- 共同光照：{v.lighting}",
        f"- 共同镜头与透视：{v.camera}",
        f"- 构图与留白：{v.composition}",
        "",
        "必须共同遵守的约束：",
        "",
        *([f"- {item}" for item in v.constraints] or ["- 以原始需求和上述统一视觉约定为准。"]),
        "",
        "## 机器分工与输入输出",
        "",
        "按 background/foreground/video 能力池分工；实际机器数量、地址、模型和并发数来自集群配置，不固定两台。",
        "",
        "| 阶段 | 负责人 | 输入 | 输出 | 依赖 |",
        "| --- | --- | --- | --- | --- |",
        "| 背景生成 | 背景 worker 池 / background | 背景提示词、统一视觉约定、画布 | background.png | 无，可与各前景任务并行 |",
        "| 主前景生成 | 前景 worker 池 / foreground | 主体提示词、统一视觉约定 | foreground.png；已有 alpha/遮罩则一并交付 | 无，可与背景及其他前景并行 |",
        "| 抠图与合成 | 调度机 / compositor | 背景、主体、alpha/前景遮罩、placement | composite.png | 等待背景与全部前景完成 |",
    ]
    for layer in plan.extra_layers:
        lines.append(
            f"| 额外前景 `{layer.id}` | 前景 worker 池 / foreground | 该层提示词、统一视觉约定 | {layer.id}.png 与对应 alpha/遮罩 | 无，可与背景及其他前景并行 |"
        )
    if plan.video:
        lines.append(
            "| 图生视频 | 视频 worker 池 / video | 无文案的合成首帧、视频提示词与帧率/帧数 | video.mp4 | 合成完成，并配置视频工作流 |"
        )
    if plan.text_layers:
        lines.append(
            "| 精确文字排版 | 后期排版 / typography | 下表逐字文案、字体与位置 | 最终带字图片/视频（视频由 ffmpeg 后期叠加） | 在视觉生成完成后 |"
        )
    lines += [
        "",
        "执行顺序：背景与各前景按可用 worker 并行（单机排队） → 等待全部完成 → 按层顺序抠图/合成"
        + (" → 图生视频" if plan.video else "")
        + (" → 精确文字排版" if plan.text_layers else "")
        + "。",
        "",
        "普通生图未必带透明背景；必须提供有效 alpha/前景遮罩，或为调度机配置抠图能力。",
        "多台机器的显存不自动合并；存在前后依赖的阶段必须等待输入就绪。",
        "",
        *_prompt("完整成图提示词（统一构图参考，也可用于单机生成）", plan.full_image_prompt),
        *_prompt("背景 worker：背景生成", plan.background),
        *_prompt("前景 worker：主前景生成", plan.foreground),
        "## 调度机：抠图与合成指令",
        "",
        f"- 画布：{plan.canvas.width} × {plan.canvas.height} 像素，左上角为原点。",
        f"- 主体目标位置：x={p.x}，y={p.y}。",
        f"- 主体目标框：width={p.width}，height={p.height} 像素。",
        f"- 边缘羽化：{p.feather_px:g} 像素。",
        "- 遮罩约定：白色/1 表示保留前景，黑色/0 表示背景；遮罩必须与前景源图对应。",
        "- 缩放时保持主体比例；目标框只定义放置区域，不应通过拉伸改变主体形态。",
        "",
        "执行器负责缩放、alpha/遮罩合成和羽化。下面的阴影/光照要求需由素材生成、指定工作流或后续人工调整实现，普通像素合成不会自动重新布光。",
        "",
        _block(p.instructions),
        "",
        "### 合成验收",
        "",
        *_checks(p.acceptance),
        "",
    ]
    if plan.extra_layers:
        lines += [
            "## 额外前景任务与叠放顺序",
            "",
            "先放置主前景，再按下面的顺序叠加额外前景；越靠后越位于画面上层。",
            "",
        ]
        for index, layer in enumerate(plan.extra_layers, 1):
            lp = layer.placement
            lines += _prompt(f"额外前景 {index}：{layer.id}", layer.prompt)
            lines += [
                f"- 目标位置：x={lp.x}，y={lp.y}；目标框：{lp.width} × {lp.height} 像素。",
                f"- 边缘羽化：{lp.feather_px:g} 像素；使用本层前景对应的 alpha/遮罩。",
                "",
                _block(lp.instructions),
                "",
                *_checks(lp.acceptance),
                "",
            ]
    if plan.text_layers:
        lines += [
            "## 精确文字排版清单",
            "",
            "文案必须逐字保留，使用实际字体绘制，不交给扩散模型猜字。坐标单位为像素。",
            "",
            "| 原样文案 | x | y | 字号 | 颜色 |",
            "| --- | --- | --- | --- | --- |",
        ]
        lines.extend(
            f"| {_table(t.text)} | {t.x} | {t.y} | {t.font_size} | {_table(t.color)} |"
            for t in plan.text_layers
        )
        for index, layer in enumerate(plan.text_layers, 1):
            lines += ["", f"逐字文案 {index}（保留原始换行）：", "", _block(layer.text)]
        lines += [
            "",
            "- [ ] 使用覆盖所需中文字符的字体，逐字核对文案。",
            "- [ ] 检查文字实际宽高、换行和边缘，不能只检查起点坐标。",
            "- [ ] 视频中的精确文字应在视频生成完成后叠加，避免字形随画面运动变形。",
            "",
        ]
    if plan.video:
        video = plan.video
        lines += [
            "## 视频 worker：图生视频",
            "",
            "输入：上一阶段无文案的合成图片作为首帧。需要为可用 worker 配置 video 角色和对应工作流。",
            "",
            f"- 目标时长：{video.duration_seconds:g} 秒",
            f"- 输出帧率：{video.fps} fps",
            f"- 生成帧数：{video.frames}",
            f"- 按帧数计算时长：{video.frames / video.fps:.3f} 秒（与目标在一帧误差内）",
            "",
            "### 中文动作与运镜提示词",
            "",
            _block(video.positive_zh),
            "",
            "### English motion and camera prompt",
            "",
            _block(video.positive_en),
            "",
            "### 视频负向提示词",
            "",
            _block(video.negative) if video.negative else "未单独指定；按视频模型和工作流决定。",
            "",
            "### 视频验收",
            "",
            *_checks(video.acceptance),
            "",
        ]
    lines += [
        "## 假设与待实际验证项",
        "",
        *([f"- {item}" for item in plan.assumptions] or ["- 未额外添加创作假设。"]),
        "- 提示词可用性、模型效果、素材融合与真实耗时，需在实际 ComfyUI 工作流中验证。",
        "- 本报告不代表已生成图片/视频，也不代表已通过上述验收。",
        "",
    ]
    return "\n".join(lines)
