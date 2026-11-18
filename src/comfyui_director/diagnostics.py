"""Check which configured ComfyUI workers can accept a bound workflow."""

import asyncio
import os
from dataclasses import dataclass

import httpx

from .comfy import ComfyClient, ComfyError
from .models import ClusterConfig, Worker


@dataclass(frozen=True)
class WorkerCheck:
    worker_id: str
    role: str
    ok: bool
    message: str


def create_worker_client(worker: Worker, max_artifact_mb: int) -> ComfyClient:
    api_key = os.environ.get(worker.api_key_env) if worker.api_key_env else None
    if worker.api_key_env and not api_key:
        raise ComfyError(f"Worker auth environment variable is not set: {worker.api_key_env}")
    try:
        return ComfyClient(worker.url, api_key=api_key, max_artifact_mb=max_artifact_mb)
    except (httpx.InvalidURL, ValueError):
        raise ComfyError("Worker URL is invalid") from None


async def probe_workers(
    config: ClusterConfig,
    clients: dict[str, ComfyClient],
    workflows: dict[str, dict],
    *,
    roles: set[str],
    unavailable: dict[str, str] | None = None,
) -> list[WorkerCheck]:
    """Probe each relevant worker once. Return diagnostics by worker and role."""
    unavailable = unavailable or {}

    async def check(worker: Worker, assigned_roles: list[str]) -> list[WorkerCheck]:
        client = clients.get(worker.id)
        if client is None:
            reason = unavailable.get(worker.id, "Worker client is unavailable")
            return [WorkerCheck(worker.id, role, False, reason) for role in assigned_roles]
        try:
            await client.health()
            info = await client.object_info()
        except ComfyError as exc:
            return [WorkerCheck(worker.id, role, False, str(exc)) for role in assigned_roles]
        results = []
        for role in assigned_roles:
            if role not in workflows:
                results.append(WorkerCheck(worker.id, role, False, "No valid workflow profile"))
                continue
            try:
                await client.validate_workflow(workflows[role], node_info=info)
            except ComfyError as exc:
                results.append(WorkerCheck(worker.id, role, False, str(exc)))
            else:
                results.append(WorkerCheck(worker.id, role, True, "Node and static input checks passed"))
        return results

    checks = await asyncio.gather(*(
        check(worker, assigned)
        for worker in config.workers
        if (assigned := sorted(roles.intersection(worker.roles)))
    ))
    results = [row for worker_checks in checks for row in worker_checks]
    observed = {row.role for row in results}
    results.extend(WorkerCheck("-", role, False, "No worker assigned to this role") for role in sorted(roles - observed))
    return results
