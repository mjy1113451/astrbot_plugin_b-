#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bilibili_learning_bot — AstrBot 插件版本
将原 CLI 菜单命令转换为聊天指令
"""

import asyncio
import os
import traceback

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star
from astrbot.api import logger

# ============================================================
# 1. 导入所有原有业务模块（保持原样）
# ============================================================
# 原 cli.app 中的常量和函数
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

# ============================================================
# 2. 辅助函数：原 CLI 的异步运行器
# ============================================================
def _run_async(coro):
    """安全执行异步协程"""
    return asyncio.run(coro)


# ============================================================
# 3. 插件主类（继承 Star）
# ============================================================
class BilibiliLearningBot(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        # 原 main() 中的 Windows 事件循环策略设置
        if os.name == 'nt':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        # 原免责声明确认
        _disclaimer_confirm()
        logger.info("Bilibili Learning Bot 插件已加载")

    # ============================================================
    # 3.1 原菜单选项 "1"：启动机器人
    # ============================================================
    @filter.command("bili_start")
    async def start_bot(self, event: AstrMessageEvent):
        """启动 B站学习机器人（对应原菜单选项 1）"""
        try:
            yield event.plain_result("🚀 正在启动机器人...")
            await AgentBrain().run()
        except KeyboardInterrupt:
            logger.warning("机器人被用户中断")
            yield event.plain_result("⏹️ 机器人已中断")
        except Exception as e:
            logger.error(f"机器人运行异常: {e}")
            traceback.print_exc()
            yield event.plain_result(f"❌ 机器人运行异常: {e}")
        finally:
            _release_bot_lock()
            yield event.plain_result("🛑 机器人已停止")

    # ============================================================
    # 3.2 原菜单选项 "2"：配置管理
    # ============================================================
    @filter.command("bili_config")
    async def show_config(self, event: AstrMessageEvent):
        """显示配置菜单（对应原菜单选项 2）"""
        # 注意：原 show_config_menu() 是交互式菜单，此处改为输出配置摘要
        # 如需完整菜单，可将输出改为引导用户使用子命令
        yield event.plain_result(
            "📋 配置管理功能\n"
            "可用子命令：\n"
            "/bili_config_export - 导出配置\n"
            "/bili_config_import - 导入配置\n"
            "/bili_config_reset - 恢复出厂设置"
        )

    @filter.command("bili_config_export")
    async def export_config_cmd(self, event: AstrMessageEvent):
        """导出配置（对应原菜单选项 E）"""
        try:
            result = export_config()
            yield event.plain_result(f"✅ 配置已导出: {result}" if result else "❌ 导出失败")
        except Exception as e:
            yield event.plain_result(f"❌ 导出异常: {e}")

    @filter.command("bili_config_import")
    async def import_config_cmd(self, event: AstrMessageEvent):
        """导入配置（对应原菜单选项 I）"""
        try:
            result = import_config()
            yield event.plain_result(f"✅ 配置已导入: {result}" if result else "❌ 导入失败")
        except Exception as e:
            yield event.plain_result(f"❌ 导入异常: {e}")

    @filter.command("bili_config_reset")
    async def factory_reset_cmd(self, event: AstrMessageEvent):
        """恢复出厂设置（对应原菜单选项 R）"""
        try:
            factory_reset_all()
            yield event.plain_result("✅ 已恢复出厂设置")
        except Exception as e:
            yield event.plain_result(f"❌ 重置失败: {e}")

    # ============================================================
    # 3.3 原菜单选项 "3"：登录管理
    # ============================================================
    @filter.command("bili_login")
    async def login(self, event: AstrMessageEvent):
        """登录管理（对应原菜单选项 3）"""
        try:
            # 原 show_login_menu() 是交互式菜单，此处简化为执行登录流程
            await _run_async(show_login_menu())
            yield event.plain_result("✅ 登录流程已执行")
        except Exception as e:
            yield event.plain_result(f"❌ 登录异常: {e}")

    # ============================================================
    # 3.4 原菜单选项 "4"：知识库管理
    # ============================================================
    @filter.command("bili_kb")
    async def knowledge_base(self, event: AstrMessageEvent):
        """知识库管理（对应原菜单选项 4）"""
        yield event.plain_result(
            "📚 知识库管理\n"
            "可用子命令：\n"
            "/bili_kb_revisit - 重温知识库\n"
            "/bili_kb_custom - 自定义知识管理\n"
            "/bili_kb_organize - 整理知识库\n"
            "/bili_kb_tutor - 知识辅导"
        )

    @filter.command("bili_kb_revisit")
    async def kb_revisit(self, event: AstrMessageEvent):
        """重温知识库（对应原菜单选项 K）"""
        try:
            await _run_async(revisit_knowledge_base_menu())
            yield event.plain_result("✅ 知识库重温完成")
        except Exception as e:
            yield event.plain_result(f"❌ 异常: {e}")

    @filter.command("bili_kb_custom")
    async def kb_custom(self, event: AstrMessageEvent):
        """自定义知识管理（对应原菜单选项 N）"""
        try:
            await _run_async(custom_knowledge_menu())
            yield event.plain_result("✅ 自定义知识管理完成")
        except Exception as e:
            yield event.plain_result(f"❌ 异常: {e}")

    @filter.command("bili_kb_organize")
    async def kb_organize(self, event: AstrMessageEvent):
        """整理知识库（对应原菜单选项 O）"""
        try:
            await _run_async(organize_knowledge_base())
            yield event.plain_result("✅ 知识库整理完成")
        except Exception as e:
            yield event.plain_result(f"❌ 异常: {e}")

    @filter.command("bili_kb_tutor")
    async def kb_tutor(self, event: AstrMessageEvent):
        """知识辅导（对应原菜单选项 T）"""
        try:
            await _run_async(show_knowledge_tutor_menu())
            yield event.plain_result("✅ 知识辅导完成")
        except Exception as e:
            yield event.plain_result(f"❌ 异常: {e}")

    # ============================================================
    # 3.5 原菜单选项 "5"：兴趣管理
    # ============================================================
    @filter.command("bili_interest")
    async def interest(self, event: AstrMessageEvent):
        """兴趣管理（对应原菜单选项 5）"""
        try:
            show_interest_menu()
            yield event.plain_result("✅ 兴趣管理已执行")
        except Exception as e:
            yield event.plain_result(f"❌ 异常: {e}")

    @filter.command("bili_interest_prefs")
    async def interest_prefs(self, event: AstrMessageEvent):
        """兴趣偏好设置（对应原菜单选项 P）"""
        try:
            show_interest_prefs_menu()
            yield event.plain_result("✅ 兴趣偏好设置完成")
        except Exception as e:
            yield event.plain_result(f"❌ 异常: {e}")

    # ============================================================
    # 3.6 原菜单选项 "6"：评论管理
    # ============================================================
    @filter.command("bili_comment")
    async def comment(self, event: AstrMessageEvent):
        """评论管理（对应原菜单选项 6）"""
        try:
            show_comment_menu()
            yield event.plain_result("✅ 评论管理已执行")
        except Exception as e:
            yield event.plain_result(f"❌ 异常: {e}")

    # ============================================================
    # 3.7 原菜单选项 "7"：私信管理
    # ============================================================
    @filter.command("bili_pm")
    async def private_message(self, event: AstrMessageEvent):
        """私信管理（对应原菜单选项 7）"""
        try:
            show_private_message_menu()
            yield event.plain_result("✅ 私信管理已执行")
        except Exception as e:
            yield event.plain_result(f"❌ 异常: {e}")

    # ============================================================
    # 3.8 原菜单选项 "8"：日记演化
    # ============================================================
    @filter.command("bili_diary")
    async def diary_evolution(self, event: AstrMessageEvent):
        """日记演化（对应原菜单选项 8）"""
        try:
            show_diary_evolution_menu()
            yield event.plain_result("✅ 日记演化已执行")
        except Exception as e:
            yield event.plain_result(f"❌ 异常: {e}")

    # ============================================================
    # 3.9 原菜单选项 "9"：Agent 技能
    # ============================================================
    @filter.command("bili_skill")
    async def agent_skill(self, event: AstrMessageEvent):
        """Agent 技能管理（对应原菜单选项 9）"""
        try:
            show_agent_skill_menu()
            yield event.plain_result("✅ Agent 技能已执行")
        except Exception as e:
            yield event.plain_result(f"❌ 异常: {e}")

    # ============================================================
    # 3.10 原菜单选项 "F"：UP主弹幕
    # ============================================================
    @filter.command("bili_danmaku")
    async def up_danmaku(self, event: AstrMessageEvent):
        """UP主弹幕管理（对应原菜单选项 F）"""
        try:
            show_up_danmaku_menu()
            yield event.plain_result("✅ UP主弹幕管理已执行")
        except Exception as e:
            yield event.plain_result(f"❌ 异常: {e}")

    # ============================================================
    # 3.11 原菜单选项 "V"：手动视频分析
    # ============================================================
    @filter.command("bili_analyze")
    async def analyze_video(self, event: AstrMessageEvent):
        """手动视频分析（对应原菜单选项 V）"""
        try:
            yield event.plain_result("🔍 开始视频分析...")
            await _run_async(manual_video_analysis())
            yield event.plain_result("✅ 视频分析完成")
        except KeyboardInterrupt:
            yield event.plain_result("⏹️ 用户中断")
        except Exception as e:
            yield event.plain_result(f"❌ 异常: {e}")

    # ============================================================
    # 3.12 原菜单选项 "U"：UP主主页学习
    # ============================================================
    @filter.command("bili_up")
    async def up_homepage(self, event: AstrMessageEvent):
        """UP主主页学习（对应原菜单选项 U）"""
        try:
            yield event.plain_result("🔍 开始UP主主页学习...")
            await _run_async(up_homepage_learn())
            yield event.plain_result("✅ UP主主页学习完成")
        except KeyboardInterrupt:
            yield event.plain_result("⏹️ 用户中断")
        except Exception as e:
            yield event.plain_result(f"❌ 异常: {e}")

    # ============================================================
    # 3.13 原菜单选项 "W"：视频转HTML
    # ============================================================
    @filter.command("bili_tohtml")
    async def video_to_html(self, event: AstrMessageEvent):
        """视频转HTML（对应原菜单选项 W）"""
        try:
            yield event.plain_result("🔄 开始视频转HTML...")
            await _run_async(video_to_html_bg())
            yield event.plain_result("✅ 视频转HTML完成")
        except KeyboardInterrupt:
            yield event.plain_result("⏹️ 用户中断")
        except Exception as e:
            yield event.plain_result(f"❌ 异常: {e}")

    # ============================================================
    # 3.14 原菜单选项 "G"：ASR设置
    # ============================================================
    @filter.command("bili_asr")
    async def asr_settings(self, event: AstrMessageEvent):
        """ASR语音识别设置（对应原菜单选项 G）"""
        try:
            _configure_asr_settings()
            if save_config(config):
                _reload_all_globals(config)
                yield event.plain_result("✅ ASR设置已保存")
            else:
                yield event.plain_result("❌ ASR设置保存失败")
        except Exception as e:
            yield event.plain_result(f"❌ 异常: {e}")

    # ============================================================
    # 3.15 原菜单选项 "D"：干货设置
    # ============================================================
    @filter.command("bili_drygoods")
    async def dry_goods_settings(self, event: AstrMessageEvent):
        """干货设置（对应原菜单选项 D）"""
        try:
            _configure_dry_goods_settings()
            yield event.plain_result("✅ 干货设置已执行")
        except Exception as e:
            yield event.plain_result(f"❌ 异常: {e}")

    # ============================================================
    # 3.16 原菜单选项 "M"：心情菜单
    # ============================================================
    @filter.command("bili_mood")
    async def mood(self, event: AstrMessageEvent):
        """心情菜单（对应原菜单选项 M）"""
        try:
            show_mood_menu()
            yield event.plain_result("✅ 心情菜单已执行")
        except Exception as e:
            yield event.plain_result(f"❌ 异常: {e}")

    # ============================================================
    # 3.17 原菜单选项 "L"：待机设置
    # ============================================================
    @filter.command("bili_standby")
    async def standby_settings(self, event: AstrMessageEvent):
        """待机设置（对应原菜单选项 L）"""
        try:
            _configure_standby_settings()
            yield event.plain_result("✅ 待机设置已执行")
        except Exception as e:
            yield event.plain_result(f"❌ 异常: {e}")

    # ============================================================
    # 3.18 原菜单选项 "Y"：视频间隔设置
    # ============================================================
    @filter.command("bili_interval")
    async def video_interval(self, event: AstrMessageEvent):
        """视频间隔设置（对应原菜单选项 Y）"""
        try:
            _configure_video_interval_settings()
            yield event.plain_result("✅ 视频间隔设置已执行")
        except Exception as e:
            yield event.plain_result(f"❌ 异常: {e}")

    # ============================================================
    # 3.19 原菜单选项 "S"：回复安全设置
    # ============================================================
    @filter.command("bili_safety")
    async def reply_safety(self, event: AstrMessageEvent):
        """回复安全设置（对应原菜单选项 S）"""
        try:
            show_reply_safety_menu()
            yield event.plain_result("✅ 回复安全设置已执行")
        except Exception as e:
            yield event.plain_result(f"❌ 异常: {e}")

    # ============================================================
    # 3.20 原菜单选项 "H"：搜索历史
    # ============================================================
    @filter.command("bili_history")
    async def search_history(self, event: AstrMessageEvent):
        """搜索历史（对应原菜单选项 H）"""
        try:
            show_search_history()
            yield event.plain_result("✅ 搜索历史已显示")
        except Exception as e:
            yield event.plain_result(f"❌ 异常: {e}")

    # ============================================================
    # 3.21 原菜单选项 "B"：后台任务
    # ============================================================
    @filter.command("bili_bg")
    async def bg_tasks(self, event: AstrMessageEvent):
        """查看后台任务（对应原菜单选项 B）"""
        try:
            _show_bg_tasks()
            yield event.plain_result("✅ 后台任务已查看")
        except Exception as e:
            yield event.plain_result(f"❌ 异常: {e}")

    # ============================================================
    # 3.22 原菜单选项 "X"：投币设置
    # ============================================================
    @filter.command("bili_coin")
    async def coin_settings(self, event: AstrMessageEvent):
        """投币设置（对应原菜单选项 X）"""
        try:
            show_coin_settings_menu()
            yield event.plain_result("✅ 投币设置已执行")
        except Exception as e:
            yield event.plain_result(f"❌ 异常: {e}")

    # ============================================================
    # 3.23 原快速切换功能（A/Q/C/Z 等）
    # ============================================================
    @filter.command("bili_toggle_asr")
    async def toggle_asr(self, event: AstrMessageEvent):
        """切换 ASR 开关（对应原菜单选项 A）"""
        try:
            import cli.app as _app_mod
            _app_mod.ASR_ENABLED = not _app_mod.ASR_ENABLED
            config.setdefault("asr", {})["enabled"] = _app_mod.ASR_ENABLED
            if save_config(config):
                _reload_all_globals(config)
                state = "✓ 已开启" if _app_mod.ASR_ENABLED else "⏸️ 已关闭"
                yield event.plain_result(f"✅ ASR语音识别: {state}")
            else:
                yield event.plain_result("❌ 配置保存失败")
        except Exception as e:
            yield event.plain_result(f"❌ 异常: {e}")

    @filter.command("bili_toggle_cover")
    async def toggle_cover(self, event: AstrMessageEvent):
        """切换封面分析开关（对应原菜单选项 C）"""
        try:
            import cli.app as _app_mod
            _app_mod.VISION_COVER_ENABLED = not _app_mod.VISION_COVER_ENABLED
            config.setdefault("vision", {})["cover_enabled"] = _app_mod.VISION_COVER_ENABLED
            if save_config(config):
                _reload_all_globals(config)
                state = "✓ 已开启" if _app_mod.VISION_COVER_ENABLED else "⏸️ 已关闭(刷视频更快)"
                yield event.plain_result(f"✅ 封面分析: {state}")
            else:
                yield event.plain_result("❌ 配置保存失败")
        except Exception as e:
            yield event.plain_result(f"❌ 异常: {e}")

    @filter.command("bili_toggle_quiet")
    async def toggle_quiet(self, event: AstrMessageEvent):
        """切换安静模式（对应原菜单选项 Z）"""
        try:
            import cli.app as _app_mod
            _app_mod.QUIET_MODE = not _app_mod.QUIET_MODE
            config.setdefault("system", {})["quiet_mode"] = _app_mod.QUIET_MODE
            if save_config(config):
                _reload_all_globals(config)
                state = "🔇 已开启 (精简日志)" if _app_mod.QUIET_MODE else "📢 已关闭 (完整日志)"
                yield event.plain_result(f"✅ 安静模式: {state}")
            else:
                yield event.plain_result("❌ 配置保存失败")
        except Exception as e:
            yield event.plain_result(f"❌ 异常: {e}")

    @filter.command("bili_toggle_fast")
    async def toggle_fast(self, event: AstrMessageEvent):
        """切换快速模式（对应原菜单选项 Q）"""
        try:
            no_human_delay = not config.get("speed", {}).get("no_human_delay", False)
            config.setdefault("speed", {})["no_human_delay"] = no_human_delay
            if save_config(config):
                _reload_all_globals(config)
                state = "⚡ 已开启 (跳过延迟)" if no_human_delay else "🐢 已关闭 (模拟真人)"
                yield event.plain_result(f"✅ 快速模式: {state}")
            else:
                yield event.plain_result("❌ 配置保存失败")
        except Exception as e:
            yield event.plain_result(f"❌ 异常: {e}")

    # ============================================================
    # 3.24 终止函数（可选）
    # ============================================================
    async def terminate(self):
        """插件被卸载/停用时调用[reference:0]"""
        logger.info("Bilibili Learning Bot 插件已卸载")