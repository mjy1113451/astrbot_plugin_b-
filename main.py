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
4. 异常日志统一附带 traceback，便于排查。
"""
from __future__ import annotations

import asyncio
import sys
import traceback
from importlib import metadata as importlib_metadata

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

# 插件版本的代码侧唯一来源，必须与 metadata.yaml 的 version 保持一致。
# 其它位置（CLI 横幅、文档、依赖清单）不再硬编码版本号，升级时只改这里和
# metadata.yaml 两处。
PLUGIN_VERSION = "3.1.6"

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

_brain_task: asyncio.Task | None = None
"""后台运行的 AgentBrain 任务，None 表示当前没有任务。"""

_brain_stopping = False
"""停止流程进行中标记，避免并发 stop 重复取消任务或误报“已停止”。"""

_brain_task_lock = asyncio.Lock()
"""串行化 start/stop 对 _brain_task 的状态切换，避免并发指令下引用被写乱。"""


def _cli_app():
    """延迟导入 CLI 模块。

    cli.app 会拉起 bilibili_api / colorama 等较重的依赖。放在模块顶层导入会让插件在
    缺失可选依赖时直接加载失败；延迟到真正用到时再导入，插件本身仍可正常注册指令。

    Returns:
        module: cli.app 模块对象。

    Raises:
        ImportError: CLI 依赖不可用时抛出。
    """
    import cli.app as app

    return app


def _is_interactive() -> bool:
    """判断当前进程是否连接到交互式终端。

    Returns:
        bool: stdin 是 TTY 时返回 True，否则返回 False（Docker / 远程会话）。
    """
    try:
        return bool(sys.stdin and sys.stdin.isatty())
    except (AttributeError, OSError, ValueError):
        return False


def _log_exception(action: str, exc: BaseException) -> None:
    """记录异常并附带完整堆栈。

    Args:
        action: 当前执行的动作，用于日志前缀。
        exc: 捕获到的异常。
    """
    logger.error(f"{action}失败: {exc}\n{traceback.format_exc()}")


def _on_brain_done(task: asyncio.Task) -> None:
    """后台任务结束时的回调：清理引用并记录异常。

    Args:
        task: 已结束的后台任务。
    """
    global _brain_task, _brain_stopping
    is_current = _brain_task is task
    if is_current:
        _brain_task = None
        _brain_stopping = False
    # 单实例锁只在当前任务真正结束时释放：避免过期回调误释放已被新任务持有的锁，
    # 也避免 stop 超时后锁被提前释放导致多实例竞争
    if is_current:
        try:
            _cli_app()._release_bot_lock()
        except Exception as exc:
            _log_exception("释放机器人锁", exc)
    if task.cancelled():
        logger.info("B站学习机器人已被停止")
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            f"B站学习机器人后台任务异常退出: {exc}\n"
            + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        )


class BilibiliLearningBot(Star):
    # 插件信息注册处：版本只从 metadata.yaml / PLUGIN_VERSION 获取。
    version = PLUGIN_VERSION

    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config

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
        global _brain_task, _missing_core_deps
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
        async with _brain_task_lock:
            if _brain_stopping:
                yield event.plain_result("⏳ 机器人正在停止中，请稍后再启动。")
                return
            if _brain_task is not None and not _brain_task.done():
                yield event.plain_result(
                    "⚠️ 机器人已在后台运行。发送 /bili_status 查看状态，或发送 /bili_stop 停止。"
                )
                return

            try:
                if self.config is not None:
                    from core.astrbot_config_bridge import apply_astrbot_config

                    apply_astrbot_config(self.config)
                from brain.agent_brain import AgentBrain

                brain = AgentBrain()
            except Exception as exc:
                _log_exception("初始化机器人", exc)
                yield event.plain_result(f"❌ 机器人初始化失败: {exc}")
                return

            _brain_task = asyncio.create_task(brain.run(), name="bili_learning_brain")
            _brain_task.add_done_callback(_on_brain_done)
        yield event.plain_result(
            "✅ 已在后台启动 B站学习机器人，本指令不会阻塞其他对话。"
            "发送 /bili_stop 可停止。"
        )

    @filter.command("bili_stop")
    async def stop_bot(self, event: AstrMessageEvent):
        """停止后台运行的 B站学习机器人。"""
        global _brain_stopping
        reply = ""
        async with _brain_task_lock:
            task = _brain_task
            if task is None or task.done():
                reply = "ℹ️ 当前没有正在运行的机器人。"
            elif _brain_stopping:
                reply = "⏳ 机器人正在停止中，请稍候。"
            else:
                # 在锁内置位“停止中”并取消，保证并发 stop 只有一个进入等待流程
                _brain_stopping = True
                task.cancel()

        if reply:
            yield event.plain_result(reply)
            return

        # 等任务真正退出（含 AgentBrain 自身的收尾）。锁由 _on_brain_done
        # 在任务结束时统一释放；超时则保留任务引用与锁，避免误报"已停止"
        stopped = False
        try:
            await asyncio.wait_for(task, timeout=10.0)
            stopped = True
        except asyncio.CancelledError:
            # 只有任务自身被取消才算停止；本协程被取消时继续向上传播
            if not task.cancelled():
                raise
            stopped = True
        except asyncio.TimeoutError:
            logger.warning("B站学习机器人 10 秒内未完成收尾，保留任务引用与单实例锁")
        except Exception as exc:
            _log_exception("等待机器人停止", exc)

        # 仅在实际观察到任务结束（done）时才报告“已停止”
        if stopped or task.done():
            yield event.plain_result("✅ 已停止 B站学习机器人。")
        else:
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
            yield event.plain_result(f"❌ 登录失败: {exc}")
            return
        yield event.plain_result("✅ 登录流程已结束，请查看后台日志确认结果。")

    # ==================== 知识库相关 ====================

    @filter.command("bili_kb_list")
    async def kb_list(self, event: AstrMessageEvent):
        """列出知识库分类与条目数量。"""
        try:
            import os

            from core.config import KNOWLEDGE_BASE_DIR
            from knowledge.browse import count_knowledge_categories

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
            yield event.plain_result(f"❌ 读取知识库列表失败: {exc}")

    @filter.command("bili_kb_revisit")
    async def kb_revisit(self, event: AstrMessageEvent):
        """重温知识库。"""
        try:
            from knowledge.revisit import revisit_knowledge_base_menu

            await revisit_knowledge_base_menu()
        except Exception as exc:
            _log_exception("知识库重温", exc)
            yield event.plain_result(f"❌ 知识库重温失败: {exc}")
            return
        yield event.plain_result("✅ 知识库重温完成")

    @filter.command("bili_kb_organize")
    async def kb_organize(self, event: AstrMessageEvent):
        """整理知识库。"""
        try:
            from knowledge.organize import organize_knowledge_base

            await organize_knowledge_base()
        except Exception as exc:
            _log_exception("知识库整理", exc)
            yield event.plain_result(f"❌ 知识库整理失败: {exc}")
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
            from knowledge.custom import custom_knowledge_menu

            await self._run_in_thread(custom_knowledge_menu)
        except Exception as exc:
            _log_exception("自定义知识管理", exc)
            yield event.plain_result(f"❌ 自定义知识管理失败: {exc}")
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
            from brain.video_analysis import analyze_bilibili_video_input

            await analyze_bilibili_video_input(bvid)
        except Exception as exc:
            _log_exception("视频分析", exc)
            yield event.plain_result(f"❌ 视频分析失败: {exc}")
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
            from brain.video_analysis import up_homepage_learn

            # 非交互调用：显式给出默认值，避免函数内部三个 input() 阻塞事件循环
            await up_homepage_learn(
                query=up_id,
                limit=10,
                skip_without_subtitle=True,
                min_score=7.0,
            )
        except Exception as exc:
            _log_exception("UP主学习", exc)
            yield event.plain_result(f"❌ UP主学习失败: {exc}")
            return
        yield event.plain_result(f"✅ UP主学习完成：{up_id}")

    @filter.command("bili_video2html")
    async def video_to_html(self, event: AstrMessageEvent, bvid: str = ""):
        """把视频转换为 HTML 学习页面。

        用法：/bili_video2html [bvid]
        """
        try:
            from cli.app import video_to_html_bg

            bvid = (bvid or "").strip()
            if not bvid:
                yield event.plain_result(
                    "ℹ️ 用法：/bili_video2html <bv号/链接>\n例如：/bili_video2html BV1xx411c7mD"
                )
                return
            await video_to_html_bg(query=bvid)
        except Exception as exc:
            _log_exception("视频转HTML", exc)
            yield event.plain_result(f"❌ 视频转HTML失败: {exc}")
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
            yield event.plain_result(f"❌ 切换{label}失败: {exc}")

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
            yield event.plain_result(f"❌ 切换快速模式失败: {exc}")

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
            yield event.plain_result(f"❌ 兴趣设置失败: {exc}")
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
            yield event.plain_result(f"❌ 评论设置失败: {exc}")
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
            yield event.plain_result(f"❌ 导出配置失败: {exc}")
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
            yield event.plain_result(f"❌ 导入配置失败: {exc}")
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
            yield event.plain_result(f"❌ 恢复出厂设置失败: {exc}")
            return
        yield event.plain_result("✅ 已恢复出厂设置")

    @filter.command("bili_status")
    async def status(self, event: AstrMessageEvent):
        """查看后台任务状态。"""
        try:
            app = _cli_app()
            app._show_bg_tasks()
        except Exception as exc:
            _log_exception("查看后台任务", exc)
            yield event.plain_result(f"❌ 查看后台任务失败: {exc}")
            return
        state = "运行中" if _brain_task and not _brain_task.done() else "未运行"
        yield event.plain_result(f"机器人主循环: {state}，后台任务详情见日志。")


def _run_cli_main() -> None:
    """CLI 入口，供 python main.py 直接调用以及 tests/test_cli_exit.py 注入测试。

    AstrBot 通过 metadata.yaml + class Plugin(Star) 自动识别插件，不依赖 @register
    装饰器（@register_star 在上游 astrbot/core/star/register/star.py 已标记为 deprecated）。
    因此 main.py 在被 AstrBot 加载时仅需导出 class BilibiliLearningBot(Star)；
    本函数仅用于 CLI / 桌面 / 测试场景。
    """
    import logging as _logging

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
        except Exception as _exc:
            _logging.getLogger(__name__).exception("主菜单运行异常: %s", _exc)
            print(f"[ERROR] 主菜单运行异常: {_exc}")
            return


# 兼容旧测试导入：tests/test_cli_exit.py 使用 main_module.run_cli()。
run_cli = _run_cli_main


if __name__ == "__main__":
    _run_cli_main()
