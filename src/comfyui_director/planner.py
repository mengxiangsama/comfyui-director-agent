"""Turn an actual language-model response into a validated, executable creative plan."""

import json
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from .models import CreativePlan
from .vendor.prompt_expander import PromptExpander


class PlannerError(RuntimeError):
    """A safe, user-facing planning error; provider bodies and credentials are never included."""


DIRECTOR_RULES = """你是一位负责 ComfyUI 多机创作的导演和提示词工程师。将用户中文需求变成一份详细、可执行的创作计划。
只返回符合给定 JSON Schema 的 JSON 对象，不要代码围栏，不生成可执行代码、网络地址、凭证或 ComfyUI 节点图。

硬约束：
1. 完整保留用户明确指定的主体、数量、动作、场景、颜色、风格、用途和逐字文案。缺失信息采用合理默认值并记入 assumptions，避免反复提问。request 必须逐字回填原始需求。
2. 将输出拆为完整成图提示词 full_image_prompt、背景 background、主前景 foreground、主前景合成 placement，以及用户要求视频时的 video。按内容独立性拆出可单独生成的人物、商品、装饰等 extra_layers；不是按机器数硬凑图层。extra_layers 每项包含唯一安全 id、prompt 和 placement，最多 31 个；id 不能使用 background/foreground/compose/video。主前景先合成，extra_layers 按数组顺序逐层叠加，越靠后越位于上层。每一项都写可独立复制使用的详细中文和英文正向提示词，英文必须表达与中文相同的内容。用具体可见描述，避免只堆叠“高清、大师级”。
3. visual 是全部任务共用的视觉约定，明确 style、palette、lighting（方向、色温、软硬、阴影方向）、camera（视角和透视）、composition（主体与留白位置），constraints 列出不可更改的要素。背景、主体和完整图必须共享这些约定。用户已有指定优先，参考模板里的固定配色比例、字数与数字参数禁令均不是硬约束。
4. 背景提示词必须明确：不生成待单独叠加的主体，为主体在指定区域留出空间；背景不绘制需要后期添加的文案、价格、Logo。可以有用户要求的场景元素，但不可重复主体。
5. 主体提示词必须描述主体造型、材质、姿态、镜头、共同布光与透视，并要求干净纯色背景以便抠图、完整轮廓、边缘清楚、无裁切。不承诺普通生图会自动输出透明 PNG 或遮罩；抠图/分割作为后续步骤说明。
6. placement 定义主体在最终画布上的像素 x/y/width/height，必须完全位于 canvas 内；instructions 写明 alpha/前景遮罩、缩放、位置、边缘羽化、接触阴影和色彩融合要求。尺寸不得被模糊描述代替。canvas 宽高均为 8 的倍数，默认 1024×1024，除非用户要求其他比例。
7. 用户给出的标题、中文文案、价格和品牌文字逐字写入 text_layers，单独后期字体排版，绝不翻译、改写、凭空新增。位置在画布内，font_size 为像素，color 为 #RRGGBB。没有明确文案时 text_layers=[]。不要依赖生成模型准确画字；最终排版步骤在视频生成后叠加，避免文字变形。
8. medium=image 时 video=null；用户要求视频时 medium=video，video 必须完整给出图生视频动作和镜头提示词、fps、frames、duration_seconds。图生视频以合成图为首帧，重点写动作、运动速度、运镜、时间顺序和保持不变的要素，勿要求互相冲突的运动。frames/fps 与 duration_seconds 的偏差不能超过一帧；默认 5 秒、16 fps、81 帧。不要将某模型不支持的音频/首尾帧能力当作已实现。
9. negative 写与该任务相关的排除项，仅作为下游工作流支持时使用的建议，不宣称所有模型都支持负向提示词。每个 acceptance 列表写具体可观察的验收条件。
10. 分工按能力池：background worker 池生成背景，foreground worker 池生成主前景与 extra_layers；这些独立任务在统一视觉约定下可并行。调度机等待背景和全部前景后抠图并按层顺序合成；video worker 池处理可选视频，文字最后排版。实际机器由集群配置的 roles/capacity 动态选择，不固定两台，不声称知道机器显卡、模型或速度。若给出可用机器数，仅作为拆分和并发参考；单机可排队，多机也无需人为增加素材层数。

下面的扩写参考只提供主体/构图/光照/材质维度的组织启发；以上用户约束和导演规则优先：
"""


def _endpoint(base_url: str) -> str:
    try:
        parsed = urlsplit(base_url)
        valid = (
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and not any(char.isspace() or ord(char) < 32 for char in base_url)
        )
        # Accessing port also checks malformed values; don't echo the URL in errors.
        _ = parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise PlannerError("模型地址必须是 http(s)，且不能包含内联凭证、查询参数或片段。")
    return base_url.rstrip("/") + "/chat/completions"


def _content(response: httpx.Response) -> str:
    if response.status_code >= 400:
        raise PlannerError(f"提示词模型请求失败（HTTP {response.status_code}）；请检查模型配置。")
    if response.is_redirect:
        raise PlannerError("提示词模型返回了重定向；请直接配置最终 API 地址。")
    try:
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip() or len(content) > 200_000:
            raise ValueError
        return content
    except (ValueError, TypeError, KeyError, IndexError):
        raise PlannerError("提示词模型未返回有效的文本消息，请检查模型是否支持聊天补全。") from None


def _parse_plan(content: str, request: str) -> CreativePlan:
    content = content.strip()
    # Some compatible providers wrap otherwise valid JSON in a single Markdown fence.
    if content.startswith("```") and content.endswith("```"):
        lines = content.splitlines()
        content = "\n".join(lines[1:-1])
    data: Any = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError("plan must be an object")
    # The input is the source of truth; a model may accidentally paraphrase it.
    data["request"] = request
    return CreativePlan.model_validate(data)


async def generate_plan(
    request: str,
    *,
    base_url: str,
    model: str,
    api_key: str | None = None,
    machines: int | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> CreativePlan:
    """Call an OpenAI-compatible model and validate its plan, with at most one repair.

    Provider and transport errors are deliberately reduced to safe messages. We never
    execute returned code or silently replace a failed model call with an example plan.
    """
    if not request.strip() or len(request) > 20_000:
        raise PlannerError("创作需求不能为空，且不能超过 20000 个字符。")
    if not model.strip():
        raise PlannerError("请配置提示词模型名称。")
    if machines is not None and (isinstance(machines, bool) or not isinstance(machines, int) or machines < 1):
        raise PlannerError("可用机器数必须是正整数。")
    endpoint = _endpoint(base_url)
    reference = PromptExpander().build_prompt(
        request,
        "prompt_expander",
        output_language="zh",
        output_format="structured",
    )["llm_input_prompt"]
    schema = json.dumps(CreativePlan.model_json_schema(), ensure_ascii=False)
    machine_context = (
        f"\n用户提供的可用机器数：{machines}。仅用于拆分参考，实际以集群角色与并发配置为准。\n"
        if machines is not None
        else "\n用户未提供机器数，按通用 worker 能力池分工，不假设固定机器数量。\n"
    )
    messages = [
        {
            "role": "system",
            "content": DIRECTOR_RULES + reference + machine_context + "\n\n输出 JSON Schema：\n" + schema,
        },
        {"role": "user", "content": request},
    ]
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(120.0, connect=15.0),
        transport=transport,
        follow_redirects=False,
        headers=headers,
    ) as client:
        for attempt in range(2):
            try:
                response = await client.post(
                    endpoint,
                    json={
                        "model": model,
                        "messages": messages,
                        "temperature": 0.5,
                        "max_tokens": 8192,
                        "response_format": {"type": "json_object"},
                    },
                )
            except httpx.TimeoutException:
                raise PlannerError("请求提示词模型超时，请稍后重试或检查模型服务。") from None
            except (httpx.HTTPError, httpx.InvalidURL):
                raise PlannerError("无法连接提示词模型，请检查 API 地址、网络和服务状态。") from None
            content = _content(response)
            try:
                return _parse_plan(content, request)
            except (ValueError, TypeError, ValidationError):
                if attempt:
                    raise PlannerError(
                        "模型连续两次返回无效计划；请更换模型或简化需求后重试。"
                        "已停止，未提交任何 ComfyUI 任务。"
                    ) from None
                messages.extend(
                    [
                        {"role": "assistant", "content": content},
                        {
                            "role": "user",
                            "content": (
                                "上次 JSON 未通过结构或布局校验。请对照系统中的完整 Schema 修复："
                                "返回单个 JSON 对象、补齐所有必填字段，主体位置不能超出画布，"
                                "图像模式 video=null，视频模式给出 video 且 frames/fps 与时长一致。"
                                "保持原始创作需求，勿增加 Schema 以外的字段。只返回修复后的 JSON。"
                            ),
                        },
                    ]
                )
    raise PlannerError("未获得创作计划。")  # Defensive; the bounded loop always returns or raises.
