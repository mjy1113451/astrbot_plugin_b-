""" knowledge_mgr.py — 知识库管理器 ================ 原子写入 + 倒排索引 + bvid去重 + CRUD + 统计 供 astrbot_plugin_bilibili_learning 插件使用 """

import json
import random
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)  # type: ignore


class KnowledgeManager:
    """本地 JSON 知识库管理器。 - 元数据原子写入，断电/崩溃不损坏文件 - bvid 唯一约束 - score 类型/范围校验 - 支持增删改查 + 简易搜索 + 统计 """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        config = config or {}
        # 支持通过 config 覆盖 kb_dir，未指定则按相对路径推导
        kb_dir_override = config.get("kb_dir")
        if kb_dir_override:
            self.kb_dir = Path(kb_dir_override)
        else:
            self.kb_dir = Path(__file__).parent.parent / "data" / "knowledgebase"

        self.kb_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_file = self.kb_dir / "metadata.json"
        self.metadata = self._load_metadata()

        # ---- 简易倒排索引（加速 search） ----
        self._index: Dict[str, set] = {}  # token → set of entry idx
        self._rebuild_index()

    # ─────────────────────────── 持久化 ───────────────────────────

    def _load_metadata(self) -> Dict[str, Any]:
        """加载元数据文件。损坏时告警并返回空结构。"""
        if not self.metadata_file.exists():
            return {"entries": [], "last_updated": ""}

        try:
            data = json.loads(self.metadata_file.read_text(encoding="utf-8"))
            # 兼容旧格式：确保 entries 是列表
            if not isinstance(data.get("entries"), list):
                logger.warning("metadata.json 中 'entries' 不是列表，已重置")
                data["entries"] = []
            return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "元数据文件 %s 损坏/不可读 (%s)，使用空知识库。"
                " 原始文件已备份为 .bak",
                self.metadata_file,
                exc,
            )
            # 备份损坏文件，方便事后排查
            try:
                bak = self.metadata_file.with_suffix(".json.bak")
                bak.write_bytes(self.metadata_file.read_bytes())
            except OSError:
                pass
            return {"entries": [], "last_updated": ""}

    def _save_metadata(self) -> None:
        """原子写入元数据：先写 .tmp，再 replace，保证崩溃不损坏。"""
        self.metadata["last_updated"] = datetime.now().isoformat()

        tmp = self.metadata_file.with_suffix(".json.tmp")
        data = json.dumps(self.metadata, ensure_ascii=False, indent=2)
        tmp.write_text(data, encoding="utf-8")
        tmp.replace(self.metadata_file)  # 原子替换（同分区内为 rename）

    # ─────────────────────────── 索引 ───────────────────────────

    def _tokenize(self, text: str) -> set:
        """极简分词：按空格 / 常见标点切分，全小写。"""
        tokens = set()
        for ch in "，,。/、；：\"'!！?？\n\r\t()（）[]【】{}":
            text = text.replace(ch, " ")
        for token in text.split():
            token = token.strip().lower()
            if token:
                tokens.add(token)
        return tokens

    def _index_entry(self, idx: int, entry: Dict) -> None:
        """为一条 entry 建立索引。"""
        source = " ".join(
            entry.get(k, "") for k in ("title", "category", "up")
        )
        for token in self._tokenize(source):
            self._index.setdefault(token, set()).add(idx)

    def _rebuild_index(self) -> None:
        """全量重建倒排索引。"""
        self._index.clear()
        for i, entry in enumerate(self.metadata.get("entries", [])):
            self._index_entry(i, entry)

    # ─────────────────────────── CRUD ───────────────────────────

    def add_entry( self, bvid: str, title: str, up: str, category: str, score: Any, file_path: str, ) -> Dict[str, Any]:
        """添加知识条目。 Raises: ValueError: bvid 重复 / score 非法 / file_path 不存在 / 参数类型错误 """
        # ---- 去重 ----
        for e in self.metadata.get("entries", []):
            if e.get("bvid") == bvid:
                raise ValueError(f"bvid '{bvid}' 已存在，不允许重复添加")

        # ---- score 校验 ----
        if not isinstance(score, (int, float)):
            raise TypeError(
                f"score 必须为数字，收到 {type(score).__name__}: {score!r}"
            )
        if score < 0:
            raise ValueError(f"score 不能为负数: {score}")

        # ---- file_path：不存在则创建占位文件 ----
        fp = Path(file_path)
        if not fp.exists():
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(
                json.dumps({"title": title, "up": up, "bvid": bvid,
                            "score": float(score), "category": category},
                           ensure_ascii=False, indent=2),
                encoding="utf-8")

        entry: Dict[str, Any] = {
            "bvid": bvid,
            "title": title,
            "up": up,
            "category": category,
            "score": float(score),
            "file_path": str(fp.resolve()),  # 存绝对路径
            "added": datetime.now().isoformat(),
        }

        entries: List[Dict] = self.metadata.setdefault("entries", [])
        idx = len(entries)
        entries.append(entry)

        # 增量更新索引
        self._index_entry(idx, entry)

        self._save_metadata()
        return dict(entry)  # 返回 copy，避免调用方意外修改

    def update_entry(self, bvid: str, **kwargs) -> Dict[str, Any]:
        """按 bvid 更新条目字段。返回更新后的 entry copy。 Raises: KeyError: bvid 不存在 """
        entries = self.metadata.get("entries", [])
        for i, e in enumerate(entries):
            if e.get("bvid") == bvid:
                if "score" in kwargs:
                    s = kwargs["score"]
                    if not isinstance(s, (int, float)) or s < 0:
                        raise ValueError(f"score 非法: {s}")
                    kwargs["score"] = float(s)
                if "file_path" in kwargs:
                    fp = Path(kwargs["file_path"])
                    if not fp.exists():
                        raise FileNotFoundError(f"文件不存在: {kwargs['file_path']}")
                    kwargs["file_path"] = str(fp.resolve())
                e.update(kwargs)
                e["updated"] = datetime.now().isoformat()
                self._rebuild_index()  # 更新可能影响搜索字段，全量重建（条目少时成本低）
                self._save_metadata()
                return dict(e)
        raise KeyError(f"bvid '{bvid}' 不存在")

    def delete_entry(self, bvid: str) -> Dict[str, Any]:
        """按 bvid 删除条目。返回被删除的 entry。 Raises: KeyError: bvid 不存在 """
        entries = self.metadata.get("entries", [])
        for i, e in enumerate(entries):
            if e.get("bvid") == bvid:
                removed = entries.pop(i)
                self._rebuild_index()
                self._save_metadata()
                return removed
        raise KeyError(f"bvid '{bvid}' 不存在")

    def get_entry(self, bvid: str) -> Optional[Dict[str, Any]]:
        """按 bvid 获取条目（返回 copy）。"""
        for e in self.metadata.get("entries", []):
            if e.get("bvid") == bvid:
                return dict(e)
        return None

    # ─────────────────────────── 查询 ───────────────────────────

    def get_random_entry(self) -> Optional[Dict[str, Any]]:
        """随机获取一条知识。始终返回副本，不污染原始数据。 中文安全截断：按 UTF-8 字节边界取前 300 字节再解码。 """
        entries = self.metadata.get("entries", [])
        if not entries:
            return None

        source = random.choice(entries)
        result = dict(source)  # ← 关键：返回副本

        try:
            fp = Path(source["file_path"])
            if fp.exists():
                content = fp.read_text(encoding="utf-8")
                if content:
                    # 按字节截断避免切断多字节字符（如中文）
                    raw = content.encode("utf-8")[:300]
                    result["summary_preview"] = raw.decode("utf-8", errors="ignore")
                else:
                    result["summary_preview"] = ""
            else:
                result["summary_preview"] = ""
        except Exception:
            result["summary_preview"] = ""

        return result

    def search(self, keyword: str, limit: int = 10) -> List[Dict[str, Any]]:
        """搜索知识库（优先使用倒排索引，回退 O(n) 子串匹配）。"""
        keyword_lower = keyword.strip().lower()
        if not keyword_lower:
            return []

        entries = self.metadata.get("entries", [])
        if not entries:
            return []

        # 尝试索引查找（分词匹配）
        idx_candidates: Optional[set] = None
        for token in self._tokenize(keyword_lower):
            if token in self._index:
                idx_candidates = (
                    self._index[token]
                    if idx_candidates is None
                    else idx_candidates & self._index[token]
                )

        results: List[Dict] = []
        source = (
            (entries[i] for i in sorted(idx_candidates))
            if idx_candidates
            else entries  # 回退全量扫描
        )

        for entry in source:
            if (
                keyword_lower in entry.get("title", "").lower()
                or keyword_lower in entry.get("category", "").lower()
                or keyword_lower in entry.get("up", "").lower()
            ):
                results.append(dict(entry))  # 返回 copy
                if len(results) >= limit:
                    break

        return results

    # ─────────────────────────── 统计 ───────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息。"""
        entries = self.metadata.get("entries", [])
        categories: Dict[str, int] = {}
        score_sum = 0.0
        score_count = 0

        for e in entries:
            cat = e.get("category", "未分类")
            categories[cat] = categories.get(cat, 0) + 1

            s = e.get("score")
            if isinstance(s, (int, float)) and s >= 0:
                score_sum += float(s)
                score_count += 1

        return {
            "total_files": len(entries),
            "categories": categories,
            "avg_score": round(score_sum / max(1, score_count), 1),
            "last_updated": self.metadata.get("last_updated", ""),
        }

    # ─────────────────────────── 工具 ───────────────────────────

    def __len__(self) -> int:
        return len(self.metadata.get("entries", []))

    def __repr__(self) -> str:
        n = len(self)
        stats = self.get_stats()
        return (
            f"<KnowledgeManager entries={n} "
            f"avg_score={stats['avg_score']} "
            f"kb_dir={self.kb_dir!s}>"
        )