"""Command line interface. Planning and GPU execution are separate operations."""

import argparse
import asyncio
import importlib.resources
import json
import os
import sys
from pathlib import Path

from pydantic import ValidationError

from .comfy import ComfyError
from .models import ClusterConfig, CreativePlan
from .planner import PlannerError, generate_plan
from .report import render_markdown
from .runner import RunError, Runner


def _write_plan(plan: CreativePlan, path: Path):
    if path.suffix.lower() != ".json":
        raise ValueError("Plan output must use a .json extension; Markdown is written beside it")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    path.with_suffix(".md").write_text(render_markdown(plan), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="中文需求 → 详细提示词 → N 机器分工 → 最终合成")
    commands = parser.add_subparsers(dest="command", required=True)
    plan_cmd = commands.add_parser("plan", help="使用语言模型生成可校验的详细方案，不启动 GPU")
    plan_cmd.add_argument("request", nargs="?", help="中文生成需求")
    plan_cmd.add_argument("--request-file", type=Path)
    plan_cmd.add_argument("--out", type=Path, default=Path("plan.json"))
    plan_cmd.add_argument("--machines", type=int, help="规划参考机器数，实际由配置和空闲容量分配")
    plan_cmd.add_argument("--base-url", default=os.environ.get("LLM_BASE_URL"))
    plan_cmd.add_argument("--model", default=os.environ.get("LLM_MODEL"))
    report_cmd = commands.add_parser("report", help="将方案转换为中文详细提示词和分工说明")
    report_cmd.add_argument("plan", type=Path)
    report_cmd.add_argument("--out", type=Path)
    run_cmd = commands.add_parser("run", help="执行方案；同一个 run-dir 可恢复原 prompt_id")
    run_cmd.add_argument("plan", type=Path)
    run_cmd.add_argument("--config", type=Path, required=True)
    run_cmd.add_argument("--run-dir", type=Path, required=True)
    demo_cmd = commands.add_parser("demo", help="导出人工编写的完整示例，不调用 LLM 或 GPU")
    demo_cmd.add_argument("--out-dir", type=Path, default=Path("demo-output"))
    demo_cmd.add_argument("--medium", choices=["image", "video", "multi"], default="image")
    commands.add_parser("schema", help="打印 CreativePlan JSON Schema")
    args = parser.parse_args(argv)
    try:
        if args.command == "schema":
            print(json.dumps(CreativePlan.model_json_schema(), ensure_ascii=False, indent=2))
        elif args.command == "plan":
            if bool(args.request) == bool(args.request_file):
                raise ValueError("Provide exactly one of request or --request-file")
            if not args.base_url or not args.model:
                raise ValueError("Set LLM_BASE_URL and LLM_MODEL, or use --base-url and --model")
            request = args.request_file.read_text(encoding="utf-8") if args.request_file else args.request
            if args.machines is not None and args.machines < 1:
                raise ValueError("--machines must be positive")
            plan = asyncio.run(generate_plan(
                request, base_url=args.base_url, model=args.model,
                api_key=os.environ.get("LLM_API_KEY"), machines=args.machines,
            ))
            _write_plan(plan, args.out)
            print(f"方案已保存：{args.out}；详细提示词：{args.out.with_suffix('.md')}（未执行生成）")
        elif args.command == "demo":
            filename = {"image": "poster-plan.json", "video": "video-plan.json", "multi": "multi-layer-plan.json"}[args.medium]
            resource = importlib.resources.files("comfyui_director.data").joinpath(filename)
            plan = CreativePlan.model_validate_json(resource.read_text(encoding="utf-8"))
            destination = args.out_dir / filename
            _write_plan(plan, destination)
            print(f"人工示例已导出：{destination}（未调用 LLM/GPU）")
        elif args.command == "report":
            plan = CreativePlan.model_validate_json(args.plan.read_text(encoding="utf-8"))
            report = render_markdown(plan)
            if args.out:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(report, encoding="utf-8")
            else:
                print(report)
        elif args.command == "run":
            plan = CreativePlan.model_validate_json(args.plan.read_text(encoding="utf-8"))
            config = ClusterConfig.model_validate_json(args.config.read_text(encoding="utf-8"))
            result = asyncio.run(Runner(config, args.config.resolve().parent).run(plan, args.run_dir))
            print(f"成品图片：{args.run_dir.resolve() / result['final_image']}")
            if result.get("final_video"):
                print(f"成品视频：{args.run_dir.resolve() / result['final_video']}")
        return 0
    except ValidationError as exc:
        # Do not echo complete provider responses or potentially sensitive input values.
        print(f"方案/配置校验失败：{exc.error_count()} 处，请对照 schema 检查字段", file=sys.stderr)
        return 2
    except (PlannerError, RunError, ComfyError, OSError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
