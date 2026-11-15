"""Privacy-safe deletion of all generated BiliLearn user data.

This module deliberately has no dependency on the CLI or the Web panel so both
entry points remove exactly the same private data and generated artifacts.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Iterable

from .user_data import USER_DATA_DIR, PROJECT_DIR, _has_link_component, _is_link, resolve_output_path


_USER_ROOT_FILES = (
    "bot_memory.json", "bot_journal.md", "knowledge_metadata.json",
    "learning_log.md", ".cipher_key", "data.json", "web_panel_stdout.log",
    ".legacy_migration_v1_done", ".project_layout_v2_done",
    ".local_appdata_migration_v1_done",
)

# The web panel and CLI expose only these fixed group ids. This prevents a
# malformed request from ever expanding a reset into an arbitrary drive path.
RESET_GROUPS = {
    "credentials_runtime": {
        "label": "登录凭证与运行数据",
        "description": "Cookie、二维码、API 配置、网页密码、会话、日志和互动记录",
    },
    "knowledge_generated": {
        "label": "知识库与生成内容",
        "description": "知识库、HTML、思维导图、Word、亮点归档和自定义导出目录",
    },
    "local_models": {
        "label": "本地 ASR 模型",
        "description": "当前数据根的 models 及根内配置的 ASR 模型目录，之后需要重新下载",
    },
    "project_docs": {
        "label": "用户 docs 内容",
        "description": "仅当前数据根的 docs 目录，程序包内文档不会删除",
    },
    "backup_files": {
        "label": "配置备份（默认保留）",
        "description": "手动导出的配置备份。仅在明确勾选后删除，便于恢复出厂后重新导入",
    },
}

# A factory reset should remove live private data, but leave an explicitly
# created backup available for a later import. Selecting ALL still includes it.
DEFAULT_RESET_GROUP_IDS = tuple(group_id for group_id in RESET_GROUPS if group_id != "backup_files")


def _configured_path(value: Any) -> Path | None:
    if not isinstance(value, (str, os.PathLike)) or not str(value).strip():
        return None
    try:
        return resolve_output_path(value)
    except (ValueError, OSError):
        return None


def _is_safe_target(path: Path, protected: Iterable[Path]) -> bool:
    """Reset is stricter than export: never erase outside this profile."""
    try:
        resolved = path.resolve()
        if _has_link_component(path):
            return False
        root = USER_DATA_DIR.resolve()
        if resolved == root or not resolved.is_relative_to(root):
            return False
        for item in protected:
            protected_path = item.resolve()
            # Protect the code tree and its ancestors from a configured target.
            if resolved.is_relative_to(protected_path) or protected_path.is_relative_to(resolved):
                return False
        return True
    except (ValueError, OSError, RuntimeError):
        return False


def collect_reset_target_groups(**kwargs: Any) -> dict[str, list[Path]]:
    """Return only root-owned paths; legacy package data is never reset."""
    supplied_root = Path(kwargs.get("user_data_dir", USER_DATA_DIR)).resolve()
    if supplied_root != USER_DATA_DIR:
        raise ValueError("Factory reset must use the selected user-data root")
    user_data_dir = USER_DATA_DIR
    project_dir = Path(kwargs.get("project_dir", PROJECT_DIR))
    protected = (PROJECT_DIR, project_dir)
    cfg = kwargs.get("config") if isinstance(kwargs.get("config"), dict) else {}

    runtime = [user_data_dir / name for name in _USER_ROOT_FILES]
    runtime.extend(user_data_dir / name for name in ("Data", "qr_codes", "temp", "cache", "账号恢复"))
    runtime.extend(Path(kwargs[key]) for key in ("data_dir", "cipher_key_file") if kwargs.get(key))
    generated_names = ("KnowledgeBase", "highlights", "html_exports", "MindMaps", "Word")
    generated = [user_data_dir / name for name in generated_names]
    models = [user_data_dir / "models", user_data_dir / "model"]
    docs = [user_data_dir / "docs"]
    custom_values = (
        cfg.get("knowledge_base_dir"),
        (cfg.get("knowledge") or {}).get("base_dir") if isinstance(cfg.get("knowledge"), dict) else None,
        (cfg.get("document_export") or {}).get("output_dir") if isinstance(cfg.get("document_export"), dict) else None,
        (cfg.get("document_export") or {}).get("folder_name") if isinstance(cfg.get("document_export"), dict) else None,
        (cfg.get("mindmap") or {}).get("output_dir") if isinstance(cfg.get("mindmap"), dict) else None,
        (cfg.get("dry_goods") or {}).get("folder_name") if isinstance(cfg.get("dry_goods"), dict) else None,
    )
    for value in custom_values:
        path = _configured_path(value)
        if path is not None:
            generated.append(path)
    asr_cfg = cfg.get("asr") if isinstance(cfg.get("asr"), dict) else {}
    model_path = _configured_path(asr_cfg.get("funasr_model_dir"))
    if model_path is not None:
        models.append(model_path)

    def unique(paths: Iterable[Path], *, keep_backups: bool = True) -> list[Path]:
        seen: set[Path] = set()
        output: list[Path] = []
        backup = user_data_dir / "backups"
        for path in paths:
            if not _is_safe_target(path, protected):
                continue
            key = path.resolve()
            if keep_backups and (key.is_relative_to(backup) or backup.is_relative_to(key)):
                continue
            if key not in seen:
                seen.add(key)
                output.append(key)
        return output

    return {
        "credentials_runtime": unique(runtime),
        "knowledge_generated": unique(generated),
        "local_models": unique(models),
        "project_docs": unique(docs),
        "backup_files": unique([user_data_dir / "backups"], keep_backups=False),
    }


def _path_usage(path: Path) -> tuple[int, int]:
    if not path.exists() or _has_link_component(path):
        return 0, 0
    if path.is_file():
        try:
            return 1, path.stat().st_size
        except OSError:
            return 0, 0
    files = total = 0
    try:
        for directory, directories, filenames in os.walk(path, followlinks=False):
            parent = Path(directory)
            directories[:] = [name for name in directories if not _is_link(parent / name)]
            for name in filenames:
                child = parent / name
                if child.is_file() and not _is_link(child):
                    try:
                        files += 1
                        total += child.stat().st_size
                    except OSError:
                        pass
    except OSError:
        pass
    return files, total


def preview_reset_targets(*, selected_groups: Iterable[str] | None = None, **kwargs: Any) -> dict[str, Any]:
    selected = set(selected_groups or DEFAULT_RESET_GROUP_IDS)
    unknown = selected.difference(RESET_GROUPS)
    if unknown:
        raise ValueError(f"Unknown reset groups: {', '.join(sorted(unknown))}")
    grouped = collect_reset_target_groups(**kwargs)
    groups = []
    for group_id, meta in RESET_GROUPS.items():
        files = total = 0
        paths = grouped[group_id]
        for path in paths:
            count, size = _path_usage(path)
            files += count
            total += size
        groups.append({"id": group_id, **meta, "selected": group_id in selected,
                       "paths": [str(path) for path in paths], "files": files, "bytes": total})
    return {"groups": groups, "selected_groups": [key for key in RESET_GROUPS if key in selected]}


def collect_reset_targets(**kwargs: Any) -> list[Path]:
    """Return every selectable target, including the opt-in backup folder."""
    grouped = collect_reset_target_groups(**kwargs)
    return [path for group_id in RESET_GROUPS for path in grouped[group_id]]


def erase_all_user_data(**kwargs: Any) -> dict[str, Any]:
    """Delete all reset targets and recreate only the empty runtime data dir."""
    data_dir = USER_DATA_DIR / "Data"
    selected = set(kwargs.pop("selected_groups", None) or DEFAULT_RESET_GROUP_IDS)
    unknown = selected.difference(RESET_GROUPS)
    if unknown:
        raise ValueError(f"Unknown reset groups: {', '.join(sorted(unknown))}")
    grouped = collect_reset_target_groups(**kwargs)
    targets = [path for group_id in RESET_GROUPS if group_id in selected for path in grouped[group_id]]
    deleted: list[str] = []
    failures: list[str] = []
    protected = (PROJECT_DIR, Path(kwargs.get("project_dir", PROJECT_DIR)))
    for target in targets:
        try:
            if not _is_safe_target(target, protected):
                failures.append(f"{target}: unsafe reset target")
                continue
            if target.is_dir():
                shutil.rmtree(target)
                deleted.append(str(target))
            elif target.exists():
                target.unlink()
                deleted.append(str(target))
        except OSError as exc:
            failures.append(f"{target}: {exc}")
    if "credentials_runtime" in selected:
        if _is_safe_target(data_dir, protected):
            data_dir.mkdir(parents=True, exist_ok=True)
        else:
            failures.append(f"{data_dir}: unsafe runtime data directory")
    return {"deleted": deleted, "failures": failures, "selected_groups": sorted(selected)}
