# astrbot_plugin_bilibili_learning/main.py
import asyncio
import os
import traceback

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger

# 导入原有模块（路径按实际情况调整）
from cli.app import (
    _disclaimer_confirm, show_main_menu, show_mood_menu, show_config_menu,
    show_login_menu, show_knowledge_base_menu, show_interest_menu,
    show_comment_menu, show_private_message_menu, show_diary_evolution_menu,
    show_agent_skill_menu, show_up_danmaku_menu, _configure_asr_settings,
    _configure_dry_goods_settings, _configure_standby_settings,
    _configure_video_interval_settings,
    show_knowledge_tutor_menu,
    show_search_history, show_reply_safety_menu,
    factory_reset_all, export_config, import_config, _reload_all_globals,
    save_config, config,
    SUBTITLE_STRICT_CHECK,
    _release_bot_lock,
    _show_bg_tasks,
    video_to_html_bg,
    show_interest_prefs_menu,
    show_coin_settings_menu,
)
from brain.agent_brain import AgentBrain
from brain.video_analysis import manual_video_analysis, up_homepage_learn
from knowledge.revisit import revisit_knowledge_base_menu
from knowledge.custom import custom_knowledge_menu
from knowledge.organize import organize_knowledge_base


class BilibiliLearningBot(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        # Windows 事件循环策略
        if os.name == 'nt':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        # 免责确认（可改为首次使用时触发）
        _disclaimer_confirm()

    # ==================== 核心指令 ====================

    @filter.command("bili_start")
    async def start_bot(self, event: AstrMessageEvent):
        """启动 B站学习机器人"""
        try:
            await AgentBrain().run()
        except KeyboardInterrupt:
            logger.info("机器人被用户中断")
        except Exception as e:
            logger.error(f"机器人运行异常: {e}\n{traceback.format_exc()}")
        finally:
            _release_bot_lock()
        yield event.plain_result("✅ 机器人已停止")

    @filter.command("bili_config")
    async def show_config(self, event: AstrMessageEvent):
        """显示配置菜单（由于是交互式菜单，建议拆分为子指令）"""
        # 原 show_config_menu() 是交互式的，不适合直接调用
        # 建议拆分为 /bili_config_set <key> <value> 等形式
        yield event.plain_result("请使用子指令：/bili_config_set <key> <value>")

    @filter.command("bili_login")
    async def login(self, event: AstrMessageEvent):
        """登录 B站"""
        show_login_menu()  # 交互式，需改造
        yield event.plain_result("请查看后台日志完成登录")

    # ==================== 知识库相关 ====================

    @filter.command("bili_kb_list")
    async def kb_list(self, event: AstrMessageEvent):
        """列出知识库内容"""
        # 原 show_knowledge_base_menu() 是交互式的
        yield event.plain_result("请使用 /bili_kb_add /bili_kb_del 等子指令")

    @filter.command("bili_kb_revisit")
    async def kb_revisit(self, event: AstrMessageEvent):
        """重温知识库"""
        try:
            await revisit_knowledge_base_menu()
        except Exception as e:
            logger.error(f"知识库重温异常: {e}")
        yield event.plain_result("✅ 知识库重温完成")

    @filter.command("bili_kb_organize")
    async def kb_organize(self, event: AstrMessageEvent):
        """整理知识库"""
        try:
            await organize_knowledge_base()
        except Exception as e:
            logger.error(f"知识库整理异常: {e}")
        yield event.plain_result("✅ 知识库整理完成")

    @filter.command("bili_kb_custom")
    async def kb_custom(self, event: AstrMessageEvent):
        """自定义知识管理"""
        try:
            await custom_knowledge_menu()
        except Exception as e:
            logger.error(f"自定义知识管理异常: {e}")
        yield event.plain_result("✅ 自定义知识管理完成")

    # ==================== 视频分析相关 ====================

    @filter.command("bili_analyze")
    async def analyze_video(self, event: AstrMessageEvent, bvid: str = None):
        """手动分析视频
        用法：/bili_analyze [bvid]
        """
        try:
            if bvid:
                # 可扩展为指定 BVID 分析
                pass
            await manual_video_analysis()
        except Exception as e:
            logger.error(f"视频分析异常: {e}")
        yield event.plain_result("✅ 视频分析完成")

    @filter.command("bili_up_learn")
    async def up_learn(self, event: AstrMessageEvent, up_id: str = None):
        """学习 UP 主主页
        用法：/bili_up_learn [up_id]
        """
        try:
            await up_homepage_learn()
        except Exception as e:
            logger.error(f"UP主学习异常: {e}")
        yield event.plain_result("✅ UP主学习完成")

    @filter.command("bili_video2html")
    async def video_to_html(self, event: AstrMessageEvent):
        """视频转 HTML"""
        try:
            await video_to_html_bg()
        except Exception as e:
            logger.error(f"视频转HTML异常: {e}")
        yield event.plain_result("✅ 视频转HTML完成")

    # ==================== 开关控制 ====================

    @filter.command("bili_asr")
    async def toggle_asr(self, event: AstrMessageEvent):
        """切换 ASR 语音识别开关"""
        import cli.app as _app_mod
        _app_mod.ASR_ENABLED = not _app_mod.ASR_ENABLED
        config.setdefault("asr", {})["enabled"] = _app_mod.ASR_ENABLED
        if save_config(config):
            _reload_all_globals(config)
            state = "已开启" if _app_mod.ASR_ENABLED else "已关闭"
            yield event.plain_result(f"✅ ASR语音识别: {state}")
        else:
            yield event.plain_result("❌ 配置保存失败")

    @filter.command("bili_quiet")
    async def toggle_quiet(self, event: AstrMessageEvent):
        """切换安静模式"""
        import cli.app as _app_mod
        _app_mod.QUIET_MODE = not _app_mod.QUIET_MODE
        config.setdefault("system", {})["quiet_mode"] = _app_mod.QUIET_MODE
        if save_config(config):
            _reload_all_globals(config)
            state = "🔇 已开启 (精简日志)" if _app_mod.QUIET_MODE else "📢 已关闭 (完整日志)"
            yield event.plain_result(f"✅ 安静模式: {state}")
        else:
            yield event.plain_result("❌ 配置保存失败")

    @filter.command("bili_fast")
    async def toggle_fast(self, event: AstrMessageEvent):
        """切换快速模式（跳过延迟）"""
        no_human_delay = not config.get("speed", {}).get("no_human_delay", False)
        config.setdefault("speed", {})["no_human_delay"] = no_human_delay
        if save_config(config):
            _reload_all_globals(config)
            state = "⚡ 已开启 (跳过延迟)" if no_human_delay else "🐢 已关闭 (模拟真人)"
            yield event.plain_result(f"✅ 快速模式: {state}")
        else:
            yield event.plain_result("❌ 配置保存失败")

    @filter.command("bili_vision")
    async def toggle_vision(self, event: AstrMessageEvent):
        """切换封面分析开关"""
        import cli.app as _app_mod
        _app_mod.VISION_COVER_ENABLED = not _app_mod.VISION_COVER_ENABLED
        config.setdefault("vision", {})["cover_enabled"] = _app_mod.VISION_COVER_ENABLED
        if save_config(config):
            _reload_all_globals(config)
            state = "已开启" if _app_mod.VISION_COVER_ENABLED else "已关闭(刷视频更快)"
            yield event.plain_result(f"✅ 封面分析: {state}")
        else:
            yield event.plain_result("❌ 配置保存失败")

    # ==================== 其他功能 ====================

    @filter.command("bili_interest")
    async def interest(self, event: AstrMessageEvent):
        """兴趣设置"""
        show_interest_menu()  # 交互式，需改造
        yield event.plain_result("请查看后台日志完成兴趣设置")

    @filter.command("bili_comment")
    async def comment(self, event: AstrMessageEvent):
        """评论设置"""
        show_comment_menu()
        yield event.plain_result("请查看后台日志完成评论设置")

    @filter.command("bili_export")
    async def export(self, event: AstrMessageEvent):
        """导出配置"""
        export_config()
        yield event.plain_result("✅ 配置已导出")

    @filter.command("bili_import")
    async def import_cfg(self, event: AstrMessageEvent):
        """导入配置"""
        import_config()
        yield event.plain_result("✅ 配置已导入")

    @filter.command("bili_reset")
    async def reset(self, event: AstrMessageEvent):
        """恢复出厂设置"""
        factory_reset_all()
        yield event.plain_result("✅ 已恢复出厂设置")

    @filter.command("bili_status")
    async def status(self, event: AstrMessageEvent):
        """查看后台任务状态"""
        try:
            _show_bg_tasks()
        except Exception as e:
            logger.error(f"查看后台任务异常: {e}")
        yield event.plain_result("请查看后台日志")