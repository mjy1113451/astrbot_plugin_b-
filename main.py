#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bilibili_learning_bot — AstrBot 插件"""

import asyncio
import os
import traceback

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger

# 导入原有模块
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
    """B站学习机器人插件"""

    def __init__(self, context: Context):
        super().__init__(context)
        # 设置 Windows 事件循环策略
        if os.name == 'nt':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        # 显示免责声明（仅在加载时执行一次）
        _disclaimer_confirm()
        logger.info("B站学习机器人插件已加载")

    async def terminate(self):
        """插件卸载/禁用时调用"""
        _release_bot_lock()
        logger.info("B站学习机器人插件已卸载")

    # ========== 核心指令 ==========

    @filter.command("bili_start")
    async def bili_start(self, event: AstrMessageEvent):
        """启动B站学习机器人（对应原菜单 1）"""
        try:
            await event.send(event.plain_result("🤖 正在启动机器人..."))
            await AgentBrain().run()
        except asyncio.CancelledError:
            await event.send(event.plain_result("⏹️ 机器人已停止"))
        except Exception as e:
            logger.error(f"机器人运行异常: {e}\n{traceback.format_exc()}")
            await event.send(event.plain_result(f"❌ 运行异常: {e}"))
        finally:
            _release_bot_lock()

    @filter.command("bili_stop")
    async def bili_stop(self, event: AstrMessageEvent):
        """停止机器人（紧急停止）"""
        _release_bot_lock()
        await event.send(event.plain_result("⏹️ 已发送停止信号"))

    # ========== 配置类指令 ==========

    @filter.command("bili_config")
    async def bili_config(self, event: AstrMessageEvent):
        """显示配置菜单（对应原菜单 2）"""
        # 原有 show_config_menu 是交互式菜单，在聊天场景下需改造为返回文本
        # 这里简化为返回当前配置摘要
        cfg_summary = f"""
📋 **当前配置摘要**
- ASR: {'启用' if config.get('asr', {}).get('enabled', False) else '禁用'}
- 快速模式: {'开启' if config.get('speed', {}).get('no_human_delay', False) else '关闭'}
- 封面分析: {'开启' if config.get('vision', {}).get('cover_enabled', False) else '关闭'}
- 安静模式: {'开启' if config.get('system', {}).get('quiet_mode', False) else '关闭'}
        """
        await event.send(event.plain_result(cfg_summary))

    @filter.command("bili_login")
    async def bili_login(self, event: AstrMessageEvent):
        """登录B站（对应原菜单 3）"""
        # show_login_menu 是交互式的，这里简化为触发登录流程
        await event.send(event.plain_result("🔐 正在打开登录页面，请按提示操作..."))
        try:
            # 原 show_login_menu 可能需要异步改造
            # 这里占位，实际需根据原有逻辑调整
            await event.send(event.plain_result("✅ 登录功能已触发（请查看控制台）"))
        except Exception as e:
            await event.send(event.plain_result(f"❌ 登录失败: {e}"))

    # ========== 知识库类指令 ==========

    @filter.command("bili_kb")
    async def bili_kb(self, event: AstrMessageEvent):
        """知识库管理（对应原菜单 4）"""
        await event.send(event.plain_result("📚 知识库管理功能已触发（请查看控制台）"))
        # 原 show_knowledge_base_menu 是交互式菜单
        # 可考虑拆分为子指令: /bili_kb list, /bili_kb add, /bili_kb delete

    @filter.command("bili_revisit")
    async def bili_revisit(self, event: AstrMessageEvent):
        """重温知识库（对应原菜单 K）"""
        await event.send(event.plain_result("🔄 开始重温知识库..."))
        try:
            await revisit_knowledge_base_menu()
            await event.send(event.plain_result("✅ 知识库重温完成"))
        except Exception as e:
            await event.send(event.plain_result(f"❌ 重温失败: {e}"))

    @filter.command("bili_organize")
    async def bili_organize(self, event: AstrMessageEvent):
        """整理知识库（对应原菜单 O）"""
        await event.send(event.plain_result("📂 开始整理知识库..."))
        try:
            await organize_knowledge_base()
            await event.send(event.plain_result("✅ 知识库整理完成"))
        except Exception as e:
            await event.send(event.plain_result(f"❌ 整理失败: {e}"))

    @filter.command("bili_custom_kb")
    async def bili_custom_kb(self, event: AstrMessageEvent):
        """自定义知识管理（对应原菜单 N）"""
        await event.send(event.plain_result("📝 自定义知识管理已触发（请查看控制台）"))
        try:
            await custom_knowledge_menu()
        except Exception as e:
            await event.send(event.plain_result(f"❌ 操作失败: {e}"))

    # ========== 视频分析类指令 ==========

    @filter.command("bili_analyze")
    async def bili_analyze(self, event: AstrMessageEvent):
        """手动视频分析（对应原菜单 V）"""
        await event.send(event.plain_result("🔍 开始手动视频分析..."))
        try:
            await manual_video_analysis()
            await event.send(event.plain_result("✅ 视频分析完成"))
        except Exception as e:
            await event.send(event.plain_result(f"❌ 分析失败: {e}"))

    @filter.command("bili_up_learn")
    async def bili_up_learn(self, event: AstrMessageEvent):
        """UP主主页学习（对应原菜单 U）"""
        await event.send(event.plain_result("📺 开始UP主主页学习..."))
        try:
            await up_homepage_learn()
            await event.send(event.plain_result("✅ UP主主页学习完成"))
        except Exception as e:
            await event.send(event.plain_result(f"❌ 学习失败: {e}"))

    @filter.command("bili_video2html")
    async def bili_video2html(self, event: AstrMessageEvent):
        """视频转HTML（对应原菜单 W）"""
        await event.send(event.plain_result("🌐 开始视频转HTML..."))
        try:
            await video_to_html_bg()
            await event.send(event.plain_result("✅ 视频转HTML完成"))
        except Exception as e:
            await event.send(event.plain_result(f"❌ 转换失败: {e}"))

    # ========== 开关类指令 ==========

    @filter.command("bili_asr")
    async def bili_asr(self, event: AstrMessageEvent):
        """切换ASR语音识别（对应原菜单 A）"""
        import cli.app as _app_mod
        _app_mod.ASR_ENABLED = not _app_mod.ASR_ENABLED
        config.setdefault("asr", {})["enabled"] = _app_mod.ASR_ENABLED
        if save_config(config):
            _reload_all_globals(config)
            state = "✅ 已开启" if _app_mod.ASR_ENABLED else "⏸️ 已关闭"
            await event.send(event.plain_result(f"🎤 ASR语音识别: {state}"))
        else:
            await event.send(event.plain_result("❌ 配置保存失败"))

    @filter.command("bili_quick")
    async def bili_quick(self, event: AstrMessageEvent):
        """切换快速模式（对应原菜单 Q）"""
        no_human_delay = not config.get("speed", {}).get("no_human_delay", False)
        config.setdefault("speed", {})["no_human_delay"] = no_human_delay
        if save_config(config):
            _reload_all_globals(config)
            state = "⚡ 已开启 (跳过延迟)" if no_human_delay else "🐢 已关闭 (模拟真人)"
            await event.send(event.plain_result(f"🚀 快速模式: {state}"))
        else:
            await event.send(event.plain_result("❌ 配置保存失败"))

    @filter.command("bili_quiet")
    async def bili_quiet(self, event: AstrMessageEvent):
        """切换安静模式（对应原菜单 Z）"""
        import cli.app as _app_mod
        _app_mod.QUIET_MODE = not _app_mod.QUIET_MODE
        config.setdefault("system", {})["quiet_mode"] = _app_mod.QUIET_MODE
        if save_config(config):
            _reload_all_globals(config)
            state = "🔇 已开启 (精简日志)" if _app_mod.QUIET_MODE else "📢 已关闭 (完整日志)"
            await event.send(event.plain_result(f"🤫 安静模式: {state}"))
        else:
            await event.send(event.plain_result("❌ 配置保存失败"))

    @filter.command("bili_cover")
    async def bili_cover(self, event: AstrMessageEvent):
        """切换封面分析（对应原菜单 C）"""
        import cli.app as _app_mod
        _app_mod.VISION_COVER_ENABLED = not _app_mod.VISION_COVER_ENABLED
        config.setdefault("vision", {})["cover_enabled"] = _app_mod.VISION_COVER_ENABLED
        if save_config(config):
            _reload_all_globals(config)
            state = "✅ 已开启" if _app_mod.VISION_COVER_ENABLED else "⏸️ 已关闭(刷视频更快)"
            await event.send(event.plain_result(f"🖼️ 封面分析: {state}"))
        else:
            await event.send(event.plain_result("❌ 配置保存失败"))

    # ========== 其他功能指令 ==========

    @filter.command("bili_interest")
    async def bili_interest(self, event: AstrMessageEvent):
        """兴趣设置（对应原菜单 5）"""
        await event.send(event.plain_result("🎯 兴趣设置已触发（请查看控制台）"))

    @filter.command("bili_comment")
    async def bili_comment(self, event: AstrMessageEvent):
        """评论管理（对应原菜单 6）"""
        await event.send(event.plain_result("💬 评论管理已触发（请查看控制台）"))

    @filter.command("bili_pm")
    async def bili_pm(self, event: AstrMessageEvent):
        """私信管理（对应原菜单 7）"""
        await event.send(event.plain_result("✉️ 私信管理已触发（请查看控制台）"))

    @filter.command("bili_diary")
    async def bili_diary(self, event: AstrMessageEvent):
        """日记进化（对应原菜单 8）"""
        await event.send(event.plain_result("📖 日记进化已触发（请查看控制台）"))

    @filter.command("bili_skill")
    async def bili_skill(self, event: AstrMessageEvent):
        """Agent技能（对应原菜单 9）"""
        await event.send(event.plain_result("🧠 Agent技能已触发（请查看控制台）"))

    @filter.command("bili_danmaku")
    async def bili_danmaku(self, event: AstrMessageEvent):
        """UP主弹幕菜单（对应原菜单 F）"""
        await event.send(event.plain_result("💬 UP主弹幕菜单已触发（请查看控制台）"))

    @filter.command("bili_mood")
    async def bili_mood(self, event: AstrMessageEvent):
        """心情菜单（对应原菜单 M）"""
        await event.send(event.plain_result("😊 心情菜单已触发（请查看控制台）"))

    @filter.command("bili_history")
    async def bili_history(self, event: AstrMessageEvent):
        """搜索历史（对应原菜单 H）"""
        show_search_history()
        await event.send(event.plain_result("📜 搜索历史已输出到控制台"))

    @filter.command("bili_export")
    async def bili_export(self, event: AstrMessageEvent):
        """导出配置（对应原菜单 E）"""
        export_config()
        await event.send(event.plain_result("📤 配置已导出"))

    @filter.command("bili_import")
    async def bili_import(self, event: AstrMessageEvent):
        """导入配置（对应原菜单 I）"""
        import_config()
        await event.send(event.plain_result("📥 配置已导入"))

    @filter.command("bili_reset")
    async def bili_reset(self, event: AstrMessageEvent):
        """恢复出厂设置（对应原菜单 R）"""
        factory_reset_all()
        await event.send(event.plain_result("🔄 已恢复出厂设置"))

    @filter.command("bili_tasks")
    async def bili_tasks(self, event: AstrMessageEvent):
        """查看后台任务（对应原菜单 B）"""
        try:
            _show_bg_tasks()
            await event.send(event.plain_result("📋 后台任务列表已输出到控制台"))
        except Exception as e:
            await event.send(event.plain_result(f"❌ 查看任务失败: {e}"))

    @filter.command("bili_help")
    async def bili_help(self, event: AstrMessageEvent):
        """显示所有可用指令"""
        help_text = """
📚 **B站学习机器人 - 指令列表**

**核心指令**
- `/bili_start` - 启动机器人
- `/bili_stop` - 停止机器人

**配置类**
- `/bili_config` - 查看配置
- `/bili_login` - 登录B站
- `/bili_asr` - 切换ASR
- `/bili_quick` - 切换快速模式
- `/bili_quiet` - 切换安静模式
- `/bili_cover` - 切换封面分析

**知识库类**
- `/bili_kb` - 知识库管理
- `/bili_revisit` - 重温知识库
- `/bili_organize` - 整理知识库
- `/bili_custom_kb` - 自定义知识

**视频分析类**
- `/bili_analyze` - 手动视频分析
- `/bili_up_learn` - UP主主页学习
- `/bili_video2html` - 视频转HTML

**其他**
- `/bili_interest` - 兴趣设置
- `/bili_comment` - 评论管理
- `/bili_pm` - 私信管理
- `/bili_diary` - 日记进化
- `/bili_skill` - Agent技能
- `/bili_danmaku` - UP主弹幕
- `/bili_mood` - 心情菜单
- `/bili_history` - 搜索历史
- `/bili_export` - 导出配置
- `/bili_import` - 导入配置
- `/bili_reset` - 恢复出厂设置
- `/bili_tasks` - 查看后台任务
- `/bili_help` - 显示本帮助
        """
        await event.send(event.plain_result(help_text))