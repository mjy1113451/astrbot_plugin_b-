"""services/platform_adapter.py — 多平台视频输入识别与适配层。

借鉴 BiliNote-v2.4.4-source 的平台识别 / 下载 / 字幕机制：
  - 平台识别正则借鉴 backend/app/utils/url_parser.py
  - 支持平台列表借鉴 backend/app/validators/video_url_validator.py
    (bilibili / youtube / douyin / kuaishou / web / local)
  - YouTube 下载与字幕借鉴 backend/app/downloaders/youtube_*.py
    (yt_dlp 下载音频 / 视频 + youtube-transcript-api 取字幕，均需代理)
  - 本地文件借鉴 backend/app/downloaders/local_downloader.py (ffmpeg 转码)

产品策略：默认仅针对 B站做完整刷视频分析；YouTube / 抖音 / 快手 / 网页 /
本地文件的平台支持代码保留（识别 / 归一化 / 下载骨架），但当前阶段标注
「暂不支持」，UI 不主动暴露其分析入口。

同步 API 保留；显式 *_async 入口通过 asyncio.to_thread 卸载整条同步链。
取消 await 不会终止已运行的工作线程，底层 I/O 使用超时与有限重试，
但这些限制不等于整次下载的硬截止时间。
"""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

try:
    import yt_dlp
except Exception:  # pragma: no cover
    yt_dlp = None

try:
    from youtube_transcript_api import YouTubeTranscriptApi
except Exception:  # pragma: no cover
    YouTubeTranscriptApi = None

from ..core.user_data import DATA_DIR, resolve_output_path

SUPPORTED_PLATFORMS = ("bilibili", "youtube", "douyin", "kuaishou", "web", "local")

PLATFORM_LABELS = {
    "bilibili": "B站",
    "youtube": "YouTube",
    "douyin": "抖音",
    "kuaishou": "快手",
    "web": "网页",
    "local": "本地文件",
}

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"


def resolve_bilibili_short_url(url: str, timeout: int = 10) -> str:
    """解析一次原始短链；HEAD 超时 / HTTP 失败时尝试 GET，失败返回原值。"""
    if "b23.tv" not in (url or "").lower() or requests is None:
        return url
    for request in (requests.head, requests.get):
        try:
            # 只需要重定向后的 URL，不下载 GET 响应体；退出时释放连接。
            with request(
                url, allow_redirects=True, timeout=timeout,
                headers={"User-Agent": _UA}, stream=True,
            ) as resp:
                resp.raise_for_status()
                return resp.url or url
        except Exception:
            continue
    return url


def detect_platform(value: str) -> str:
    """识别输入所属平台（借鉴 BiliNote url_parser + video_url_validator）。"""
    text = (value or "").strip()
    if not text:
        return "unknown"
    low = text.lower()
    if re.search(r"\bBV[0-9A-Za-z]{10}\b", text) or "bilibili.com/video/" in low or "b23.tv" in low:
        return "bilibili"
    if "youtube.com/watch" in low or "youtube.com/shorts/" in low or "youtu.be/" in low:
        return "youtube"
    if "douyin.com" in low or "iesdouyin.com" in low:
        return "douyin"
    if "kuaishou.com" in low or "ks.com" in low:
        return "kuaishou"
    if re.match(r"https?://", low) and "bilibili" not in low:
        return "web"
    if os.path.isabs(text) and os.path.exists(text):
        return "local"
    return "unknown"


def is_supported_video_input(value: str) -> bool:
    return detect_platform(value) in SUPPORTED_PLATFORMS


def extract_video_id(value: str, platform: str | None = None) -> str:
    """从各平台输入中提取视频 ID（借鉴 BiliNote url_parser.extract_video_id）。"""
    text = (value or "").strip()
    platform = platform or detect_platform(text)
    if "b23.tv" in text.lower():
        text = resolve_bilibili_short_url(text)
    return _extract_video_id(text, platform)


def _extract_video_id(text: str, platform: str) -> str:
    """只解析已处理的文本，绝不发起网络请求。"""
    if platform == "bilibili":
        m = re.search(r"\b(BV[0-9A-Za-z]{10})\b", text)
        return m.group(1) if m else ""
    if platform == "youtube":
        m = re.search(r"(?:v=|youtu\.be/|shorts/)([0-9A-Za-z_-]{11})", text)
        return m.group(1) if m else ""
    if platform == "douyin":
        m = re.search(r"/video/(\d+)", text)
        return m.group(1) if m else ""
    if platform == "kuaishou":
        m = re.search(r"/short-video/([\w-]+)", text)
        return m.group(1) if m else ""
    return ""


def extract_bilibili_p_number(value: str) -> int | None:
    """提取 B站分 P 序号（借鉴 BiliNote url_parser.extract_bilibili_p_number）。"""
    text = resolve_bilibili_short_url(value) if "b23.tv" in (value or "").lower() else (value or "")
    return _extract_bilibili_p_number(text)


def _extract_bilibili_p_number(text: str) -> int | None:
    """只解析已处理文本中的分 P，不再解析短链。"""
    m = re.search(r"[?&]p=(\d+)", text)
    if m and int(m.group(1)) >= 1:
        return int(m.group(1))
    m = re.search(r"/p(\d+)(?:/?$|\?|&)", text)
    if m and int(m.group(1)) >= 1:
        return int(m.group(1))
    return None


def normalize_video_input(value: str) -> dict[str, Any]:
    """归一化视频输入为统一结构。"""
    platform = detect_platform(value)
    url = (value or "").strip()
    # 原输入只解析一次；失败或返回 URL 仍含 b23.tv 时也不重复联网。
    text = resolve_bilibili_short_url(url) if "b23.tv" in url.lower() else url
    video_id = _extract_video_id(text, platform)
    if platform == "bilibili" and video_id:
        url = f"https://www.bilibili.com/video/{video_id}"
        p = _extract_bilibili_p_number(text)
        if p:
            url += f"?p={p}"
    return {
        "ok": platform in SUPPORTED_PLATFORMS,
        "platform": platform,
        "video_id": video_id,
        "url": url,
    }


def get_proxy(cfg: dict[str, Any] | None = None) -> str:
    """借鉴 BiliNote ProxyConfigManager：优先配置 platform_adapter.proxy，其次环境变量。"""
    if isinstance(cfg, dict):
        p = (cfg.get("platform_adapter") or {}).get("proxy") or ""
        if p:
            return p
    return os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or ""


def fetch_platform_metadata(
    value: str, cfg: dict[str, Any] | None = None, prefer_subtitle: bool = True
) -> dict[str, Any]:
    """平台元数据预取。

    B站走现有 BiliClient / 字幕强链路；其他平台做识别归一化并标注「暂不支持」。
    """
    norm = normalize_video_input(value)
    if not norm["ok"]:
        return {"ok": False, "message": "暂不支持该链接格式（目前仅完整支持 B站）", **norm}
    platform = norm["platform"]
    if platform == "bilibili":
        return {"ok": True, "message": "B站视频将使用现有 BiliClient / 字幕强链路", **norm}
    # 其他平台：识别成功，但当前阶段暂不支持完整分析
    return {
        "ok": True,
        "platform": platform,
        "video_id": norm["video_id"],
        "url": norm["url"],
        "message": (
            f"{PLATFORM_LABELS.get(platform, platform)} 已识别，"
            "但当前阶段暂不支持分析（仅 B站可用）"
        ),
        "supported_analysis": False,
    }


def _downloaded_file_path(ydl, info: dict[str, Any], out_dir: Path) -> Path:
    """Use downloader-reported final paths before falling back to its template."""
    records = [info]
    requested = info.get("requested_downloads")
    if isinstance(requested, (list, tuple)):
        records.extend(item for item in requested if isinstance(item, dict))

    def existing_path(value):
        if not isinstance(value, (str, os.PathLike)) or not str(value):
            return None
        path = Path(value)
        path = resolve_output_path(path if path.is_absolute() else out_dir / path)
        if not path.is_relative_to(out_dir):
            raise ValueError("Downloaded file must remain inside its output directory")
        return path if path.is_file() else None

    # requested_downloads can contain a postprocessed filepath while top-level
    # filename/_filename still names the pre-processing input.
    for field in ("filepath", "filename", "_filename"):
        for record in records:
            path = existing_path(record.get(field))
            if path is not None:
                return path
    path = existing_path(ydl.prepare_filename(info))
    if path is None:
        raise FileNotFoundError("Downloader did not produce an accessible output file")
    return path


def download_platform_video(
    value: str, cfg: dict[str, Any] | None = None
) -> tuple[str | None, float, float, dict[str, Any]]:
    """借鉴 BiliNote YoutubeDownloader：用 yt_dlp 下载音频（需代理）。

    注意：此为多平台支持保留的下载骨架；当前产品策略下 YouTube / 抖音 / 快手
    的分析未启用，本函数主要供后续扩展或本地调试使用。
    """
    if yt_dlp is None:
        return None, 0.0, 0.0, {"error": "yt_dlp 未安装"}
    try:
        # 配置解析失败也必须保持返回契约，且不能回传带凭据的异常原文。
        proxy = get_proxy(cfg)
        configured_dir = ((cfg or {}).get("video") or {}).get("download_dir")
        out_dir = resolve_output_path(configured_dir, DATA_DIR / "downloads")
        out_dir.mkdir(parents=True, exist_ok=True)
        ydl_opts: dict[str, Any] = {
            "format": "bestaudio[ext=m4a]/bestaudio/best",
            "outtmpl": str(out_dir / "%(id)s.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 15,
            "retries": 2,
            "fragment_retries": 2,
            "extractor_retries": 2,
            "logger": _DownloadLogger(),
        }
        if proxy:
            ydl_opts["proxy"] = proxy
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(value, download=True)
            if not isinstance(info, dict):
                raise ValueError("Downloader metadata must be an object")
            video_id = info.get("id")
            title = info.get("title")
            duration = info.get("duration", 0)
            audio_path = str(_downloaded_file_path(ydl, info, out_dir))
            return audio_path, float(duration), 0.0, {"video_id": video_id, "title": title}
    except Exception as exc:
        return None, 0.0, 0.0, {"error": f"Video download failed ({type(exc).__name__})"}


class _DownloadLogger:
    def debug(self, message):
        pass

    def warning(self, message):
        from ..utils.display import log
        log("视频下载器报告警告", "WARN")

    def error(self, message):
        from ..utils.display import log
        log("视频下载器报告错误", "ERROR")


async def resolve_bilibili_short_url_async(url: str, timeout: int = 10) -> str:
    return await asyncio.to_thread(resolve_bilibili_short_url, url, timeout)


async def extract_video_id_async(value: str, platform: str | None = None) -> str:
    return await asyncio.to_thread(extract_video_id, value, platform)


async def extract_bilibili_p_number_async(value: str) -> int | None:
    return await asyncio.to_thread(extract_bilibili_p_number, value)


async def normalize_video_input_async(value: str) -> dict[str, Any]:
    return await asyncio.to_thread(normalize_video_input, value)


async def fetch_platform_metadata_async(value: str, cfg=None, prefer_subtitle=True):
    return await asyncio.to_thread(fetch_platform_metadata, value, cfg, prefer_subtitle)


async def download_platform_video_async(value: str, cfg=None):
    return await asyncio.to_thread(download_platform_video, value, cfg)
