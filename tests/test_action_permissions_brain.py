"""Issue #42 brain writes: execute real AST bodies with isolated config/SDKs.

Run: python -B -m unittest discover -s tests -p test_action_permissions_brain.py -v
No application imports, account files, network, or real review queues are used.
"""
import ast
import asyncio
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import re
import sys
from types import MethodType, ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_action_permissions_brain_fixture"
BV = "BV1Q541167Qg"
COMMENT = {"id": 42, "aid": 123, "content": "请讲讲", "user": "reader", "user_id": 7}


class BrainPermissionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(sys, "dont_write_bytecode", True))
        self.stack.enter_context(patch.dict(sys.modules))
        for name in (PACKAGE, PACKAGE + ".core", PACKAGE + ".brain", PACKAGE + ".services"):
            module = ModuleType(name)
            module.__path__ = []
            sys.modules[name] = module
        self.cfg = {}
        self.config_loader = Mock(side_effect=lambda: self.cfg)
        cfg = ModuleType(PACKAGE + ".core.config")
        cfg.load_config = self.config_loader
        sys.modules[cfg.__name__] = cfg
        spec = importlib.util.spec_from_file_location(
            PACKAGE + ".core.action_permissions", ROOT / "core/action_permissions.py"
        )
        self.permissions = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.permissions
        spec.loader.exec_module(self.permissions)
        platform = ModuleType(PACKAGE + ".core.platform_actions")
        platform.at_mention_replies_enabled = Mock(return_value=True)
        sys.modules[platform.__name__] = platform
        self.review_actions = set()
        self.queue = Mock()
        review = ModuleType(PACKAGE + ".services.like_review")
        review.requires_review = Mock(side_effect=lambda cfg, action: action in self.review_actions)
        review.ActionReviewInbox = Mock(return_value=SimpleNamespace(propose=self.queue))
        review.LikeReviewInbox = Mock(return_value=SimpleNamespace(propose=self.queue))
        sys.modules[review.__name__] = review
        self.review = review
        self.sdk_comment = SimpleNamespace(send_comment=AsyncMock(return_value={}))
        self.comment_like = AsyncMock(return_value={})
        self.sdk_comment.Comment = Mock(return_value=SimpleNamespace(like=self.comment_like))
        self.sdk_private = SimpleNamespace(send_msg=AsyncMock(return_value={}),
                                           EventType=SimpleNamespace(TEXT=1))
        self.guard = SimpleNamespace(review=Mock(return_value=(True, "", [])),
                                     block_on_incoming=False,
                                     detect_injection=Mock(return_value=(False, [])))
        self.throttle = AsyncMock()
        self.log, self.log_exception = Mock(), Mock()
        # Deliberately stale, permissive startup settings must never authorize a write.
        snapshot = {"ai_permissions": {"enabled": True, "actions": dict.fromkeys(self.permissions.ACTIONS, True)}}
        self.ns = {
            "__package__": PACKAGE + ".brain", "config": snapshot, "datetime": datetime,
            "asyncio": SimpleNamespace(sleep=AsyncMock(), wait_for=asyncio.wait_for,
                                       TimeoutError=asyncio.TimeoutError),
            "random": SimpleNamespace(uniform=lambda *args: 0, random=lambda: 0,
                                      choice=lambda items: items[0], choices=lambda *args, **kwargs: ["like"]),
            "json": json, "re": re, "log": self.log, "log_exception": self.log_exception,
            "_bili_throttle": self.throttle, "comment": self.sdk_comment,
            "bili_session": self.sdk_private, "CommentResourceType": SimpleNamespace(VIDEO=1),
            "public_commenting_enabled": lambda: True, "video_liking_enabled": lambda: True,
            "ensure_ai_marker": lambda value: value, "human_reply_delay": lambda: 0,
            "ReplySafetyGuard": lambda: self.guard, "_is_terminal_comment_error": lambda exc: False,
            "_safe_platform_error": lambda exc: type(exc).__name__, "COMMENT_MODE": "real",
            "DATA_DIR": "unused-isolated-data", "MAX_REPLIES_PER_CHECK": 3,
            "PROB_COMMENT_OTHERS": 1, "PRIVATE_MESSAGE_ENABLED": True,
            "PRIVATE_MESSAGE_AUTO_REPLY": True, "PRIVATE_MESSAGE_MAX_REPLIES": 3,
            "UP_FOLLOW_ENABLED": True, "UP_FOLLOW_MAX_DAILY": 3,
            "UP_FOLLOW_COOLDOWN_MINUTES": 0, "UP_FOLLOW_EXCEPTIONAL_SCORE": 8,
            "UP_FOLLOW_MIN_SCORE": 6, "UP_FOLLOW_MIN_IMPRESSIONS": 1,
            "UP_FOLLOW_AUTO_PROB": 1, "DANMAKU_LIKE_PROB": 1, "DANMAKU_SEND_PROB": 1,
            "DANMAKU_MAX_DAILY_LIKES": 5, "DANMAKU_MAX_DAILY_SEND": 5,
            "VIDEO_INTERVAL_MIN": 0, "VIDEO_INTERVAL_MAX": 0,
        }
        self.ns["random"].randint = lambda *args: 60
        self.snapshot = snapshot

    def grant(self, *actions):
        self.cfg.clear()
        self.cfg.update(ai_permissions={"enabled": True, "actions": dict.fromkeys(actions, True)})

    def function(self, filename, name):
        tree = ast.parse((ROOT / "brain" / filename).read_text(encoding="utf-8"))
        imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)
                   and node.module == "core.action_permissions"]
        function = next(node for node in ast.walk(tree)
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name)
        namespace = dict(self.ns)
        module = ast.fix_missing_locations(ast.Module(body=[*imports, function], type_ignores=[]))
        exec(compile(module, str(ROOT / "brain" / filename), "exec"), namespace)
        return namespace[name]

    def bind(self, obj, filename, name):
        function = self.function(filename, name)
        setattr(obj, name, MethodType(function, obj))
        return getattr(obj, name)

    def video_actions(self, requested=("video_like", "favorite", "coin", "public_comment")):
        """Compile the unmodified, contiguous write section of the real run() body."""
        tree = ast.parse((ROOT / "brain/_brain_loop.py").read_text(encoding="utf-8"))
        body = next(node.body for node in ast.walk(tree) if isinstance(node, ast.Try)
                    and any(isinstance(item, ast.If) and isinstance(item.test, ast.Name)
                            and item.test.id == "action_log" for item in node.body))
        start = next(i for i, node in enumerate(body) if isinstance(node, ast.If)
                     and isinstance(node.test, ast.Name) and node.test.id == "do_like_trigger")
        end = next(i for i, node in enumerate(body) if isinstance(node, ast.If)
                   and isinstance(node.test, ast.Name) and node.test.id == "action_log")
        wrapper = ast.parse("async def execute(self, v):\n"
                            "    do_like_trigger = 'video_like' in requested\n"
                            "    do_fav = 'favorite' in requested\n"
                            "    do_coin = 'coin' in requested\n"
                            "    do_replies = [{'target_id': 42, 'content': 'reply'}] if 'public_comment' in requested else []\n"
                            "    action_log = []\n"
                            "    aid = 123\n").body[0]
        wrapper.body.extend(deepcopy(body[start:end]))
        wrapper.body.append(ast.Return(value=ast.Name(id="action_log", ctx=ast.Load())))
        imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)
                   and node.module == "core.action_permissions"]
        namespace = {**self.ns, "requested": requested, "bvid": BV, "title": "title", "up": "creator",
                     "score": 9, "thought": "thought", "video_url": "url", "pic_url": "pic",
                     "tags": [], "up_uid": 17, "c_list": [COMMENT],
                     "favorite_list": SimpleNamespace(get_video_favorite_list=AsyncMock(
                         return_value={"list": [{"id": 5}]}))}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[*imports, wrapper], type_ignores=[])),
                     str(ROOT / "brain/_brain_loop.py"), "exec"), namespace)
        return namespace["execute"]

    def manager(self):
        return SimpleNamespace(credential=SimpleNamespace(dedeuserid=1), last_reply_failure={},
                               safety_guard=self.guard, log_interaction=Mock(), _mark_user_replied=Mock(),
                               _remember_outbound_message=Mock(), log_blocked_reply=Mock())

    def brain(self):
        manager = self.manager()
        manager._should_reply_user = Mock(return_value=(True, ""))
        return SimpleNamespace(
            credential=SimpleNamespace(dedeuserid=1), coins_spent=0, _coin_hour_timestamps=[],
            psycho_profile=None, user_profile_mgr=SimpleNamespace(adjust_affinity=Mock(),
                                                                 build_prompt_block=lambda *args: ""),
            add_history_video=Mock(), comment_mgr=manager, mood_mgr=SimpleNamespace(
                shift=Mock(), build_prompt_block=lambda: ""),
            persona_mgr=SimpleNamespace(build_prompt_block=lambda: ""),
            interest_mgr=SimpleNamespace(get_interests=lambda: []),
            record_session_event=Mock(), _last_active_chat_at=datetime(2020, 1, 1), _active_chat_count=0,
            _live_config=lambda: {"model_brain": "fake"},
            _call_ai_with_retry=AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content="hello"))])),
            daily_follows=0, last_follow_at=datetime(2020, 1, 1),
            _reset_daily_follows=Mock(), _inspect_up_before_follow=AsyncMock(return_value=True),
            memory={"known_ups": {"creator": {"views": 3, "avg_score": 9}}}, _save_memory=Mock(),
            daily_danmaku_likes=0, daily_danmaku_sent=0,
            _reset_daily_danmaku_likes=Mock(), _reset_daily_danmaku_sent=Mock(),
            _last_danmaku_cids={BV: 123}, _last_danmaku_videos={},
            bili=SimpleNamespace(follow_up=AsyncMock(return_value={"code": 0}),
                                 like_danmaku=AsyncMock(return_value={"code": 0}),
                                 send_danmaku=AsyncMock(return_value={"code": 0})),
        )

    def video(self):
        return SimpleNamespace(has_liked=AsyncMock(return_value=False), like=AsyncMock(),
                               has_favoured=AsyncMock(return_value=False), set_favorite=AsyncMock(),
                               pay_coin=AsyncMock(), get_aid=lambda: 123)

    def assert_no_video_writes(self, video):
        for name in ("like", "set_favorite", "pay_coin"):
            getattr(video, name).assert_not_awaited()
        self.sdk_comment.send_comment.assert_not_awaited()

    async def test_loop_denial_never_writes_queues_or_records_success(self):
        for review in (False, True):
            with self.subTest(review=review):
                self.review_actions = set(self.permissions.ACTIONS) if review else set()
                brain, video = self.brain(), self.video()
                self.assertEqual(await self.video_actions()(brain, video), [])
                self.assert_no_video_writes(video)
                self.queue.assert_not_called()
                brain.user_profile_mgr.adjust_affinity.assert_not_called()
                brain.add_history_video.assert_not_called()
                brain.comment_mgr.log_interaction.assert_not_called()
                self.assertEqual((brain.coins_spent, brain._coin_hour_timestamps), (0, []))

    async def test_loop_authorization_keeps_existing_review_path(self):
        for action in ("video_like", "favorite", "coin"):
            with self.subTest(action=action):
                self.grant(action)
                self.review_actions = {action}
                self.queue.reset_mock()
                video = self.video()
                self.assertEqual(await self.video_actions((action,))(self.brain(), video), [])
                self.queue.assert_called_once()
                self.assert_no_video_writes(video)

    async def test_loop_authorization_reaches_original_writes_without_coin_like(self):
        self.grant("video_like", "favorite", "coin", "public_comment")
        brain, video = self.brain(), self.video()
        self.assertEqual(await self.video_actions()(brain, video), ["点赞", "收藏", "投币", "回复评论(42)"])
        video.like.assert_awaited_once_with(status=True)
        video.set_favorite.assert_awaited_once_with(add_media_ids=[5])
        video.pay_coin.assert_awaited_once_with(num=1, like=False)
        self.sdk_comment.send_comment.assert_awaited_once()
        self.assertEqual(brain.coins_spent, 1)

    async def test_coin_only_cannot_attach_unapproved_like(self):
        self.grant("coin")
        video = self.video()
        self.assertEqual(await self.video_actions(("coin",))(self.brain(), video), ["投币"])
        video.pay_coin.assert_awaited_once_with(num=1, like=False)
        video.like.assert_not_awaited()

    async def test_loop_rechecks_live_permissions_after_throttling(self):
        for action in ("video_like", "favorite", "coin", "public_comment"):
            with self.subTest(action=action):
                self.grant(action)
                self.throttle.side_effect = lambda *args: self.cfg.clear()
                brain, video = self.brain(), self.video()
                self.assertEqual(await self.video_actions((action,))(brain, video), [])
                self.assert_no_video_writes(video)
                self.assertEqual(brain.coins_spent, 0)
                brain.user_profile_mgr.adjust_affinity.assert_not_called()
                brain.comment_mgr.log_interaction.assert_not_called()

    async def test_comment_reply_denies_even_mentions_and_simulation(self):
        for mentioned in (False, True):
            for mode in ("real", "simulate"):
                with self.subTest(mentioned=mentioned, mode=mode):
                    self.ns["COMMENT_MODE"] = mode
                    manager = self.manager()
                    result = await self.function("comment.py", "reply_to_comment")(
                        manager, None, COMMENT, "reply", is_at_mention=mentioned)
                    self.assertIs(result, False)
                    self.sdk_comment.send_comment.assert_not_awaited()
                    manager.log_interaction.assert_not_called()
                    manager._mark_user_replied.assert_not_called()

    async def test_comment_reply_preserves_keyword_guard_and_sends_when_allowed(self):
        self.grant("public_comment")
        manager = self.manager()
        reply = self.function("comment.py", "reply_to_comment")
        self.guard.review.return_value = (False, "keyword", ["hit"])
        self.assertFalse(await reply(manager, None, COMMENT, "reply"))
        manager.log_blocked_reply.assert_called_once()
        self.sdk_comment.send_comment.assert_not_awaited()
        self.guard.review.return_value = (True, "", [])
        self.assertTrue(await reply(manager, None, COMMENT, "reply"))
        self.sdk_comment.send_comment.assert_awaited_once()
        manager._mark_user_replied.assert_called_once_with(7)

    async def test_comment_reply_rechecks_permission_after_throttling(self):
        self.grant("public_comment")
        self.throttle.side_effect = lambda: self.cfg.clear()
        manager = self.manager()
        self.assertFalse(await self.function("comment.py", "reply_to_comment")(manager, None, COMMENT, "reply"))
        self.sdk_comment.send_comment.assert_not_awaited()
        manager.log_interaction.assert_not_called()

    async def test_comment_like_has_its_own_permission(self):
        manager = self.manager()
        like = self.function("comment.py", "like_comment")
        self.grant("public_comment")
        self.assertFalse(await like(manager, None, COMMENT))
        self.comment_like.assert_not_awaited()
        self.sdk_comment.Comment.assert_not_called()
        self.grant("comment_like")
        self.assertTrue(await like(manager, None, COMMENT))
        self.comment_like.assert_awaited_once_with(status=True)
        manager.log_interaction.assert_called_once()

    async def test_comment_processing_counts_only_successful_likes(self):
        manager = self.manager()
        manager.get_new_comments = AsyncMock(return_value=[COMMENT])
        manager.toolbox = SimpleNamespace(_has_social_relation_request=lambda text: False)
        manager._mark_comment_processed = Mock()
        manager.user_profile_mgr = SimpleNamespace(adjust_affinity=Mock())
        self.bind(manager, "comment.py", "like_comment")
        process = self.function("comment.py", "process_new_comments")
        self.assertEqual(await process(manager, None), 0)
        manager.user_profile_mgr.adjust_affinity.assert_not_called()
        self.grant("comment_like")
        self.assertEqual(await process(manager, None), 1)
        manager.user_profile_mgr.adjust_affinity.assert_called_once()

    async def test_private_denial_precedes_review_and_has_explicit_unsent_result(self):
        for review in (False, True):
            with self.subTest(review=review):
                self.review_actions = {"private_reply"} if review else set()
                manager = self.manager()
                result = await self.function("private_msg.py", "send_reply")(manager, 7, "reply")
                self.assertIs(result.get("sent"), False)
                self.assertFalse(result.get("queued"))
                self.queue.assert_not_called()
                self.sdk_private.send_msg.assert_not_awaited()
                manager._remember_outbound_message.assert_not_called()

    async def test_private_authorization_preserves_review_and_no_success_memory(self):
        self.grant("private_reply")
        self.review_actions = {"private_reply"}
        manager = self.manager()
        result = await self.function("private_msg.py", "send_reply")(manager, 7, "reply")
        self.assertTrue(result["queued"])
        self.queue.assert_called_once()
        self.sdk_private.send_msg.assert_not_awaited()
        manager._remember_outbound_message.assert_not_called()

    async def test_private_authorized_send_preserves_raw_success_and_rejects_errors(self):
        self.grant("private_reply")
        manager = self.manager()
        send = self.function("private_msg.py", "send_reply")
        self.assertEqual(await send(manager, 7, "reply"), {})
        manager._remember_outbound_message.assert_called_once()
        for failure in ({"code": -1, "message": "rejected"}, {"sent": False}, False):
            with self.subTest(failure=failure):
                manager._remember_outbound_message.reset_mock()
                self.sdk_private.send_msg.return_value = failure
                result = await send(manager, 7, "reply")
                self.assertIs(result["sent"], False)
                manager._remember_outbound_message.assert_not_called()

    async def test_private_rechecks_live_permission_after_throttling(self):
        self.grant("private_reply")
        self.throttle.side_effect = lambda: self.cfg.clear()
        manager = self.manager()
        result = await self.function("private_msg.py", "send_reply")(manager, 7, "reply")
        self.assertIs(result["sent"], False)
        self.sdk_private.send_msg.assert_not_awaited()
        manager._remember_outbound_message.assert_not_called()

    async def test_private_processing_does_not_count_unsent_or_queued_as_success(self):
        for status in ("denied", "queued", "sent"):
            with self.subTest(status=status):
                self.cfg.clear()
                self.review_actions.clear()
                if status != "denied":
                    self.grant("private_reply")
                if status == "queued":
                    self.review_actions.add("private_reply")
                manager = self.manager()
                msg = {"id": 12, "talker_id": 7, "content": "hello"}
                manager.get_new_messages = AsyncMock(return_value=[msg])
                manager._coalesce_incoming_burst = AsyncMock(side_effect=lambda message: message)
                manager.context_db = SimpleNamespace(add_message=Mock(), update_profile=Mock(),
                                                     add_memory=Mock(), get_tool_cache=Mock(return_value={}))
                manager._mark_message_bundle_processed = Mock()
                manager.generate_reply = AsyncMock(return_value="reply")
                manager._split_reply_messages = lambda reply: [reply]
                manager._reply_generation_timeout_seconds = lambda: 75
                manager.log_data = {"history": []}
                self.bind(manager, "private_msg.py", "send_reply")
                count = await self.function("private_msg.py", "process_new_messages")(manager, auto_reply=True)
                self.assertEqual(count, int(status == "sent"))
                entry = manager.log_data["history"][0]
                self.assertEqual(entry["sent"], status == "sent")
                roles = [call.args[1] for call in manager.context_db.add_message.call_args_list]
                self.assertEqual(roles.count("assistant"), int(status == "sent"))
                self.assertEqual(manager.context_db.add_memory.call_count, int(status == "sent"))

    async def test_progress_log_does_not_claim_denied_message_was_sent(self):
        manager = self.manager()
        manager._shared_video_title = lambda context: "title"
        self.bind(manager, "private_msg.py", "send_reply")
        await self.function("private_msg.py", "_send_video_inspection_progress")(
            manager, {"talker_id": 7}, {"inspect_video": BV}, "context")
        self.assertFalse(any("已发送" in str(call.args) for call in self.log.call_args_list))
        self.sdk_private.send_msg.assert_not_awaited()

    async def test_active_chat_denial_and_queue_never_increment_success_counter(self):
        self.grant("private_reply")
        for result in ({"sent": False}, {"code": -1}, False, {"queued": True}, {"code": 0}, {}):
            with self.subTest(result=result):
                brain = self.brain()
                brain.private_message_mgr = SimpleNamespace(send_reply=AsyncMock(return_value=result))
                before = brain._last_active_chat_at
                await self.function("_brain_session.py", "_compose_active_chat")(brain, 7, "reader", None)
                queued = isinstance(result, dict) and result.get("queued")
                success = result is not False and not queued and result.get("sent") is not False and result.get("code", 0) == 0
                self.assertEqual(brain._active_chat_count, int(success))
                if success or queued:
                    self.assertEqual(brain.record_session_event.call_args.args[0],
                                     "active_chat" if success else "active_chat_review")
                else:
                    brain.record_session_event.assert_not_called()
                    self.assertEqual(brain._last_active_chat_at, before)

    async def test_session_write_denial_precedes_review_and_success_updates(self):
        self.review_actions = {"follow_up", "send_danmaku", "danmaku_like"}
        brain = self.brain()
        for name, args in (("maybe_follow_up", (17, "creator", 9)),
                           ("maybe_send_danmaku", (BV,)),
                           ("maybe_like_danmaku", (BV, [{"id_str": "dm42", "text": "hello"}], 123))):
            with self.subTest(name=name):
                self.assertFalse(await self.function("_brain_session.py", name)(brain, *args))
        self.queue.assert_not_called()
        for method in (brain.bili.follow_up, brain.bili.send_danmaku, brain.bili.like_danmaku):
            method.assert_not_awaited()
        self.assertEqual((brain.daily_follows, brain.daily_danmaku_sent, brain.daily_danmaku_likes), (0, 0, 0))
        brain.record_session_event.assert_not_called()

    async def test_session_authorization_keeps_review_and_daily_limits(self):
        self.grant("follow_up", "send_danmaku", "danmaku_like")
        for review in (True, False):
            with self.subTest(review=review):
                self.review_actions = {"follow_up", "send_danmaku"} if review else set()
                brain = self.brain()
                self.assertEqual(await self.function("_brain_session.py", "maybe_follow_up")(
                    brain, 17, "creator", 9), not review)
                self.assertEqual(await self.function("_brain_session.py", "maybe_send_danmaku")(brain, BV), not review)
                self.assertTrue(await self.function("_brain_session.py", "maybe_like_danmaku")(
                    brain, BV, [{"id_str": "dm42", "text": "hello"}], 123))
                self.assertEqual((brain.daily_follows, brain.daily_danmaku_sent, brain.daily_danmaku_likes),
                                 (int(not review), int(not review), 1))
        self.ns.update(UP_FOLLOW_MAX_DAILY=0, DANMAKU_MAX_DAILY_SEND=0, DANMAKU_MAX_DAILY_LIKES=0)
        for name, args in (("maybe_follow_up", (17, "creator", 9)), ("maybe_send_danmaku", (BV,)),
                           ("maybe_like_danmaku", (BV, [{"id_str": "dm42"}], 123))):
            with self.subTest(limit=name):
                self.assertFalse(await self.function("_brain_session.py", name)(self.brain(), *args))

    async def test_no_permissions_do_not_block_danmaku_reading_or_history(self):
        brain = self.brain()
        items = [{"id_str": "dm42", "text": "hello"}]
        brain.bili.get_danmakus = AsyncMock(return_value=(123, items))
        brain.bili.report_history = AsyncMock(return_value={"code": 0})
        self.ns["DANMAKU_READ_PROB"] = 1
        self.bind(brain, "_brain_session.py", "maybe_like_danmaku")
        self.bind(brain, "_brain_session.py", "maybe_send_danmaku")
        self.assertEqual(await self.function("_brain_session.py", "maybe_read_danmaku")(brain, BV, force=True), items)
        await self.function("_brain_session.py", "watch_and_sync_history")(brain, BV)
        brain.bili.get_danmakus.assert_awaited_once()
        brain.bili.report_history.assert_awaited_once()
        brain.bili.like_danmaku.assert_not_awaited()
        brain.bili.send_danmaku.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
