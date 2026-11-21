"""Execute real API/toolbox/standby code with synthetic config and fake SDK/HTTP.

Run: python -B -m unittest discover -s tests -p test_action_permissions_api.py -v
No plugin initializer, real profile, credentials, review files or network is used.
"""
import asyncio
from contextlib import ExitStack
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_action_permissions_api_fixture"
BV = "BV1Q541167Qg"
OWNER, BOT, TARGET = 101, 202, 303
PRIVATE = "synthetic-private-content cookie=synthetic-cookie api_key=synthetic-key"
BOUNDARIES = (
    ("client_follow", "follow_up"),
    ("client_unfollow", "unfollow_user"),
    ("client_dm_like", "danmaku_like"),
    ("client_dm_send", "send_danmaku"),
    ("tool_video_like", "video_like"),
    ("tool_favorite", "favorite"),
    ("tool_coin", "coin"),
    ("tool_follow", "follow_up"),
    ("tool_unfollow", "unfollow_user"),
    ("standby_comment", "public_comment"),
)


class ActionPermissionsAPITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(sys, "dont_write_bytecode", True))
        self.network_guards = [self.stack.enter_context(patch(
            name, side_effect=AssertionError("real network is forbidden")
        )) for name in ("socket.socket.connect", "socket.socket.connect_ex",
                        "socket.create_connection", "socket.getaddrinfo")]
        modules = {}
        for name in (PACKAGE, *(f"{PACKAGE}.{part}" for part in
                               ("api", "brain", "core", "services", "utils"))):
            module = ModuleType(name)
            module.__package__ = name
            module.__path__ = []  # Never fall through to real package initializers.
            modules[name] = module

        self.runtime_config = {
            "owner_share": {"owner_bili_uid": str(OWNER)},
            "private_message": {"agent": {
                "allow_account_actions": True, "allow_social_follow_actions": True,
                "allow_proactive_social_follow": True, "social_follow_daily_limit": 2,
            }},
            "approval_review": {"enabled": False},
            "automation": {"allow_like": True, "allow_coin": True, "allow_comment": True},
            "interaction": {"prob_coin": 1, "prob_like_solo": 1},
        }
        self.config = ModuleType(f"{PACKAGE}.core.config")
        self.config.load_config = Mock(side_effect=lambda: self.runtime_config)
        self.config.config = {}
        self.config.COOKIE_FILE = str(self.temp / "never-read-cookies.json")
        self.config.INTERESTS_FILE = str(self.temp / "never-read-interests.json")
        self.config.resolve_knowledge_base_dir = Mock(return_value=str(self.temp / "knowledge"))
        modules[self.config.__name__] = self.config
        user_data = ModuleType(f"{PACKAGE}.core.user_data")
        user_data.DATA_DIR = self.temp / "data"
        self.credential_guard = Mock(side_effect=AssertionError("real credentials are forbidden"))
        user_data.managed_file_path = self.credential_guard
        user_data.write_private_json = self.credential_guard
        modules[user_data.__name__] = user_data
        self.logs, self.exceptions = Mock(), Mock()
        display = ModuleType(f"{PACKAGE}.utils.display")
        display.log, display.log_exception = self.logs, self.exceptions
        modules[display.__name__] = display
        throttle = ModuleType(f"{PACKAGE}.api.throttle")
        self.throttle = throttle._bili_throttle = AsyncMock()
        throttle._bili_trigger_cooldown = Mock()
        modules[throttle.__name__] = throttle
        policy = ModuleType(f"{PACKAGE}.core.platform_actions")
        self.public_policy = policy.public_commenting_enabled = Mock(return_value=True)
        modules[policy.__name__] = policy

        self.sdk_user = SimpleNamespace(
            modify_relation=AsyncMock(), get_relation=AsyncMock(return_value={"attribute": 1}),
            get_relation_info=AsyncMock(return_value={}),
        )
        self.sdk_video = SimpleNamespace(
            like_danmaku=AsyncMock(), send_danmaku=AsyncMock(),
            has_liked=AsyncMock(return_value=False), has_favoured=AsyncMock(return_value=False),
            like=AsyncMock(), set_favorite=AsyncMock(), pay_coin=AsyncMock(),
        )
        self.user_factory = Mock(return_value=self.sdk_user)
        self.video_factory = Mock(return_value=self.sdk_video)
        self.danmaku_factory = Mock(return_value=object())
        self.self_info = AsyncMock(return_value={"mid": BOT, "name": "synthetic bot", "coins": 100})
        sdk = ModuleType("bilibili_api")
        sdk.Credential = self.credential_guard
        sdk.user = SimpleNamespace(
            User=self.user_factory, get_self_info=self.self_info,
            RelationType=SimpleNamespace(SUBSCRIBE="subscribe", UNSUBSCRIBE="unsubscribe"),
        )
        sdk.homepage, sdk.search = SimpleNamespace(), SimpleNamespace()
        sdk.Danmaku = self.danmaku_factory
        comments = ModuleType("bilibili_api.comment")
        comments.CommentResourceType = SimpleNamespace(VIDEO=1)
        comments.OrderType = SimpleNamespace(LIKE=1)
        self.get_comments = comments.get_comments = AsyncMock(return_value={"replies": [{"rpid": 1}]})
        videos = ModuleType("bilibili_api.video")
        videos.Video = self.video_factory
        favorites = ModuleType("bilibili_api.favorite_list")
        favorites.get_video_favorite_list = AsyncMock(return_value={"list": [{"id": 7}]})
        sdk.comment, sdk.video, sdk.favorite_list = comments, videos, favorites
        modules.update({"bilibili_api": sdk, comments.__name__: comments,
                        videos.__name__: videos, favorites.__name__: favorites})
        self.favorites = favorites
        httpx = ModuleType("httpx")
        self.http_constructor = httpx.AsyncClient = Mock(
            side_effect=AssertionError("real HTTP clients are forbidden"))
        modules["httpx"] = httpx
        colorama = ModuleType("colorama")
        colorama.Fore = SimpleNamespace(GREEN="", YELLOW="")
        colorama.Style = SimpleNamespace(RESET_ALL="")
        modules["colorama"] = colorama
        self.stack.enter_context(patch.dict(sys.modules, modules))

        self.permissions = self.load("core.action_permissions", "core/action_permissions.py")
        self.review = self.load("services.like_review", "services/like_review.py")
        self.inbox = SimpleNamespace(propose=Mock(return_value={"id": "synthetic-review"}))
        self.inbox_factory = self.stack.enter_context(patch.object(
            self.review, "ActionReviewInbox", return_value=self.inbox))
        self.client_module = self.load("api.client", "api/client.py")
        self.toolbox_module = self.load("services.utils", "services/utils.py")
        self.standby_module = self.load("brain.standby", "brain/standby.py")
        self.stack.enter_context(patch.object(self.standby_module, "save_stats", Mock()))
        self.http = SimpleNamespace(
            post=AsyncMock(return_value=SimpleNamespace(json=lambda: {"code": 0})),
            get=AsyncMock(return_value=SimpleNamespace(
                json=lambda: {"code": 0, "data": {"aid": 123, "cid": 456}})),
        )
        self.credential = SimpleNamespace(dedeuserid=str(BOT))
        self.client = self.client_module.BiliClient()
        self.client.credential, self.client.uid = self.credential, BOT
        self.client.raw_cookies = {"bili_jct": "synthetic-csrf"}
        self.client._load_credential = self.credential_guard
        self.client._get_http_client = AsyncMock(return_value=self.http)
        self.toolbox = self.toolbox_module.BiliToolbox(self.credential, BOT)
        self.toolbox.self_status = AsyncMock(return_value={"coin_balance": 100})
        self.toolbox.is_owner = Mock(wraps=self.toolbox.is_owner)
        self.toolbox._proactive_social_follow_count_today = Mock(return_value=0)
        self.toolbox._record_proactive_social_follow = Mock()
        # Skip StandbyBot.__init__: it intentionally reads credentials in production.
        self.standby = self.standby_module.StandbyBot.__new__(self.standby_module.StandbyBot)
        self.standby.config = {"at_trigger_keywords": ["总结"]}
        self.standby.stats = {"at_replies": 0, "errors": 0}
        self.standby._log = Mock()
        self.standby._bvid_to_oid = AsyncMock(return_value="123")
        self.side_effects = [
            self.user_factory, self.video_factory, self.danmaku_factory,
            *vars(self.sdk_user).values(), *vars(self.sdk_video).values(),
            self.self_info, self.get_comments, self.favorites.get_video_favorite_list,
            self.http.post, self.http.get, self.http_constructor,
            self.inbox_factory, self.inbox.propose, self.throttle, self.credential_guard,
            self.client._get_http_client, self.toolbox.self_status, self.toolbox.is_owner,
            self.toolbox._proactive_social_follow_count_today,
            self.toolbox._record_proactive_social_follow, self.standby._bvid_to_oid,
            self.logs, self.exceptions, self.standby._log,
        ]

    def tearDown(self):
        self.credential_guard.assert_not_called()
        self.http_constructor.assert_not_called()
        for guard in self.network_guards:
            guard.assert_not_called()
        self.assertFalse((self.temp / "data").exists(), "tests must not create review/profile files")

    def load(self, name, filename):
        spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", ROOT / filename)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    def grant(self, action):
        self.runtime_config["ai_permissions"] = {
            "enabled": True, "actions": {action: True},
        }

    def reset_calls(self):
        for effect in self.side_effects:
            effect.reset_mock()

    async def invoke(self, boundary):
        if boundary == "client_follow":
            return await self.client.follow_up(TARGET)
        if boundary == "client_unfollow":
            return await self.client.unfollow_up(TARGET)
        if boundary == "client_dm_like":
            return await self.client.like_danmaku("123456", 456, BV)
        if boundary == "client_dm_send":
            return await self.client.send_danmaku(BV, PRIVATE, 1.5)
        if boundary.startswith("tool_"):
            action = boundary.removeprefix("tool_")
            if action in {"follow", "unfollow"}:
                return await self.toolbox.request_social_relation(
                    action, TARGET, OWNER, "请关注我或取消关注 " + PRIVATE, PRIVATE,
                    display_name=PRIVATE,
                )
            return await self.toolbox.request_video_action(
                action, BV, PRIVATE, "请点赞、收藏、投币 " + PRIVATE, OWNER,
            )
        if boundary == "standby_comment":
            return await self.standby._post_comment(self.http, BV, 1, 2, PRIVATE)
        self.fail("unknown test boundary")

    def assert_denied(self, boundary, result):
        if boundary.startswith("client_"):
            self.assertNotEqual(result.get("code"), 0, result)
            self.assertIn("未执行", result.get("msg", ""))
        elif boundary.startswith("tool_"):
            self.assertIs(result.get("ok"), False, result)
            self.assertIs(result.get("executed"), False, result)
            self.assertIs(result.get("queued"), False, result)
            self.assertIn("not executed", result.get("error", ""))
        else:
            self.assertIs(result, False)
        log_calls = [*self.logs.call_args_list, *self.standby._log.call_args_list]
        self.assertTrue(log_calls, "permission denial must have a safe diagnostic")
        self.assertNotIn(PRIVATE, str(result) + str(log_calls))
        self.assertNotIn("synthetic-cookie", str(result) + str(log_calls))
        self.exceptions.assert_not_called()

    def assert_no_writes(self):
        for effect in (self.sdk_user.modify_relation, self.sdk_video.like,
                       self.sdk_video.set_favorite, self.sdk_video.pay_coin,
                       self.sdk_video.like_danmaku, self.sdk_video.send_danmaku,
                       self.http.post, self.inbox_factory, self.inbox.propose,
                       self.toolbox._record_proactive_social_follow):
            effect.assert_not_called()

    def assert_no_effects(self):
        for effect in self.side_effects:
            if effect not in (self.logs, self.standby._log):
                effect.assert_not_called()

    async def test_default_and_legacy_settings_deny_before_reads_or_writes(self):
        self.client.credential, self.client.raw_cookies = None, {}
        for boundary, _ in BOUNDARIES:
            with self.subTest(boundary=boundary):
                self.reset_calls()
                self.assert_denied(boundary, await self.invoke(boundary))
                self.assert_no_effects()

    async def test_master_and_exact_action_must_both_be_strict_true(self):
        for boundary, action in BOUNDARIES:
            other = next(key for key in self.permissions.ACTIONS if key != action)
            for permission in (
                {"enabled": False, "actions": {action: True}},
                {"enabled": 1, "actions": {action: True}},
                {"enabled": "true", "actions": {action: True}},
                {"enabled": True, "actions": {}},
                {"enabled": True, "actions": {action: 1}},
                {"enabled": True, "actions": {action: "true"}},
                {"enabled": True, "actions": {other: True}},
            ):
                with self.subTest(boundary=boundary, permission=permission):
                    self.reset_calls()
                    self.runtime_config["ai_permissions"] = permission
                    self.assert_denied(boundary, await self.invoke(boundary))
                    self.assert_no_effects()

    async def test_configuration_failure_denies_with_static_diagnostics(self):
        self.config.load_config.side_effect = ValueError(PRIVATE)
        for boundary, _ in BOUNDARIES:
            with self.subTest(boundary=boundary):
                self.reset_calls()
                self.assert_denied(boundary, await self.invoke(boundary))
                self.assert_no_effects()

    async def test_exact_client_permission_reaches_only_requested_sdk_write(self):
        for boundary, action in BOUNDARIES[:4]:
            with self.subTest(boundary=boundary):
                self.reset_calls()
                self.grant(action)
                result = await self.invoke(boundary)
                self.assertEqual(result["code"], 0, result)
                self.throttle.assert_awaited_once()
                self.inbox.propose.assert_not_called()
                self.http.post.assert_not_called()
                if action in {"follow_up", "unfollow_user"}:
                    self.sdk_user.modify_relation.assert_awaited_once_with(
                        "subscribe" if action == "follow_up" else "unsubscribe")
                    self.sdk_video.send_danmaku.assert_not_called()
                    self.sdk_video.like_danmaku.assert_not_called()
                elif action == "danmaku_like":
                    self.sdk_video.like_danmaku.assert_awaited_once_with(dmid="123456", cid=456)
                    self.sdk_video.send_danmaku.assert_not_called()
                else:
                    self.danmaku_factory.assert_called_once_with(text=PRIVATE, dm_time=1.5)
                    self.sdk_video.send_danmaku.assert_awaited_once_with(
                        danmaku=self.danmaku_factory.return_value, page_index=0)
                    self.sdk_video.like_danmaku.assert_not_called()

    async def test_client_rechecks_live_permission_after_throttle(self):
        async def revoke(*args):
            self.runtime_config["ai_permissions"]["enabled"] = False
        self.throttle.side_effect = revoke
        for boundary, action in BOUNDARIES[:4]:
            with self.subTest(boundary=boundary):
                self.reset_calls()
                self.grant(action)
                self.assert_denied(boundary, await self.invoke(boundary))
                self.assert_no_writes()
                self.user_factory.assert_not_called()
                self.video_factory.assert_not_called()

    async def test_authorized_danmaku_like_fallback_keeps_existing_request(self):
        self.grant("danmaku_like")
        self.sdk_video.like_danmaku.side_effect = RuntimeError("synthetic SDK failure")
        self.assertEqual((await self.invoke("client_dm_like"))["code"], 0)
        self.sdk_video.like_danmaku.assert_awaited_once()
        self.http.post.assert_awaited_once_with(
            "https://api.bilibili.com/x/v2/dm/thumbup/add",
            data={"dmid": "123456", "oid": 456, "platform": "web", "csrf": "synthetic-csrf"},
            cookies=self.client.raw_cookies,
        )

    async def test_revocation_during_sdk_failure_cannot_fall_back_to_http(self):
        self.grant("danmaku_like")
        async def revoke(**kwargs):
            self.runtime_config["ai_permissions"]["enabled"] = False
            raise RuntimeError("synthetic SDK failure")
        self.sdk_video.like_danmaku.side_effect = revoke
        result = await self.invoke("client_dm_like")
        self.assertNotEqual(result["code"], 0)
        self.assertIn("未执行", result["msg"])
        self.http.post.assert_not_called()
        self.client._get_http_client.assert_not_called()

    async def test_danmaku_like_without_sdk_is_also_permission_gated(self):
        result = await self.client.like_danmaku("123456", 456)
        self.assert_denied("client_dm_like", result)
        self.assert_no_effects()
        self.grant("danmaku_like")
        self.assertEqual((await self.client.like_danmaku("123456", 456))["code"], 0)
        self.video_factory.assert_not_called()
        self.http.post.assert_awaited_once()

    async def test_toolbox_exact_permissions_still_use_existing_review(self):
        self.runtime_config["approval_review"] = {"enabled": True}
        for boundary, action in BOUNDARIES[4:9]:
            with self.subTest(boundary=boundary):
                self.reset_calls()
                self.grant(action)
                result = await self.invoke(boundary)
                self.assertIs(result["ok"], True, result)
                self.assertIs(result["queued"], True, result)
                self.assertEqual(self.inbox.propose.call_args.args[0], action)
                self.assertEqual(self.inbox.propose.call_count, 1)
                self.user_factory.assert_not_called()
                self.video_factory.assert_not_called()
                self.http.post.assert_not_called()
                self.throttle.assert_not_called()

    async def test_toolbox_exact_video_permission_reaches_sdk_without_coin_like(self):
        for boundary, action in BOUNDARIES[4:7]:
            with self.subTest(boundary=boundary):
                self.reset_calls()
                self.grant(action)
                result = await self.invoke(boundary)
                self.assertIs(result["ok"], True, result)
                self.assertIs(result["executed"], True, result)
                self.inbox.propose.assert_not_called()
                if action == "video_like":
                    self.sdk_video.like.assert_awaited_once_with(status=True)
                    self.sdk_video.pay_coin.assert_not_called()
                    self.sdk_video.set_favorite.assert_not_called()
                elif action == "favorite":
                    self.sdk_video.set_favorite.assert_awaited_once_with(add_media_ids=[7])
                    self.sdk_video.like.assert_not_called()
                    self.sdk_video.pay_coin.assert_not_called()
                else:
                    self.assertFalse(self.permissions.allowed("video_like", self.runtime_config))
                    self.sdk_video.pay_coin.assert_awaited_once_with(num=1, like=False)
                    self.sdk_video.like.assert_not_called()
                    self.sdk_video.set_favorite.assert_not_called()

    async def test_toolbox_exact_social_permission_reaches_sdk(self):
        for boundary, action in BOUNDARIES[7:9]:
            with self.subTest(boundary=boundary):
                self.reset_calls()
                self.grant(action)
                result = await self.invoke(boundary)
                self.assertIs(result["ok"], True, result)
                self.assertIs(result["executed"], True, result)
                self.sdk_user.modify_relation.assert_awaited_once_with(
                    "subscribe" if action == "follow_up" else "unsubscribe")
                self.inbox.propose.assert_not_called()
                self.toolbox._record_proactive_social_follow.assert_not_called()

    async def test_toolbox_rechecks_before_queue_after_account_status_read(self):
        self.grant("coin")
        self.runtime_config["approval_review"] = {"enabled": True}
        async def revoke(**kwargs):
            self.runtime_config["ai_permissions"]["enabled"] = False
            return {"coin_balance": 100}
        self.toolbox.self_status.side_effect = revoke
        self.assert_denied("tool_coin", await self.invoke("tool_coin"))
        self.assert_no_writes()

    async def test_toolbox_rechecks_permission_after_throttle(self):
        async def revoke(*args):
            self.runtime_config["ai_permissions"]["enabled"] = False
        self.throttle.side_effect = revoke
        for boundary, action in BOUNDARIES[4:9]:
            with self.subTest(boundary=boundary):
                self.reset_calls()
                self.grant(action)
                self.assert_denied(boundary, await self.invoke(boundary))
                self.assert_no_writes()

    async def test_toolbox_rechecks_permission_after_sdk_reads(self):
        for action, reader in (("video_like", self.sdk_video.has_liked),
                               ("favorite", self.favorites.get_video_favorite_list)):
            with self.subTest(action=action):
                self.reset_calls()
                self.grant(action)
                async def revoke(*args, **kwargs):
                    self.runtime_config["ai_permissions"]["enabled"] = False
                    return False if action == "video_like" else {"list": [{"id": 7}]}
                reader.side_effect = revoke
                self.assert_denied("tool_" + action, await self.invoke("tool_" + action))
                self.assert_no_writes()

    async def test_owner_account_switch_and_coin_policy_are_not_bypassed(self):
        self.grant("coin")
        self.assertIs((await self.toolbox.request_video_action(
            "coin", BV, PRIVATE, "请投币", TARGET))["ok"], False)
        self.runtime_config["private_message"]["agent"]["allow_account_actions"] = False
        self.assertIs((await self.invoke("tool_coin"))["ok"], False)
        self.runtime_config["private_message"]["agent"]["allow_account_actions"] = True
        self.toolbox.self_status.return_value = {"coin_balance": 0}
        self.assertIs((await self.invoke("tool_coin"))["ok"], False)
        self.toolbox.self_status.return_value = {"coin_balance": 10}
        self.assertIs((await self.toolbox.request_video_action(
            "coin", BV, "简短理由", "仅查看视频", OWNER))["ok"], False)
        self.assert_no_writes()

    async def test_social_owner_switch_reason_and_daily_limit_are_not_bypassed(self):
        self.grant("unfollow_user")
        self.assertIs((await self.toolbox.request_social_relation(
            "unfollow", TARGET, TARGET, "请取关", PRIVATE))["ok"], False)
        self.grant("follow_up")
        self.assertIs((await self.toolbox.request_social_relation(
            "follow", TARGET, OWNER, "请关注", "短理由"))["ok"], False)
        self.runtime_config["private_message"]["agent"]["allow_social_follow_actions"] = False
        self.assertIs((await self.invoke("tool_follow"))["ok"], False)
        self.runtime_config["private_message"]["agent"]["allow_social_follow_actions"] = True
        self.toolbox._proactive_social_follow_count_today.return_value = 2
        self.assertIs((await self.toolbox.request_social_relation(
            "follow", TARGET, TARGET, "普通交流", PRIVATE))["ok"], False)
        self.assert_no_writes()

    async def test_public_comment_permission_preserves_global_policy(self):
        self.grant("public_comment")
        self.public_policy.return_value = False
        self.assertIs(await self.invoke("standby_comment"), False)
        self.assert_no_effects()
        self.public_policy.return_value = True
        self.assertIs(await self.invoke("standby_comment"), True)
        self.http.post.assert_awaited_once_with(
            "https://api.bilibili.com/x/v2/reply/add",
            data={"oid": 123, "type": 1, "root": 1, "parent": 2,
                  "message": PRIVATE, "plat": 1},
        )

    async def test_standby_rechecks_after_oid_lookup(self):
        self.grant("public_comment")
        async def revoke(*args):
            self.runtime_config["ai_permissions"]["enabled"] = False
            return "123"
        self.standby._bvid_to_oid.side_effect = revoke
        self.assert_denied("standby_comment", await self.invoke("standby_comment"))
        self.assert_no_writes()

    async def test_standby_reply_stats_only_count_successful_posts(self):
        self.standby._get_video_info = AsyncMock(return_value={"title": "synthetic video"})
        self.standby._get_subtitles = AsyncMock(return_value="synthetic subtitles")
        self.standby._generate_summary = AsyncMock(return_value="synthetic summary")
        self.standby._answer_rag_question = AsyncMock(return_value="synthetic answer")
        for kind in ("notification_summary", "notification_rag", "legacy_summary"):
            for permitted in (False, True):
                with self.subTest(kind=kind, permitted=permitted):
                    self.reset_calls()
                    self.standby.stats = {"at_replies": 0, "errors": 0}
                    self.grant("public_comment")
                    self.runtime_config["ai_permissions"]["enabled"] = permitted
                    row = {"rpid": "1", "aid": 123, "uname": "synthetic user",
                           "content": "这是什么？" if kind.endswith("rag") else "总结"}
                    if kind.startswith("notification"):
                        await self.standby._handle_at_trigger_v2(self.http, row, BV)
                    else:
                        await self.standby._handle_at_trigger_legacy(self.http, row, BV, BV)
                    self.assertEqual(self.standby.stats["at_replies"], int(permitted))
                    self.assertEqual(self.standby.stats.get("notifications_handled", 0),
                                     int(permitted and kind == "notification_summary"))
                    self.assertEqual(self.standby.stats["errors"], 0)
                    self.assertEqual(self.http.post.await_count, int(permitted))

    async def test_ordinary_reads_login_and_watch_history_are_not_permission_gated(self):
        with patch.object(asyncio, "sleep", AsyncMock()):
            self.assertIs(await self.client.init_user_info(), True)
        self.assertEqual(await self.client.get_hot_comments(123), [{"rpid": 1}])
        status = await self.toolbox_module.BiliToolbox.self_status(self.toolbox)
        self.assertEqual(status["uid"], BOT)
        result = await self.client.report_history(BV)
        self.assertEqual(result["code"], 0)
        self.assertEqual(self.http.post.await_count, 2)
        self.assertTrue(all(call.args[0].endswith("/heartbeat")
                            for call in self.http.post.await_args_list))
        self.config.load_config.assert_not_called()
        self.inbox.propose.assert_not_called()


if __name__ == "__main__":
    unittest.main()
