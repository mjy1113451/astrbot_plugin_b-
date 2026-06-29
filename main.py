""" astrbot_plugin_bilibili_learning/main.py B站学习助手 AstrBot 插件 ======================= 基于 bilibili_learning_bot (https://github.com/xiaoyaya191/bilibili_learning_bot) 移植 核心命令组 bl: - bl — 菜单 - bl analyze <BV号> — AI分析视频 - bl search <关键词> — 搜索B站视频 - bl learn <BV号> — 学习归档视频 - bl review — 从知识库随机回顾 - bl search_kb <关键词> — 搜索知识库 - bl hot — B站热门 - bl stats — 统计信息 - bl up <关键词> — 搜索UP主 - bl mood [心情] — 查看/设置心情 - bl auto start [数量] — 全自动刷视频 - bl auto stop — 停止 - bl auto status — 状态 快捷别名: - /bili_search /bili_info /bili_analyze /bili_hot /bili_kb /bili_kb_list - /bili_auto_start /bili_auto_stop /bili_auto_status """
import asyncio
import json
import math
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Star, Context
from astrbot.api import logger

from .knowledge_mgr import KnowledgeManager


# ── 常量 ──
PLUGIN_DIR = Path(__file__).resolve().parent
DATA_DIR = PLUGIN_DIR / "data"

BILI_SEARCH_API = "https://api.bilibili.com/x/web-interface/search/type"
BILI_VIDEO_INFO_API = "https://api.bilibili.com/x/web-interface/view"
BILI_HOT_API = "https://api.bilibili.com/x/web-interface/popular"
BILI_RCMD_API = "https://api.bilibili.com/x/web-interface/index/top/feed/rcmd"
BILI_TAG_API = "https://api.bilibili.com/x/tag/archive/tags"
BILI_USER_SEARCH_API = "https://api.bilibili.com/x/web-interface/search/type"
BILI_USER_INFO_API = "https://api.bilibili.com/x/space/acc/info"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
}

AUTO_STATE_FILE = DATA_DIR / "auto_state.json"


# ── 工具函数 ──
def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def _format_duration(seconds: int) -> str:
    h, s = divmod(seconds, 3600)
    m, s = divmod(s, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _format_count(n: int) -> str:
    if n >= 10000:
        return f"{n / 10000:.1f}万"
    return str(n)


def _now_iso() -> str:
    return datetime.now().isoformat()


def _extract_bvid(text: str) -> str:
    """从文本提取BV号（支持链接、av号）"""
    m = re.search(r'(BV[0-9A-Za-z]{10})', text)
    if m:
        return m.group(1)
    m = re.search(r'av(\d+)', text, re.IGNORECASE)
    if m:
        return f"av{m.group(1)}"
    if re.search(r'b23\.tv/', text):
        return ""
    if text.startswith("BV") and len(text.strip()) == 12:
        return text.strip()
    return ""


def _mask_secret(value: str) -> str:
    if not value:
        return "(未配置)"
    if len(value) <= 12:
        return "*" * len(value)
    return f"{value[:6]}...{value[-4:]}"


# ── 主插件类 ──
class BiliLearningPlugin(Star):
    """B站学习助手 AstrBot 插件"""

    def __init__(self, context: Context, config: Optional[dict] = None) -> None:
        super().__init__(context)
        self.config = config or {}
        DATA_DIR.mkdir(parents=True, exist_ok=True)

        # ── 基础配置 ──
        self.api_base_url = self.config.get("api_base_url", "https://api.openai.com/v1")
        self.api_key = self.config.get("api_key", "")
        self.model = self.config.get("model", "gpt-4.1-mini")
        self.search_count = int(self.config.get("search_count", 5))
        self.kb_top_k = int(self.config.get("kb_top_k", 5))
        self.kb_min_score = float(self.config.get("kb_min_score", 0.3))

        # ── 自动模式配置 ──
        self.auto_max_videos = int(self.config.get("auto_max_videos", 10))
        self.auto_interval_min = int(self.config.get("auto_interval_min", 30))
        self.auto_interval_max = int(self.config.get("auto_interval_max", 90))
        self.auto_min_score = float(self.config.get("auto_min_score", 6.0))
        self.auto_max_duration = int(self.config.get("auto_max_duration", 900))

        # ── B站 Cookie ──
        self._bili_cookies = {}
        for k_conf, k_cookie in [
            ("bilibili_sessdata", "SESSDATA"),
            ("bilibili_jct", "bili_jct"),
            ("bilibili_buvid3", "buvid3"),
            ("bilibili_dedeuserid", "DedeUserID"),
        ]:
            val = self.config.get(k_conf, "")
            if val:
                self._bili_cookies[k_cookie] = val

        # ── 知识库 ──
        kb_config = {"kb_dir": str(DATA_DIR / "knowledgebase")}
        self.knowledge_mgr = KnowledgeManager(kb_config)

        # ── 自动模式状态 ──
        self._auto_task: Optional[asyncio.Task] = None
        self._auto_state = self._load_auto_state()

        # ── 心情 ──
        self._mood_file = DATA_DIR / "mood.json"

        logger.info(f"[bili] 插件初始化完成，知识库: {self.knowledge_mgr}")

    # ========== 持久化 ==========

    def _default_auto_state(self) -> dict:
        return {
            "running": False, "started_at": None, "target_count": 0,
            "videos_watched": 0, "videos_skipped": 0, "videos_archived": 0,
            "current_video": None, "last_error": None,
        }

    def _load_auto_state(self) -> dict:
        if AUTO_STATE_FILE.exists():
            try:
                data = json.loads(AUTO_STATE_FILE.read_text("utf-8"))
                data["running"] = False
                return data
            except (json.JSONDecodeError, KeyError):
                pass
        return self._default_auto_state()

    def _save_auto_state(self) -> None:
        AUTO_STATE_FILE.write_text(
            json.dumps(self._auto_state, ensure_ascii=False, indent=2), "utf-8")

@property
    def _has_ai(self) -> bool:
        return bool(self.api_key and self.api_base_url and self.model)

    def _has_bili_login(self) -> bool:
        return bool(self._bili_cookies.get("SESSDATA"))

    # ========== LLM ==========

    async def _llm_chat(self, messages: list[dict], timeout: int = 60) -> str:
        url = self.api_base_url.rstrip("/") + "/chat/completions"
        payload = {"model": self.model, "messages": messages, "temperature": 0.7}
        hdrs = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=timeout) as c:
            resp = await c.post(url, headers=hdrs, json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(f"AI API HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        return (data["choices"][0]["message"]["content"] or "").strip()

    async def _llm_embedding(self, text: str) -> list[float]:
        url = self.api_base_url.rstrip("/") + "/embeddings"
        payload = {"model": "text-embedding-3-small", "input": text[:8000]}
        hdrs = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=30) as c:
            resp = await c.post(url, headers=hdrs, json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(f"Embedding HTTP {resp.status_code}")
        return [float(x) for x in resp.json()["data"][0]["embedding"]]

    # ========== B站 API ==========

    async def _bili_request(self, url: str, params: dict = None) -> dict:
        async with httpx.AsyncClient(http2=True, headers=HEADERS,
                                       cookies=self._bili_cookies, timeout=20) as c:
            resp = await c.get(url, params=params or {})
            resp.raise_for_status()
            data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(data.get("message", "B站API请求失败"))
        return data.get("data", {})

    # ================================================================
    # bl 命令组
    # ================================================================

@filter.command("bl")
    async def cmd_menu(self, event: AstrMessageEvent):
        """查看功能菜单"""
        kb = self.knowledge_mgr.get_stats()
        running = (self._auto_task and not self._auto_task.done()
                   and self._auto_state.get("running", False))
        auto_line = ""
        if running:
            s = self._auto_state
            auto_line = f"\n║ 🤖 自动模式: 运行中 {s['videos_watched']}/{s['target_count'] or '∞'}"

        yield event.plain_result(f"""╔══════════════════════════════════════════╗ ║ 🤖 B站学习助手 ╠══════════════════════════════════════════╣ ║ 📺 bl analyze <BV号/链接> — AI分析视频 ║ 📺 bl search <关键词> — 搜索B站视频 ║ 📺 bl hot — B站热门 ║ 📚 bl learn <BV号> — 学习归档 ║ 📖 bl review — 回顾复习 ║ 🔍 bl search_kb <关键词> — 搜索知识库 ║ 👤 bl up <关键词> — 搜索UP主 ║ 📊 bl stats — 统计 ║ 🎭 bl mood [心情] — 心情 ║ 🤖 bl auto start [数量] — 全自动刷视频 ║ 🤖 bl auto stop — 停止 ║ 🤖 bl auto status — 状态 ╚══════════════════════════════════════════╝ 📚 知识库: {kb['total_files']}条 | B站: {'✅' if self._has_bili_login() else '⚠️未登录'} AI: {'✅ ' + self.model if self._has_ai else '❌未配置'}{auto_line}""")

@filter.command_group("bl")
    def bl_group(self):
        pass

    # ---- analyze ----
@bl_group.command("analyze")
    async def cmd_analyze(self, event: AstrMessageEvent, arg: str = ""):
        arg = (arg or "").strip()
        bvid = _extract_bvid(arg) if arg else ""
        if not bvid:
            yield event.plain_result("用法: bl analyze <BV号/链接>")
            return
        if not self._has_ai:
            yield event.plain_result("❌ 未配置 AI API")
            return
        yield event.plain_result(f"⏳ 正在分析 {bvid}...")
        result = await self._analyze_video(bvid)
        yield event.plain_result(result)

    # ---- search ----
@bl_group.command("search")
    async def cmd_search(self, event: AstrMessageEvent, keyword: str = ""):
        keyword = (keyword or "").strip()
        if not keyword:
            yield event.plain_result("用法: bl search <关键词>")
            return
        try:
            data = await self._bili_request(BILI_SEARCH_API, params={
                "search_type": "video", "keyword": keyword, "page": 1,
            })
        except Exception as e:
            yield event.plain_result(f"搜索失败: {e}")
            return
        items = data.get("result", [])[:self.search_count]
        if not items:
            yield event.plain_result(f"未找到「{keyword}」")
            return
        lines = [f"🔍 B站搜索「{keyword}」:"]
        for i, v in enumerate(items, 1):
            title = _strip_html(v.get("title", "?"))
            bvid = v.get("bvid", "")
            play = _format_count(v.get("play", 0))
            dur = _strip_html(v.get("duration", "00:00"))
            author = v.get("author", "?")
            lines.append(f"{i}. {title}\n BV:{bvid} | UP:{author} | ▶{play} | ⏱{dur}")
        yield event.plain_result("\n\n".join(lines))

    # ---- hot ----
@bl_group.command("hot")
    async def cmd_hot(self, event: AstrMessageEvent):
        yield event.plain_result("⏳ 获取热门...")
        items = await self._get_hot_videos()
        if not items:
            yield event.plain_result("获取失败")
            return
        lines = ["🔥 B站热门 TOP10:"]
        for i, v in enumerate(items, 1):
            lines.append(f"{i}. {v['title']}\n BV:{v['bvid']} | UP:{v['author']} | ▶{v['play']}")
        yield event.plain_result("\n\n".join(lines))

    # ---- learn ----
@bl_group.command("learn")
    async def cmd_learn(self, event: AstrMessageEvent, arg: str = ""):
        arg = (arg or "").strip()
        bvid = _extract_bvid(arg) if arg else ""
        if not bvid:
            yield event.plain_result("用法: bl learn <BV号/链接>")
            return
        if not self._has_ai:
            yield event.plain_result("❌ 未配置 AI API")
            return
        yield event.plain_result(f"📚 正在学习归档 {bvid}...")
        result = await self._learn_video(bvid)
        yield event.plain_result(result)

    # ---- review ----
@bl_group.command("review")
    async def cmd_review(self, event: AstrMessageEvent):
        entry = self.knowledge_mgr.get_random_entry()
        if not entry:
            yield event.plain_result("知识库为空，请先用 bl learn 学习视频")
            return
        bvid = entry.get("bvid", "")
        summary = entry.get("summary_preview", "")
        yield event.plain_result(
            f"📖 回顾复习\n━━━━━━━━━━━━━━━━\n"
            f"标题: {entry.get('title', '?')}\n"
            f"BV号: {bvid}\n"
            f"UP主: {entry.get('up', '?')}\n"
            f"分类: {entry.get('category', '?')}\n"
            f"评分: {entry.get('score', '?')}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"{summary[:300]}\n"
            f"🔗 https://www.bilibili.com/video/{bvid}"
        )

    # ---- search_kb ----
@bl_group.command("search_kb")
    async def cmd_search_kb(self, event: AstrMessageEvent, keyword: str = ""):
        keyword = (keyword or "").strip()
        if not keyword:
            yield event.plain_result("用法: bl search_kb <关键词>")
            return
        results = self.knowledge_mgr.search(keyword, limit=self.kb_top_k)
        if not results:
            yield event.plain_result(f"知识库中未找到「{keyword}」")
            return
        lines = [f"📚 知识库「{keyword}」:"]
        for r in results:
            lines.append(
                f"\n· {r.get('title', '?')} [评分:{r.get('score', '?')}]\n"
                f" BV:{r.get('bvid', '?')} | 分类:{r.get('category', '?')}"
            )
        yield event.plain_result("\n".join(lines))

    # ---- up ----
@bl_group.command("up")
    async def cmd_up(self, event: AstrMessageEvent, keyword: str = ""):
        keyword = (keyword or "").strip()
        if not keyword:
            yield event.plain_result("用法: bl up <关键词>\n示例: bl up 罗翔")
            return
        try:
            data = await self._bili_request(BILI_USER_SEARCH_API, params={
                "search_type": "bili_user", "keyword": keyword, "page": 1,
            })
            users = (data.get("result") or [])[:3]
        except Exception as e:
            yield event.plain_result(f"搜索UP主失败: {e}")
            return
        if not users:
            yield event.plain_result(f"未找到UP主「{keyword}」")
            return
        lines = [f"🔍 UP主「{keyword}」结果:"]
        for u in users:
            lines.append(
                f"\n· {_strip_html(u.get('uname', '?'))}\n"
                f" UID:{u.get('mid', '?')} | 粉丝:{_format_count(u.get('fans', 0))}\n"
                f" {_strip_html(u.get('usign', ''))[:80]}"
            )
        yield event.plain_result("\n".join(lines))

    # ---- stats ----
@bl_group.command("stats")
    async def cmd_stats(self, event: AstrMessageEvent):
        kb = self.knowledge_mgr.get_stats()
        yield event.plain_result(
            f"📊 统计\n━━━━━━━━━━━━━━━━\n"
            f"📚 知识库: {kb['total_files']} 条\n"
            f"📊 平均评分: {kb.get('avg_score', 0)}\n"
            f"📂 分类数: {len(kb.get('categories', {}))}\n"
            f"🤖 AI: {'✅ ' + self.model if self._has_ai else '❌ 未配置'}\n"
            f"🔐 B站: {'✅ 已登录' if self._has_bili_login() else '⚠️ 未登录'}"
        )

    # ---- mood ----
@bl_group.command("mood")
    async def cmd_mood(self, event: AstrMessageEvent, mood: str = ""):
        if not mood:
            current = self._get_mood()
            yield event.plain_result(f"🎭 当前心情: {current}")
            return
        self._set_mood(mood.strip())
        yield event.plain_result(f"🎭 心情已设置为: {mood.strip()}")

    # ---- auto sub-commands ----
@bl_group.command("auto")
    async def cmd_auto(self, event: AstrMessageEvent, action: str = ""):
        action = (action or "").strip().lower()
        if action == "start":
            await self._auto_start(event)
        elif action == "stop":
            await self._auto_stop(event)
        elif action == "status":
            await self._auto_status(event)
        else:
            yield event.plain_result(
                "用法:\nbl auto start [数量] — 启动自动刷视频\n"
                "bl auto stop — 停止\nbl auto status — 查看状态"
            )

    # ================================================================
    # 快捷别名（兼容旧命令）
    # ================================================================

@filter.command("bili_analyze")
    async def _alias_analyze(self, event: AstrMessageEvent, bvid: str = ""):
        await self.cmd_analyze(event, bvid)

@filter.command("bili_search")
    async def _alias_search(self, event: AstrMessageEvent, keyword: str = ""):
        await self.cmd_search(event, keyword)

@filter.command("bili_hot")
    async def _alias_hot(self, event: AstrMessageEvent):
        await self.cmd_hot(event)

@filter.command("bili_info")
    async def _alias_info(self, event: AstrMessageEvent, bvid: str = ""):
        bvid = _extract_bvid(bvid.strip() if bvid else "")
        if not bvid:
            yield event.plain_result("用法: /bili_info <BV号>")
            return
        try:
            data = await self._bili_request(BILI_VIDEO_INFO_API, params={"bvid": bvid})
        except Exception as e:
            yield event.plain_result(f"获取失败: {e}")
            return
        stat = data.get("stat", {})
        owner = data.get("owner", {})
        yield event.plain_result(
            f"📺 {data.get('title', '?')}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"UP: {owner.get('name', '?')} | {_format_duration(data.get('duration', 0))}\n"
            f"▶{_format_count(stat.get('view',0))} 💬{_format_count(stat.get('reply',0))} "
            f"👍{_format_count(stat.get('like',0))}\n"
            f"简介: {(data.get('desc','') or '无')[:200]}\n"
            f"🔗 https://www.bilibili.com/video/{bvid}"
        )

@filter.command("bili_kb")
    async def _alias_kb(self, event: AstrMessageEvent, keyword: str = ""):
        await self.cmd_search_kb(event, keyword)

@filter.command("bili_kb_list")
    async def _alias_kb_list(self, event: AstrMessageEvent):
        await self.cmd_stats(event)

@filter.command("bili_auto_start")
    async def _alias_auto_start(self, event: AstrMessageEvent, count: str = ""):
        await self._auto_start(event, count)

@filter.command("bili_auto_stop")
    async def _alias_auto_stop(self, event: AstrMessageEvent):
        await self._auto_stop(event)

@filter.command("bili_auto_status")
    async def _alias_auto_status(self, event: AstrMessageEvent):
        await self._auto_status(event)

    # ================================================================
    # 核心功能实现
    # ================================================================

    async def _get_hot_videos(self, count: int = 10) -> list[dict]:
        try:
            data = await self._bili_request(BILI_HOT_API, params={"ps": 50})
            items = (data.get("list") or [])[:count]
        except Exception:
            try:
                data = await self._bili_request(BILI_RCMD_API, params={"ps": count})
                items = data.get("item", [])[:count]
            except Exception:
                return []
        return [{
            "bvid": v.get("bvid", ""),
            "title": _strip_html(v.get("title", "?")),
            "author": (v.get("owner", {}) or {}).get("name", "?"),
            "play": _format_count(v.get("stat", {}).get("view", v.get("play", 0))),
        } for v in items]

    async def _analyze_video(self, bvid: str) -> str:
        try:
            data = await self._bili_request(BILI_VIDEO_INFO_API, params={"bvid": bvid})
        except Exception as e:
            return f"获取视频信息失败: {e}"
        title = data.get("title", "")
        desc = (data.get("desc", "") or "无简介")[:500]
        owner = data.get("owner", {}).get("name", "?")
        dur = _format_duration(data.get("duration", 0))
        try:
            tags = await self._bili_request(BILI_TAG_API, params={"bvid": bvid})
            tag_str = ", ".join(t.get("tag_name", "") for t in (tags or []))
        except Exception:
            tag_str = ""
        prompt = (
            "你是视频内容分析助手。根据以下信息生成内容总结和知识点（200字内）。\n\n"
            f"标题: {title}\nUP主: {owner}\n时长: {dur}\n"
            f"标签: {tag_str or '无'}\n简介: {desc[:300]}\n\n"
            "格式:\n【内容总结】\n（1-2句话）\n【知识点】\n- 知识点1\n- ..."
        )
        try:
            analysis = await self._llm_chat([{"role": "user", "content": prompt}], timeout=90)
        except Exception as e:
            return f"AI分析失败: {e}"
        return (
            f"🤖 AI分析: {title}\n━━━━━━━━━━━━━━━━\n{analysis}\n"
            f"━━━━━━━━━━━━━━━━\n🔗 https://www.bilibili.com/video/{bvid}"
        )

    async def _learn_video(self, bvid: str) -> str:
        """分析视频并存入知识库"""
        try:
            data = await self._bili_request(BILI_VIDEO_INFO_API, params={"bvid": bvid})
        except Exception as e:
            return f"获取视频信息失败: {e}"
        title = data.get("title", "")
        desc = (data.get("desc", "") or "无简介")[:500]
        owner = data.get("owner", {}).get("name", "?")
        dur = _format_duration(data.get("duration", 0))
        try:
            tags = await self._bili_request(BILI_TAG_API, params={"bvid": bvid})
            tag_str = ", ".join(t.get("tag_name", "") for t in (tags or []))
        except Exception:
            tag_str = ""
        # AI 评分
        score = 5.0
        if self._has_ai:
            try:
                reply = await self._llm_chat([{
                    "role": "user",
                    "content": f"给这个视频的知识价值打分(0~10)，只回复数字。\n标题:{title}\nUP主:{owner}\n标签:{tag_str}"
                }], timeout=20)
                m = re.search(r"(\d+(?:\.\d+)?)", reply)
                if m:
                    score = min(10.0, max(0.0, float(m.group(1))))
            except Exception:
                pass
        # 写入知识库
        try:
            self.knowledge_mgr.add_entry(
                bvid=bvid, title=title, up=owner,
                category="", score=score,
                file_path=str(DATA_DIR / "placeholder.md"),
            )
        except ValueError as e:
            return f"该视频已存在知识库中\n标题: {title}\n评分: {score}"
        except Exception as e:
            return f"知识库存入失败: {e}"
        # 如果分类为空，补一个默认分类
        if not tag_str:
            tag_str = "未分类"
        try:
            self.knowledge_mgr.update_entry(bvid, category=tag_str[:50])
        except Exception:
            pass
        return (
            f"✅ 已学习归档\n━━━━━━━━━━━━━━━━\n"
            f"标题: {title}\nUP主: {owner}\n时长: {dur}\n"
            f"AI评分: {score:.1f}/10\n分类: {tag_str[:50]}\n"
            f"🔗 https://www.bilibili.com/video/{bvid}"
        )

    # ========== 自动模式 ==========

    async def _auto_start(self, event: AstrMessageEvent, count: str = ""):
        if not self._has_ai:
            yield event.plain_result("❌ 未配置 AI API")
            return
        count_val = 0
        if count.strip():
            try:
                count_val = int(count.strip())
            except ValueError:
                yield event.plain_result(f"'{count}' 不是有效数字")
                return
        if not count_val:
            count_val = self.auto_max_videos
        if self._auto_task and not self._auto_task.done():
            yield event.plain_result("⚠️ 已在运行中，先 /bili_auto_stop 停止")
            return
        self._auto_state = self._default_auto_state()
        self._auto_state["running"] = True
        self._auto_state["started_at"] = _now_iso()
        self._auto_state["target_count"] = count_val
        self._save_auto_state()
        self._auto_task = asyncio.create_task(self._auto_browse_loop())
        yield event.plain_result(
            f"🚀 全自动刷视频已启动！\n目标: {count_val}个 | "
            f"间隔: {self.auto_interval_min}~{self.auto_interval_max}s | "
            f"门槛: {self.auto_min_score}分\n"
            f"bl auto status 查进度 | bl auto stop 停止"
        )

    async def _auto_stop(self, event: AstrMessageEvent):
        if not self._auto_task or self._auto_task.done():
            yield event.plain_result("自动模式未运行")
            return
        self._auto_state["running"] = False
        self._save_auto_state()
        self._auto_task.cancel()
        s = self._auto_state
        yield event.plain_result(
            f"🛑 已停止 | 刷了{s['videos_watched']}个 "
            f"归档{s['videos_archived']}个 跳过{s['videos_skipped']}个"
        )

    async def _auto_status(self, event: AstrMessageEvent):
        running = (self._auto_task and not self._auto_task.done()
                   and self._auto_state.get("running", False))
        s = self._auto_state
        lines = [
            f"📊 自动模式: {'▶ 运行中' if running else '⏸ 已停止'}",
            f"进度: {s['videos_watched']}/{s['target_count'] or '∞'} "
            f"(归档{s['videos_archived']} 跳过{s['videos_skipped']})",
        ]
        if s.get("current_video"):
            lines.append(f"当前: {s['current_video']}")
        yield event.plain_result("\n".join(lines))

    async def _auto_browse_loop(self):
        logger.info("[auto] 循环启动")
        target = self._auto_state["target_count"]
        while self._auto_state["running"]:
            if 0 < target <= self._auto_state["videos_watched"]:
                logger.info(f"[auto] 达目标{target}，停止")
                self._auto_state["running"] = False
                self._save_auto_state()
                break
            try:
                recs = await self._fetch_recommendations()
                if not recs:
                    await asyncio.sleep(self.auto_interval_min)
                    continue
                for rec in recs:
                    if not self._auto_state["running"]:
                        break
                    if 0 < target <= self._auto_state["videos_watched"]:
                        break
                    try:
                        await self._process_one_video(rec)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.error(f"[auto] 处理异常: {e}")
                        self._auto_state["last_error"] = str(e)[:200]
                        self._save_auto_state()
                    if self._auto_state["running"]:
                        await asyncio.sleep(random.randint(
                            self.auto_interval_min, self.auto_interval_max))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[auto] 循环异常: {e}")
                self._auto_state["last_error"] = str(e)[:200]
                self._save_auto_state()
                await asyncio.sleep(self.auto_interval_min * 2)
        self._auto_state["running"] = False
        self._save_auto_state()
        logger.info(f"[auto] 结束 共{self._auto_state['videos_watched']}个")

    async def _fetch_recommendations(self, count: int = 10) -> list[dict]:
        try:
            data = await self._bili_request(BILI_RCMD_API, params={"ps": count})
            items = data.get("item", [])
        except Exception:
            try:
                data = await self._bili_request(BILI_HOT_API, params={"ps": 20})
                items = data.get("list", [])
            except Exception:
                return []
        videos = []
        seen = {e.get("bvid") for e in self.knowledge_mgr.metadata.get("entries", [])}
        for v in items:
            bvid = v.get("bvid", "") or v.get("id", "")
            if not bvid or bvid in seen:
                continue
            dur = v.get("duration", 0)
            if not dur:
                args = v.get("args", {})
                if isinstance(args, dict):
                    dur = int(args.get("duration", 0))
            if self.auto_max_duration > 0 and dur > self.auto_max_duration:
                continue
            videos.append({
                "bvid": bvid,
                "title": _strip_html(v.get("title", "")),
                "author": (v.get("owner", {}) or {}).get("name", "?"),
                "duration": dur,
                "play": v.get("stat", {}).get("view", v.get("play", 0)),
            })
        return videos

    async def _process_one_video(self, rec: dict):
        bvid, title = rec["bvid"], rec["title"]
        self._auto_state["current_video"] = f"{title}({bvid})"
        self._save_auto_state()
        score = await self._quick_score(rec)
        if score < self.auto_min_score:
            self._auto_state["videos_skipped"] += 1
            self._auto_state["videos_watched"] += 1
            self._save_auto_state()
            logger.info(f"[auto] ⏭ 跳过 [{score:.1f}] {title}")
            return
        logger.info(f"[auto] 📺 分析 [{score:.1f}] {title}")
        result = await self._learn_video(bvid)
        self._auto_state["videos_watched"] += 1
        if "✅" in result:
            self._auto_state["videos_archived"] += 1
        self._auto_state["current_video"] = None
        self._auto_state["last_error"] = None
        self._save_auto_state()

    async def _quick_score(self, rec: dict) -> float:
        if not self._has_ai:
            return self.auto_min_score
        try:
            reply = await self._llm_chat([{
                "role": "user",
                "content": (
                    f"评估视频知识价值(0~10)，只回复数字。"
                    f"标题:{rec['title']} UP:{rec.get('author','?')}"
                )
            }], timeout=30)
            m = re.search(r"(\d+(?:\.\d+)?)", reply)
            return min(10.0, max(0.0, float(m.group(1)))) if m else 5.0
        except Exception:
            return 5.0

    # ========== 心情 ==========

    def _get_mood(self) -> str:
        if self._mood_file.exists():
            try:
                data = json.loads(self._mood_file.read_text("utf-8"))
                return data.get("mood", "平静")
            except Exception:
                pass
        return "平静"

    def _set_mood(self, mood: str):
        self._mood_file.write_text(
            json.dumps({"mood": mood, "updated": _now_iso()},
                       ensure_ascii=False, indent=2), "utf-8")