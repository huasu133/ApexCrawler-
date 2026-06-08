"""
Pipeline checkpoint manager — 支持断点续爬。

设计思路：
- PipelineExecutor 每完成一个 stage 保存一次检查点
- 检查点包含：已完成 URL 列表、当前状态、上下文数据、时间戳
- 支持恢复：加载检查点跳过已完成的 stage
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _context_to_dict(ctx: Any) -> dict[str, Any]:
    """将 PipelineContext 转换为可 JSON 序列化的 dict。"""
    data: dict[str, Any] = {}
    if hasattr(ctx, "__dataclass_fields__"):
        for field_name in ctx.__dataclass_fields__:
            value = getattr(ctx, field_name)
            # 跳过不可序列化的字段
            if isinstance(value, type) or callable(value):
                continue
            # 将 dict / list / str / int / float / bool / None 直接序列化
            try:
                json.dumps(value)
                data[field_name] = value
            except (TypeError, ValueError):
                data[field_name] = str(value)
    else:
        # 通用 fallback：尝试获取所有公共属性
        for key in dir(ctx):
            if key.startswith("_"):
                continue
            value = getattr(ctx, key, None)
            if callable(value):
                continue
            try:
                json.dumps(value)
                data[key] = value
            except (TypeError, ValueError):
                data[key] = str(value)
    return data


def _dict_to_context(data: dict[str, Any], ctx_cls: type) -> Any:
    """将 dict 恢复为 PipelineContext。"""
    # 过滤出 dataclass 字段
    known_fields = set()
    if hasattr(ctx_cls, "__dataclass_fields__"):
        known_fields = set(ctx_cls.__dataclass_fields__.keys())
    kwargs = {k: v for k, v in data.items() if k in known_fields}
    return ctx_cls(**kwargs)


class CheckpointManager:
    """Pipeline 检查点管理器。

    将每个 stage 完成后的上下文序列化到 JSON 文件，
    支持恢复时跳过已完成的 stage。
    """

    def __init__(self, storage_dir: str = ".apex_checkpoints"):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        logger.info("CheckpointManager initialized: %s", self.storage_dir)

    def _checkpoint_path(self, job_id: str) -> Path:
        """返回 job_id 对应的检查点文件路径。"""
        return self.storage_dir / f"{job_id}.json"

    def _metadata_path(self) -> Path:
        """返回元数据文件路径，用于列出所有可恢复的任务。"""
        return self.storage_dir / "_metadata.json"

    def _update_metadata(self, job_id: str, entry: dict[str, Any]) -> None:
        """更新检查点元数据。"""
        meta_path = self._metadata_path()
        meta: dict[str, Any] = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                meta = {}
        meta[job_id] = entry
        meta_path.write_text(
            json.dumps(meta, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def save(self, trace_id: str, stage: str, context: dict[str, Any]) -> str:
        """保存检查点。

        Args:
            trace_id: 任务唯一标识。
            stage: 当前完成的 stage 名称。
            context: PipelineContext 序列化后的 dict。

        Returns:
            写入的检查点文件路径。
        """
        job_id = f"{trace_id}_{stage}"
        now = time.time()
        checkpoint: dict[str, Any] = {
            "trace_id": trace_id,
            "job_id": job_id,
            "stage": stage,
            "timestamp": now,
            "timestamp_iso": time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.localtime(now)
            ),
            "context": context,
        }
        path = self._checkpoint_path(job_id)
        path.write_text(
            json.dumps(checkpoint, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # 更新元数据
        self._update_metadata(trace_id, {
            "trace_id": trace_id,
            "stage": stage,
            "timestamp": now,
            "timestamp_iso": checkpoint["timestamp_iso"],
        })

        logger.debug("Checkpoint saved: %s (stage=%s)", path, stage)
        return str(path)

    def load(self, job_id: str) -> dict[str, Any] | None:
        """加载检查点。

        Args:
            job_id: 格式为 "{trace_id}_{stage}" 的作业 ID。

        Returns:
            检查点数据 dict，若文件不存在返回 None。
        """
        path = self._checkpoint_path(job_id)
        if not path.exists():
            logger.warning("Checkpoint not found: %s", path)
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            logger.info("Checkpoint loaded: %s", path)
            return data
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to load checkpoint {path}: {e}")
            return None

    def list_checkpoints(self) -> list[dict[str, Any]]:
        """列出所有可恢复的任务（基于元数据文件）。

        Returns:
            list[dict]，每个 dict 包含 trace_id、stage、timestamp 等。
            按 timestamp 降序排列（最新的在前）。
        """
        meta_path = self._metadata_path()
        if not meta_path.exists():
            return []

        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

        entries = list(meta.values())
        entries.sort(key=lambda e: e.get("timestamp", 0), reverse=True)
        return entries

    def clear(self, job_id: str | None = None) -> None:
        """清理检查点。

        Args:
            job_id: 若指定，只删除该任务的所有检查点；
                    若为 None，删除所有检查点。
        """
        if job_id is None:
            # 删除 storage_dir 下所有 .json 文件
            for f in self.storage_dir.glob("*.json"):
                f.unlink()
            logger.info("All checkpoints cleared in %s", self.storage_dir)
        else:
            # 删除以 job_id 开头的所有检查点文件和元数据条目
            meta_path = self._metadata_path()
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    meta.pop(job_id, None)
                    meta_path.write_text(
                        json.dumps(meta, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                except (json.JSONDecodeError, OSError):
                    pass

            for f in self.storage_dir.glob(f"{job_id}_*.json"):
                f.unlink()
            checkpoint_path = self._checkpoint_path(job_id)
            if checkpoint_path.exists():
                checkpoint_path.unlink()
            logger.info("Checkpoint cleared: %s", job_id)
