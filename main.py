# astrbot_plugin_bilibili_learning/main.py
"""AstrBot 插件入口。

AstrBot 通过 metadata.yaml 自动识别本插件，无需 @register 装饰器。
插件类 BilibiliLearningBot(Star) 会被 AstrBot 的 PluginManager
扫描并加载（参考 astrbot.core.star.star_manager.PluginManager）。

运行时区分：

- 被 AstrBot 加载：仅 BilibiliLearningBot 会被实例化，if __name__ == "__main__"
  分支不会执行。
- 直接 python main.py 运行：走 _run_cli_main() 进入 CLI 主菜单。
- 测试：tests/test_cli_exit.py 调用 main_module.run_cli()。

设计约束（修复 issue #17）：

1. 插件在 AstrBot 的事件循环中运行，任何 input() / 阻塞主循环的调用都不能出现在
   handler 或 __init__ 里，否则整个 bot 会被卡死。
2. CLI 菜单是同步且交互式的，只在真正的终端（sys.stdin.isatty()）里执行，并且放到
   线程中运行，避免阻塞事件循环。
3. 长时间运行的 AgentBrain.run() 改为后台任务，指令立即返回，并可用 /bili_stop 停止。
4. 异常日志保留类型和调用帧，不输出原响应或异常中的凭据。
"""
from __future__ import annotations

import asyncio
import sys
from importlib import metadata as importlib_metadata

_standalone_entry = not __package__ or __name__ == "__main__"
if not __package__:
    import hashlib
    import importlib.util
    from pathlib import Path

    _root = Path(__file__).resolve().parent
    _package_name = "_bili_learning_" + hashlib.sha256(str(_root).encode()).hexdigest()[:16]
    if _package_name not in sys.modules:
        _spec = importlib.util.spec_from_file_location(
            _package_name, _root / "__init__.py", submodule_search_locations=[str(_root)]
        )
        _package = importlib.util.module_from_spec(_spec)
        sys.modules[_package_name] = _package
        _spec.loader.exec_module(_package)
    __package__ = _package_name

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from . import _runtime

if _standalone_entry:
    _runtime.configure_standalone()

# 插件版本的代码侧唯一来源，必须与 metadata.yaml 的 version 保持一致。
# 其它位置（CLI 横幅、文档、依赖清单）不再硬编码版本号，升级时只改这里和
# metadata.yaml 两处。
PLUGIN_VERSION = "3.1.7"

# AstrBot 核心提供的依赖（插件 requirements.txt 不再声明，见 issues #18/#20）。
# 值依次为 pip 包名（用于提示）与核心 v4.29.1 声明的最低版本。
_CORE_PROVIDED_DEPENDENCIES = {
    "openai": ("openai", "1.78.0"),
    "pydantic": ("pydantic", "2.12.5"),
    "httpx": ("httpx", "0.28.1"),
    "qrcode": ("qrcode", "8.2"),
    "docx": ("python-docx", "1.2.0"),
    "reportlab": ("reportlab", "5.0.0"),
    "PIL": ("pillow", "11.2.1"),
}


def _check_core_dependencies() -> list[str]:
    """校验核心应提供的依赖是否可用且满足最低版本。

    Returns:
        list[str]: 缺失或版本过低依赖的说明列表。
    """
    try:
        from packaging.version import InvalidVersion, Version
    except ImportError:
        return ["packaging（缺失，无法校验依赖版本，请修复 AstrBot 核心依赖）"]

    problems = []
    for module_name, (package_name, minimum) in _CORE_PROVIDED_DEPENDENCIES.items():
        try:
            __import__(module_name)
        except ImportError:
            problems.append(f"{package_name}（缺失，需要 >={minimum}）")
            continue

        dist_name = package_name.split("[")[0]
        try:
            installed = importlib_metadata.version(dist_name)
        except importlib_metadata.PackageNotFoundError:
            problems.append(f"{package_name}（无法确定版本，需要 >={minimum}）")
            continue
        try:
            if Version(installed) < Version(minimum):
                problems.append(f"{package_name}（已安装 {installed}，需要 >={minimum}）")
        except InvalidVersion:
            problems.append(f"{package_name}（无法解析版本 {installed!r}，需要 >={minimum}）")

    # SOCKS 代理是可选功能，不能阻止直连或 HTTP 代理用户启动。
    try:
        __import__("socksio")
    except ImportError:
        logger.warning(
            "⚠️ 可选依赖 socksio 缺失，SOCKS 代理不可用；直连和 HTTP 代理不受影响。"
            "如需使用 SOCKS 代理，请在 AstrBot 运行环境中安装 httpx[socks]。"
        )

    return problems


_missing_core_deps = _check_core_dependencies()
if _missing_core_deps:
    logger.error(
        "⚠️ AstrBot 核心提供的依赖缺失或版本过低: "
        + "; ".join(_missing_core_deps)
        + "。插件已不再声明这些包，请将 AstrBot 升级至 4.29.1 及以上后重新安装插件；"
          "修复后可直接重新发送 /bili_start，无需重启 AstrBot。"
    )


def _cli_app():
    """延迟导入 CLI 模块。

    cli.app 会拉起 bilibili_api / colorama 等较重的依赖。放在模块顶层导入会让插件在
    缺失可选依赖时直接加载失败；延迟到真正用到时再导入，插件本身仍可正常注册指令。

    Returns:
        module: cli.app 模块对象。

    Raises:
        ImportError: CLI 依赖不可用时抛出。
    """
    from .cli import app

    return app


def _is_interactive() -> bool:
    """判断当前进程是否连接到交互式终端。

    Returns:
        bool: stdin 是 TTY 时返回 True，否则返回 False（Docker / 远程会话）。
    """
    if _runtime.plugin_mode:
        return False
    try:
        return bool(sys.stdin and sys.stdin.isatty())
    except (AttributeError, OSError, ValueError):
        return False


def _log_exception(action: str, exc: BaseException) -> None:
    """记录异常类型及安全调用帧，不输出异常正文。

    Args:
        action: 当前执行的动作，用于日志前缀。
        exc: 捕获到的异常。
    """
    from .utils.display import log_exception
    log_exception(f"{action}失败", exc)


# 后台任务的启动结果分三种：ready 事件置位（确认启动）、任务提前退出（明确失败）、
# 超过该时长仍未置位（仍在初始化）。超时只影响命令回复，不会终止任务。
_STARTUP_READY_TIMEOUT = 5.0

# 后台任务在启动阶段退出时，按结束原因给出可执行的下一步；未知原因只说明任务
# 已退出，避免把内部细节或异常正文发给用户（issue #55）。
_STARTUP_FAILURE_REPLIES = {
    "login_failed": (
        "❌ 启动失败：未找到可用的 B站登录凭据（bilibili_cookies.json），后台任务已退出。"
        "请先完成 B站登录，确认插件数据目录下存在该凭据文件，然后重新发送 /bili_start。"
    ),
    "lock_occupied": (
        "❌ 启动失败：已有其他实例持有单实例锁，后台任务已退出。"
        "请先停止该实例后再重新发送 /bili_start。"
    ),
    "crashed": "❌ 启动失败：后台任务在启动阶段异常退出，详情见插件日志。",
    "ended": "❌ 启动失败：后台任务未进入学习循环就结束了，详情见插件日志。",
    "timeout": (
        "⏳ 已在后台启动，但初始化尚未完成，任务已登记。"
        "可用 /bili_status 查看进度，/bili_stop 可停止。"
    ),
}


def _startup_failure_reply(reason: str) -> str:
    """Map an internal startup end reason to a user-facing reply."""
    return _STARTUP_FAILURE_REPLIES.get(
        reason, f"❌ 启动失败：后台任务在启动阶段已退出（{reason}），详情见插件日志。"
    )


class _BrainRuntime:
    """管理同一插件包内共享的任务及其异步收尾。"""

    STARTUP_READY_TIMEOUT = _STARTUP_READY_TIMEOUT

    def __init__(self):
        self.task: asyncio.Task | None = None
        self.brain = None
        self.stopping = False
        self.closed = False
        self.last_end_reason = None
        self.lock = asyncio.Lock()
        self.stop_timeout = 10.0
        self._log_error = _log_exception
        self._log_info = logger.info

    def _on_done(self, task):
        if self.task is not task:
            return
        cleanup = getattr(self.brain, "_cleanup_task", None)
        if cleanup is not None and cleanup is not task and not cleanup.done():
            self.task = cleanup
            cleanup.add_done_callback(self._on_done)
            return
        self.last_end_reason = getattr(self.brain, "_runtime_end_reason", None)
        self.task = None
        self.brain = None
        self.stopping = False
        if task.cancelled():
            self._log_info("当前实例的 B站学习机器人受管任务已结束")
        else:
            exc = task.exception()
            if exc is not None:
                self.last_end_reason = self.last_end_reason or "crashed"
                self._log_error("B站学习机器人后台任务异常退出", exc)

    def start(self, brain):
        self.last_end_reason = None
        self.brain = brain
        self.task = asyncio.create_task(brain.run(), name="bili_learning_brain")
        self.stopping = False
        self.task.add_done_callback(self._on_done)

    async def await_startup(self):
        """Return the startup outcome: reason string, "timeout", or None when ready.

        The Brain sets ``ready_event`` after login and bridge initialization. A
        missing credential or a busy instance lock ends ``run()`` before that, and
        a crash can happen after the first suspension, so waiting for the event is
        the only reliable way to tell "confirmed started" from "failed to start".
        """
        task = self.task
        if task is None:
            return self.last_end_reason or "ended"
        ready = getattr(self.brain, "ready_event", None)
        if ready is None:
            # Test doubles and legacy brains without a ready signal: keep the old
            # behaviour of observing a single event-loop turn.
            await asyncio.sleep(0)
            if self.task is None:
                return self.last_end_reason or "ended"
            if self.task.done():
                self._on_done(self.task)
                return self.last_end_reason or "ended"
            return getattr(self.brain, "_runtime_end_reason", None)
        waiter = asyncio.ensure_future(ready.wait())
        try:
            done, _ = await asyncio.wait(
                {task, waiter},
                timeout=self.STARTUP_READY_TIMEOUT,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            if not waiter.done():
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
        if waiter in done:
            return None
        if task in done:
            self._on_done(task)
            return self.last_end_reason or "ended"
        return getattr(self.brain, "_runtime_end_reason", None) or "timeout"

    async def state(self):
        async with self.lock:
            if self.task is not None and self.task.done():
                self._on_done(self.task)
            if self.task is None:
                return "idle"
            return "stopping" if self.is_stopping() else "running"

    def is_stopping(self):
        return self.stopping or getattr(self.brain, "_closing", False) is True

    async def request_stop(self):
        async with self.lock:
            task = self.task
            if task is not None and task.done():
                self._on_done(task)
                task = self.task
            if task is None:
                return None, False
            if self.is_stopping():
                return task, False
            self.stopping = True
            # Let an already queued run enter its try/finally before cancellation.
            asyncio.get_running_loop().call_soon(task.cancel)
            return task, True

    async def wait(self, task, *, timeout=None):
        # wait_for would cancel the target again on timeout or caller cancellation.
        brain = self.brain if self.task is task else None
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + timeout
        while True:
            remaining = None if deadline is None else max(0, deadline - loop.time())
            done, _ = await asyncio.wait((task,), timeout=remaining)
            if not done:
                return False
            self._on_done(task)
            cleanup = getattr(brain, "_cleanup_task", None)
            if cleanup is None or cleanup is task or cleanup.done():
                return True
            task = cleanup

    async def terminate(self):
        self.closed = True

        async def drain():
            task, _ = await self.request_stop()
            if task is not None:
                await self.wait(task)

        try:
            await drain()
        except asyncio.CancelledError:
            # A second cancellation ends the caller's wait, not the owned work.
            await drain()
            raise


class BilibiliLearningBot(Star):
    # 插件信息注册处：版本只从 metadata.yaml / PLUGIN_VERSION 获取。
    version = PLUGIN_VERSION

    def __init__(self, context: Context, config=None):
        super().__init__(context)
        if not _standalone_entry:
            from astrbot.api.star import StarTools

            _runtime.configure_plugin(StarTools.get_data_dir(_runtime.PLUGIN_NAME))
        self.config = config
        runtime = getattr(_runtime, "brain_runtime", None)
        if runtime is None or runtime.closed:
            runtime = _BrainRuntime()
            _runtime.brain_runtime = runtime
        self._brain_runtime = runtime

    async def terminate(self):
        """Wait for this instance's registered work before the host unloads it."""
        await self._brain_runtime.terminate()

    def _unmanaged_state(self, scope="bili_stop"):
        """Describe why this instance has no managed task, without implying a global stop.

        Args:
            scope: "bili_stop" or "bili_status" selects the command-specific framing.
                Both only inspect this plugin instance and this profile's lock file.

        Only an owned-task cleanup may be reported as stopped (issue #49): another
        AstrBot instance, a standalone CLI entry or an older unmanaged task can still
        be running while this instance has no task handle.
        """
        try:
            from .utils.lock import bot_lock_status

            status = bot_lock_status(clean_stale=False)
            if status["stale"]:
                detail = "当前 profile 的锁由已退出的进程遗留，本命令未删除该锁文件。"
            elif status["locked"]:
                detail = "当前 profile 存在未由当前实例管理的运行或锁占用，本命令无法停止它。"
                if status["pid"] is None:
                    detail += "锁持有者状态未知。"
                detail += "请先从原启动入口停止；若是旧版重载遗留任务，请在维护时重启宿主。"
            else:
                detail = "当前 profile 未发现锁占用，但这不能证明其他入口或进程没有运行。"
        except Exception as exc:
            _log_exception("读取机器人锁状态", exc)
            detail = "当前 profile 的锁状态未知，无法确认其他入口是否仍在运行。"
        reason = ""
        if getattr(self._brain_runtime, "last_end_reason", None) == "login_failed":
            reason = "最近一次受管任务因登录失败已结束，无需再次停止；请检查凭据及插件日志，然后重新启动。"
        if scope == "bili_status":
            return "ℹ️ 当前实例未登记受管任务，机器人主循环不由当前插件实例管理。" + reason + detail
        return (
            "ℹ️ 当前实例未登记受管任务：本命令没有停止任何任务，"
            + reason
            + "也不覆盖其他插件实例、CLI 入口或其他进程。"
            + detail
            + "只有当前实例的受管任务完成清理时才会提示停止成功。"
        )

    def _initialize_business_config(self):
        """Initialize this profile and apply opted-in host settings before business imports."""
        from .core.config import initialize_config

        initialize_config()
        if self.config is not None:
            from .core.astrbot_config_bridge import apply_astrbot_config

            apply_astrbot_config(self.config)

    @staticmethod
    def _run_in_thread(func):
        """在线程中执行阻塞的 CLI 菜单，避免阻塞事件循环。

        Args:
            func: 需要执行的同步函数。

        Returns:
            asyncio.Future: 线程执行结果。
        """
        return asyncio.to_thread(func)

    # ==================== 核心指令 ====================

    @filter.command("bili_start")
    async def start_bot(self, event: AstrMessageEvent):
        """在后台启动 B站学习机器人。"""
        global _missing_core_deps
        # 每次启动前重新检查，而不是只用导入期快照：用户装好依赖后无需重启核心。
        _missing_core_deps = _check_core_dependencies()
        if _missing_core_deps:
            yield event.plain_result(
                "❌ AstrBot 核心提供的依赖缺失或版本过低: "
                + "; ".join(_missing_core_deps)
                + "。请将 AstrBot 升级至 4.29.1 及以上后重新安装插件；"
                  "修复后可直接重新发送 /bili_start，无需重启 AstrBot。"
            )
            return
        runtime = self._brain_runtime
        async with runtime.lock:
            if runtime.task is not None and runtime.task.done():
                runtime._on_done(runtime.task)
            if runtime.closed:
                reply = "⏳ 当前插件实例已进入卸载流程，不能再启动。"
            elif runtime.is_stopping():
                reply = "⏳ 机器人正在停止中，请稍后再启动。"
            elif runtime.task is not None:
                reply = "⚠️ 机器人已在后台运行。发送 /bili_status 查看状态，或发送 /bili_stop 停止。"
            else:
                try:
                    self._initialize_business_config()
                    from .brain.agent_brain import AgentBrain

                    brain = AgentBrain()
                    runtime.start(brain)
                    startup_reason = await runtime.await_startup()
                except Exception as exc:
                    _log_exception("初始化机器人", exc)
                    reply = f"❌ 机器人初始化失败（{type(exc).__name__}，详情见日志）"
                else:
                    if startup_reason:
                        reply = _startup_failure_reply(startup_reason)
                    else:
                        reply = (
                            "✅ 已在后台启动 B站学习机器人，本指令不会阻塞其他对话。"
                            "发送 /bili_stop 可停止。"
                        )
        yield event.plain_result(reply)

    @filter.command("bili_stop")
    async def stop_bot(self, event: AstrMessageEvent):
        """停止后台运行的 B站学习机器人。"""
        runtime = self._brain_runtime
        task, requested = await runtime.request_stop()
        if task is None:
            yield event.plain_result(self._unmanaged_state("bili_stop"))
            return
        if not requested:
            yield event.plain_result("⏳ 机器人正在停止中，请稍候。")
            return

        if await runtime.wait(task, timeout=runtime.stop_timeout):
            yield event.plain_result("✅ 当前实例的 B站学习机器人受管任务已停止。")
        else:
            logger.warning("B站学习机器人尚未完成收尾，保留任务引用与单实例锁")
            yield event.plain_result(
                "⏳ 已请求停止，但机器人仍在收尾。收尾完成前无法重新启动，请稍后再试。"
            )

    @filter.command("bili_config")
    async def show_config(self, event: AstrMessageEvent):
        """提示使用子指令配置。"""
        yield event.plain_result("请使用子指令：/bili_config_set <key> <value>")

    @filter.command("bili_login")
    async def login(self, event: AstrMessageEvent):
        """登录 B站（仅交互式终端可用）。"""
        if not _is_interactive():
            yield event.plain_result(
                "ℹ️ B站登录需要扫码 / 输入验证码，请在服务器终端运行 python main.py "
                "完成登录。"
            )
            return
        try:
            await self._run_in_thread(_cli_app().show_login_menu)
        except Exception as exc:
            _log_exception("B站登录", exc)
            yield event.plain_result(f"❌ 登录失败（{type(exc).__name__}，详情见日志）")
            return
        yield event.plain_result("✅ 登录流程已结束，请查看后台日志确认结果。")

    # ==================== 知识库相关 ====================

    @filter.command("bili_kb_list")
    async def kb_list(self, event: AstrMessageEvent):
        """列出知识库分类与条目数量。"""
        try:
            import os

            from .core.config import KNOWLEDGE_BASE_DIR
            from .knowledge.browse import count_knowledge_categories

            if not os.path.isdir(KNOWLEDGE_BASE_DIR):
                yield event.plain_result("ℹ️ 知识库为空，还没有归档任何视频。")
                return

            categories = [
                name
                for name in sorted(os.listdir(KNOWLEDGE_BASE_DIR))
                if not name.startswith(".")
                and os.path.isdir(os.path.join(KNOWLEDGE_BASE_DIR, name))
            ]
            lines = [f"📚 知识库共 {count_knowledge_categories()} 个分类"]
            for name in categories[:20]:
                category_dir = os.path.join(KNOWLEDGE_BASE_DIR, name)
                count = sum(
                    1
                    for _root, _dirs, files in os.walk(category_dir)
                    for file_name in files
                    if file_name.endswith(".md")
                )
                lines.append(f"- {name}（{count} 篇）")
            if len(categories) > 20:
                lines.append(f"…… 其余 {len(categories) - 20} 个分类请在知识库目录查看")
            yield event.plain_result("\n".join(lines))
        except Exception as exc:
            _log_exception("读取知识库列表", exc)
            yield event.plain_result(f"❌ 读取知识库列表失败（{type(exc).__name__}，详情见日志）")

    @filter.command("bili_kb_revisit")
    async def kb_revisit(self, event: AstrMessageEvent):
        """重温知识库（需要终端确认）。"""
        if not _is_interactive():
            yield event.plain_result("ℹ️ 知识库重温需要交互选择，请在独立 CLI 的知识库菜单中操作。")
            return
        try:
            from .knowledge.revisit import revisit_knowledge_base_menu

            await revisit_knowledge_base_menu()
        except Exception as exc:
            _log_exception("知识库重温", exc)
            yield event.plain_result(f"❌ 知识库重温失败（{type(exc).__name__}，详情见日志）")
            return
        yield event.plain_result("✅ 知识库重温完成")

    @filter.command("bili_kb_organize")
    async def kb_organize(self, event: AstrMessageEvent):
        """整理知识库（需要终端确认）。"""
        if not _is_interactive():
            yield event.plain_result("ℹ️ 知识库整理需要确认变更，请在独立 CLI 的知识库菜单中操作。")
            return
        try:
            from .knowledge.organize import organize_knowledge_base

            await organize_knowledge_base()
        except Exception as exc:
            _log_exception("知识库整理", exc)
            yield event.plain_result(f"❌ 知识库整理失败（{type(exc).__name__}，详情见日志）")
            return
        yield event.plain_result("✅ 知识库整理完成")

    @filter.command("bili_kb_custom")
    async def kb_custom(self, event: AstrMessageEvent):
        """自定义知识管理（仅交互式终端可用）。"""
        if not _is_interactive():
            yield event.plain_result(
                "ℹ️ 自定义知识管理是交互式菜单，请在服务器终端运行 python main.py 后进入该菜单。"
            )
            return
        try:
            from .knowledge.custom import custom_knowledge_menu

            await self._run_in_thread(custom_knowledge_menu)
        except Exception as exc:
            _log_exception("自定义知识管理", exc)
            yield event.plain_result(f"❌ 自定义知识管理失败（{type(exc).__name__}，详情见日志）")
            return
        yield event.plain_result("✅ 自定义知识管理完成")

    # ==================== 视频分析相关 ====================

    @filter.command("bili_analyze")
    async def analyze_video(self, event: AstrMessageEvent, bvid: str = ""):
        """按 BV 号 / 视频链接分析单个视频。

        用法：/bili_analyze [bvid]
        """
        bvid = (bvid or "").strip()
        if not bvid:
            yield event.plain_result(
                "ℹ️ 用法：/bili_analyze <BV号或视频链接>\n例如：/bili_analyze BV1xx411c7mD"
            )
            return

        try:
            self._initialize_business_config()
            from .brain.video_analysis import analyze_bilibili_video_input

            await analyze_bilibili_video_input(bvid)
        except Exception as exc:
            _log_exception("视频分析", exc)
            yield event.plain_result(f"❌ 视频分析失败（{type(exc).__name__}，详情见日志）")
            return
        yield event.plain_result(f"✅ 视频分析完成：{bvid}")

    @filter.command("bili_up_learn")
    async def up_learn(self, event: AstrMessageEvent, up_id: str = ""):
        """学习 UP 主主页视频。

        用法：/bili_up_learn [UP主UID或名字]
        """
        up_id = (up_id or "").strip()
        if not up_id:
            yield event.plain_result(
                "ℹ️ 用法：/bili_up_learn <UP主UID或名字>\n例如：/bili_up_learn 2"
            )
            return

        try:
            self._initialize_business_config()
            from .brain.video_analysis import up_homepage_learn

            # 非交互调用：显式给出默认值，避免函数内部三个 input() 阻塞事件循环
            await up_homepage_learn(
                query=up_id,
                limit=10,
                skip_without_subtitle=True,
                min_score=7.0,
            )
        except Exception as exc:
            _log_exception("UP主学习", exc)
            yield event.plain_result(f"❌ UP主学习失败（{type(exc).__name__}，详情见日志）")
            return
        yield event.plain_result(f"✅ UP主学习完成：{up_id}")

    @filter.command("bili_video2html")
    async def video_to_html(self, event: AstrMessageEvent, bvid: str = ""):
        """把视频转换为 HTML 学习页面。

        用法：/bili_video2html [bvid]
        """
        try:
            bvid = (bvid or "").strip()
            if not bvid:
                yield event.plain_result(
                    "ℹ️ 用法：/bili_video2html <bv号/链接>\n例如：/bili_video2html BV1xx411c7mD"
                )
                return
            self._initialize_business_config()
            from .cli.app import video_to_html_bg

            await video_to_html_bg(query=bvid)
        except Exception as exc:
            _log_exception("视频转HTML", exc)
            yield event.plain_result(f"❌ 视频转HTML失败（{type(exc).__name__}，详情见日志）")
            return
        yield event.plain_result("✅ 视频转HTML完成")

    # ==================== 开关控制 ====================

    async def _toggle(
        self,
        event: AstrMessageEvent,
        attr: str,
        config_key: str,
        label: str,
    ):
        """切换一个布尔开关并持久化配置。

        Args:
            event: 消息事件。
            attr: cli.app 中的全局开关名。
            config_key: 配置文件中的键路径（以 . 分隔）。
            label: 用于回复文案的名称。
        """
        try:
            app = _cli_app()
            new_value = not getattr(app, attr)
            setattr(app, attr, new_value)
            section, _, key = config_key.partition(".")
            app.config.setdefault(section, {})[key] = new_value
            if not app.save_config(app.config):
                yield event.plain_result("❌ 配置保存失败")
                return
            app._reload_all_globals(app.config)
            state = "已开启" if new_value else "已关闭"
            yield event.plain_result(f"✅ {label}: {state}")
        except Exception as exc:
            _log_exception(f"切换{label}", exc)
            yield event.plain_result(f"❌ 切换{label}失败（{type(exc).__name__}，详情见日志）")

    @filter.command("bili_asr")
    async def toggle_asr(self, event: AstrMessageEvent):
        """切换 ASR 语音识别开关。"""
        async for result in self._toggle(
            event, "ASR_ENABLED", "asr.enabled", "ASR语音识别"
        ):
            yield result

    @filter.command("bili_quiet")
    async def toggle_quiet(self, event: AstrMessageEvent):
        """切换安静模式。"""
        async for result in self._toggle(
            event, "QUIET_MODE", "system.quiet_mode", "安静模式"
        ):
            yield result

    @filter.command("bili_vision")
    async def toggle_vision(self, event: AstrMessageEvent):
        """切换封面分析开关。"""
        async for result in self._toggle(
            event, "VISION_COVER_ENABLED", "vision.cover_enabled", "封面分析"
        ):
            yield result

    @filter.command("bili_fast")
    async def toggle_fast(self, event: AstrMessageEvent):
        """切换快速模式（跳过延迟）。"""
        try:
            app = _cli_app()
            new_value = not app.config.get("speed", {}).get("no_human_delay", False)
            app.config.setdefault("speed", {})["no_human_delay"] = new_value
            if not app.save_config(app.config):
                yield event.plain_result("❌ 配置保存失败")
                return
            app._reload_all_globals(app.config)
            state = "⚡ 已开启 (跳过延迟)" if new_value else "🐢 已关闭 (模拟真人)"
            yield event.plain_result(f"✅ 快速模式: {state}")
        except Exception as exc:
            _log_exception("切换快速模式", exc)
            yield event.plain_result(f"❌ 切换快速模式失败（{type(exc).__name__}，详情见日志）")

    # ==================== 其他功能 ====================

    @filter.command("bili_interest")
    async def interest(self, event: AstrMessageEvent):
        """兴趣设置（仅交互式终端可用）。"""
        if not _is_interactive():
            yield event.plain_result(
                "ℹ️ 兴趣设置是交互式菜单，请在服务器终端运行 python main.py "
                "修改兴趣关键词。"
            )
            return
        try:
            await self._run_in_thread(_cli_app().show_interest_menu)
        except Exception as exc:
            _log_exception("兴趣设置", exc)
            yield event.plain_result(f"❌ 兴趣设置失败（{type(exc).__name__}，详情见日志）")
            return
        yield event.plain_result("✅ 兴趣设置流程已结束，请查看后台日志确认结果。")

    @filter.command("bili_comment")
    async def comment(self, event: AstrMessageEvent):
        """评论设置（仅交互式终端可用）。"""
        if not _is_interactive():
            yield event.plain_result(
                "ℹ️ 评论设置是交互式菜单，请在服务器终端运行 python main.py "
                "修改评论相关配置。"
            )
            return
        try:
            await self._run_in_thread(_cli_app().show_comment_menu)
        except Exception as exc:
            _log_exception("评论设置", exc)
            yield event.plain_result(f"❌ 评论设置失败（{type(exc).__name__}，详情见日志）")
            return
        yield event.plain_result("✅ 评论设置流程已结束，请查看后台日志确认结果。")

    @filter.command("bili_export")
    async def export(self, event: AstrMessageEvent):
        """导出配置（仅交互式终端可用）。"""
        if not _is_interactive():
            yield event.plain_result(
                "ℹ️ 导出配置是交互式流程，请在服务器终端运行 python main.py 后使用导出功能。"
            )
            return
        try:
            await self._run_in_thread(_cli_app().export_config)
        except Exception as exc:
            _log_exception("导出配置", exc)
            yield event.plain_result(f"❌ 导出配置失败（{type(exc).__name__}，详情见日志）")
            return
        yield event.plain_result("✅ 配置已导出")

    @filter.command("bili_import")
    async def import_cfg(self, event: AstrMessageEvent):
        """导入配置（仅交互式终端可用）。"""
        if not _is_interactive():
            yield event.plain_result(
                "ℹ️ 导入配置是交互式流程，请在服务器终端运行 python main.py 后使用导入功能。"
            )
            return
        try:
            await self._run_in_thread(_cli_app().import_config)
        except Exception as exc:
            _log_exception("导入配置", exc)
            yield event.plain_result(f"❌ 导入配置失败（{type(exc).__name__}，详情见日志）")
            return
        yield event.plain_result("✅ 配置已导入")

    @filter.command("bili_reset")
    async def reset(self, event: AstrMessageEvent):
        """恢复出厂设置（仅交互式终端可用）。"""
        if not _is_interactive():
            yield event.plain_result(
                "ℹ️ 恢复出厂设置会逐个确认删除项，必须在服务器终端运行 python main.py 后操作。"
            )
            return
        try:
            await self._run_in_thread(_cli_app().factory_reset_all)
        except Exception as exc:
            _log_exception("恢复出厂设置", exc)
            yield event.plain_result(f"❌ 恢复出厂设置失败（{type(exc).__name__}，详情见日志）")
            return
        yield event.plain_result("✅ 已恢复出厂设置")

    @filter.command("bili_status")
    async def status(self, event: AstrMessageEvent):
        """查看后台任务状态。"""
        state = await self._brain_runtime.state()
        if state == "idle":
            yield event.plain_result(self._unmanaged_state("bili_status"))
        elif state == "stopping":
            yield event.plain_result("机器人主循环: 停止中，正在等待当前实例的受管任务收尾。")
        else:
            yield event.plain_result("机器人主循环: 运行中，由当前插件实例管理。")


def _run_cli_main() -> None:
    """CLI 入口，供 python main.py 直接调用以及 tests/test_cli_exit.py 注入测试。

    AstrBot 通过 metadata.yaml + class Plugin(Star) 自动识别插件，不依赖 @register
    装饰器（@register_star 在上游 astrbot/core/star/register/star.py 已标记为 deprecated）。
    因此 main.py 在被 AstrBot 加载时仅需导出 class BilibiliLearningBot(Star)；
    本函数仅用于 CLI / 桌面 / 测试场景。
    """
    if _runtime.plugin_mode:
        raise RuntimeError("Interactive CLI is not available inside the AstrBot plugin")
    if "--migrate-data" in sys.argv[1:]:
        import argparse

        parser = argparse.ArgumentParser(description="单向导入明确选定的旧数据目录")
        parser.add_argument("--migrate-data", required=True, metavar="SOURCE")
        args = parser.parse_args()
        try:
            from .core.user_data import migrate_legacy_data
            from .utils.display import log

            copied = migrate_legacy_data(args.migrate_data)
            log(f"旧数据导入完成，新增 {len(copied)} 个文件；未覆盖目标或删除源文件", "INFO")
        except Exception as exc:
            _log_exception("旧数据导入", exc)
            raise SystemExit(1) from None
        return

    # 与 CLI 入口行为保持一致：先打印免责声明，再走主菜单。
    # 非 TTY（管道/重定向/Docker）下读 stdin 会阻塞或直接 EOF，跳过确认。
    if _is_interactive():
        try:
            _cli_app()._disclaimer_confirm()
        except Exception:
            pass

    while True:
        try:
            _cli_app().show_main_menu()
        except (KeyboardInterrupt, EOFError):
            print("\n[EXIT] 已取消，程序已退出")
            return
        except Exception as exc:
            _log_exception("主菜单运行", exc)
            return


# 兼容旧测试导入：tests/test_cli_exit.py 使用 main_module.run_cli()。
run_cli = _run_cli_main


if __name__ == "__main__":
    _run_cli_main()
