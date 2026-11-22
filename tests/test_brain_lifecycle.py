"""Issue #47: real AST lifecycle methods with fake sessions and no business I/O."""
import ast
import asyncio
from contextlib import ExitStack
from datetime import datetime
import gc
import os
from pathlib import Path
import random
import sys
import tempfile
from types import MethodType, ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
import warnings


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_brain_lifecycle_fixture"
CANARY = "PRIVATE_LIFECYCLE_ERROR_47"


def load_node(relative, name, namespace):
    """Execute production definitions, not a copy of their implementation."""
    path = ROOT / relative
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(node for node in ast.walk(tree)
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == name)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                 str(path), "exec"), namespace)
    return namespace[name]


class BrainLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.dict(sys.modules))
        self.log, self.log_exception = Mock(), Mock()
        for name in (PACKAGE, PACKAGE + ".brain", PACKAGE + ".services", PACKAGE + ".utils"):
            module = ModuleType(name)
            module.__path__ = []
            sys.modules[name] = module
        engine = ModuleType(PACKAGE + ".services.interest_engine")
        engine.get_engine = lambda: SimpleNamespace(get_keywords=lambda: [])
        display = ModuleType(PACKAGE + ".utils.display")
        display.log, display.log_exception = self.log, self.log_exception
        sys.modules[engine.__name__] = engine
        sys.modules[display.__name__] = display
        safe = load_node("utils/helpers.py", "_safe_task_callback",
                         {"__package__": PACKAGE + ".utils"})
        self.safe_callback = Mock(wraps=safe)
        self.tasks = []
        self.gates = []
        self.namespace = self.make_namespace()
        self.brain = self.make_brain(self.namespace)

    async def asyncTearDown(self):
        # Only test-owned objects are touched, even when an assertion fails.
        for gate in self.gates:
            gate.set()
        for task in self.tasks:
            if not task.done():
                task.cancel()
        if self.tasks:
            await asyncio.wait_for(asyncio.gather(*self.tasks, return_exceptions=True), 3)

    def event(self):
        event = asyncio.Event()
        self.gates.append(event)
        return event

    def task(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.append(task)
        return task

    async def reached(self, event):
        await asyncio.wait_for(event.wait(), 2)

    def make_namespace(self):
        order = []
        lock = SimpleNamespace(held=False)

        def acquire():
            order.append("acquire")
            lock.held = True
            return True

        def release():
            order.append("release")
            lock.held = False

        return {
            "__package__": PACKAGE + ".brain", "asyncio": asyncio, "datetime": datetime,
            "os": os, "random": random, "log": self.log, "log_exception": self.log_exception,
            "_runtime": SimpleNamespace(plugin_mode=True),
            "_safe_task_callback": self.safe_callback,
            "_acquire_bot_lock": Mock(side_effect=acquire),
            "_release_bot_lock": Mock(side_effect=release),
            "order": order, "lock": lock,
            "KNOWLEDGE_BASE_DIR": str(self.temp / "kb"),
            "config": {"speed": {"no_human_delay": True}},
            "MAX_ENERGY": 100, "VIDEO_INTERVAL_MIN": 1, "VIDEO_INTERVAL_MAX": 2,
            "SESSION_MAX_VIDEOS": 1, "SESSION_MAX_LEARNED_VIDEOS": 0,
            "SESSION_MAX_DURATION_MINUTES": 0, "SESSION_COMPLETION_ACTION": "stop",
            "COMMENT_CHECK_ENABLED": False, "COMMENT_CHECK_INTERVAL": 60,
            "PRIVATE_MESSAGE_ENABLED": False, "PRIVATE_MESSAGE_AUTO_REPLY": False,
            "PRIVATE_MESSAGE_CHECK_INTERVAL": 60,
            "DIARY_ENABLED": False, "DIARY_AUTO_ENABLED": False,
            "EVOLUTION_ENABLED": False, "EVOLUTION_AUTO_ENABLED": False,
            "EVOLUTION_AUTO_APPLY": False,
            "COOLDOWN_STARTUP_MIN": 1, "COOLDOWN_STARTUP_MAX": 2,
        }

    def make_brain(self, namespace, session=None):
        cls = load_node("brain/_brain_loop.py", "BrainLoopMixin", namespace)
        brain = cls()

        async def close():
            namespace["order"].append("close")

        brain.bili = SimpleNamespace(close=AsyncMock(side_effect=close))
        brain.update_runtime_clock = Mock()
        brain.update_activity = Mock()
        brain.previous_seen_at = ""
        brain.interest_mgr = SimpleNamespace(get_interests=lambda: [])
        brain.persona_mgr = SimpleNamespace(get_active_persona=lambda: "fake-persona")
        brain.mood_mgr = SimpleNamespace(get_current=lambda: "fake-mood")
        brain.classifier = SimpleNamespace(cleanup_empty_folders=lambda: 0,
                                           show_category_structure=Mock())
        brain.initialize_login = AsyncMock(return_value=False)
        brain._init_ob_bridge = AsyncMock()
        brain._live_config = lambda: {"model_brain": "fake-model"}
        brain.session_start_time = datetime.now()
        brain.videos_processed, brain.videos_learned = 1, 0
        if session is not None:
            brain._run_session = MethodType(session, brain)
        return brain

    def use_session(self, implementation):
        self.brain._run_session = MethodType(implementation, self.brain)

    def spawn(self, brain, coroutine, name="test-child"):
        task = brain._spawn_background_task(coroutine, name)
        self.tasks.append(task)
        return task

    def assert_finished(self, namespace=None, brain=None):
        namespace = self.namespace if namespace is None else namespace
        brain = self.brain if brain is None else brain
        brain.bili.close.assert_awaited_once_with()
        namespace["_release_bot_lock"].assert_called_once_with()
        self.assertFalse(namespace["lock"].held)
        self.assertEqual(namespace["order"][-2:], ["close", "release"])
        self.assertFalse(brain._background_tasks)
        self.assertTrue(brain._closing)
        self.assertTrue(brain._cleanup_task.done())

    async def test_login_failure_closes_owned_client_then_releases_lock(self):
        with patch("builtins.input", side_effect=AssertionError("interactive login")), \
                patch("builtins.print", side_effect=AssertionError("terminal output")):
            self.assertIsNone(await self.brain.run())
        self.brain.initialize_login.assert_awaited_once_with()
        self.brain._init_ob_bridge.assert_not_awaited()
        self.assert_finished()

    async def test_real_session_preserves_stop_and_monitor_results(self):
        for action in ("stop", "monitor"):
            with self.subTest(action=action):
                namespace = self.make_namespace()
                namespace["SESSION_COMPLETION_ACTION"] = action
                brain = self.make_brain(namespace)
                brain.initialize_login.return_value = True
                self.assertEqual(await brain.run(), action)
                brain._init_ob_bridge.assert_awaited_once_with()
                self.assert_finished(namespace, brain)

    async def test_startup_exception_is_preserved_after_cleanup(self):
        failure = ValueError(CANARY)
        self.brain.update_runtime_clock.side_effect = failure
        with self.assertRaises(ValueError) as caught:
            await self.brain.run()
        self.assertIs(caught.exception, failure)
        self.brain.initialize_login.assert_not_awaited()
        self.assert_finished()

    async def test_cancel_during_login_still_closes_then_releases(self):
        entered, hold = self.event(), self.event()

        async def login():
            entered.set()
            await hold.wait()

        self.brain.initialize_login.side_effect = login
        parent = self.task(self.brain.run())
        await self.reached(entered)
        parent.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await parent
        self.assert_finished()

    async def test_lock_denied_closes_own_client_but_never_releases(self):
        self.namespace["_acquire_bot_lock"].side_effect = None
        self.namespace["_acquire_bot_lock"].return_value = False
        self.brain._run_session = AsyncMock(side_effect=AssertionError("session without lock"))
        self.assertIsNone(await self.brain.run())
        self.brain._run_session.assert_not_awaited()
        self.brain.bili.close.assert_awaited_once_with()
        self.namespace["_release_bot_lock"].assert_not_called()
        self.assertEqual(self.namespace["order"], ["close"])

    async def test_acquire_exception_closes_without_releasing_unacquired_lock(self):
        failure = OSError(CANARY)
        self.namespace["_acquire_bot_lock"].side_effect = failure
        with self.assertRaises(OSError) as caught:
            await self.brain.run()
        self.assertIs(caught.exception, failure)
        self.brain.bili.close.assert_awaited_once_with()
        self.namespace["_release_bot_lock"].assert_not_called()

    async def test_natural_return_drains_background_before_close(self):
        entered = self.event()
        hold = self.event()
        children = []

        async def child():
            entered.set()
            try:
                await hold.wait()
            finally:
                self.namespace["order"].append("child-done")

        async def session(brain):
            children.append(self.spawn(brain, child()))
            await entered.wait()
            return "monitor"

        self.use_session(session)
        self.assertEqual(await self.brain.run(), "monitor")
        self.assertTrue(children[0].cancelled())
        self.assertEqual(self.namespace["order"], ["acquire", "child-done", "close", "release"])
        self.safe_callback.assert_called_once_with("test-child")
        self.assert_finished()

    async def test_session_error_drains_children_and_preserves_exception(self):
        entered, hold = self.event(), self.event()
        failure = RuntimeError(CANARY)

        async def child():
            entered.set()
            try:
                await hold.wait()
            finally:
                self.namespace["order"].append("child-done")

        async def session(brain):
            self.spawn(brain, child())
            await entered.wait()
            raise failure

        self.use_session(session)
        with self.assertRaises(RuntimeError) as caught:
            await self.brain.run()
        self.assertIs(caught.exception, failure)
        self.assertEqual(self.namespace["order"], ["acquire", "child-done", "close", "release"])
        self.assert_finished()

    async def exercise_resistant_child(self, natural_return, extra_cancels):
        entered, cancelling, finish, session_hold = (self.event() for _ in range(4))
        children = []

        async def child():
            entered.set()
            try:
                await session_hold.wait()
            except asyncio.CancelledError:
                cancelling.set()
                await finish.wait()
            self.namespace["order"].append("child-done")
            return "child-result"

        async def session(brain):
            children.append(self.spawn(brain, child()))
            await entered.wait()
            if not natural_return:
                await session_hold.wait()
            return "stop"

        self.use_session(session)
        parent = self.task(self.brain.run())
        await self.reached(entered)
        if not natural_return:
            parent.cancel()
        await self.reached(cancelling)
        for _ in range(extra_cancels):
            parent.cancel()
            await asyncio.sleep(0)
        self.assertFalse(parent.done())
        self.assertFalse(children[0].done())
        self.assertEqual(children[0].cancelling(), 1)
        self.assertTrue(self.namespace["lock"].held)
        self.brain.bili.close.assert_not_awaited()
        self.namespace["_release_bot_lock"].assert_not_called()
        finish.set()
        if not natural_return or extra_cancels:
            with self.assertRaises(asyncio.CancelledError):
                await parent
        else:
            self.assertEqual(await parent, "stop")
        self.assertEqual(children[0].result(), "child-result")
        self.assertEqual(self.namespace["order"], ["acquire", "child-done", "close", "release"])
        self.assert_finished()

    async def test_cancel_keeps_lock_until_cancel_resistant_child_is_done(self):
        await self.exercise_resistant_child(natural_return=False, extra_cancels=0)

    async def test_repeated_parent_cancel_cannot_cancel_cleanup_or_child_again(self):
        await self.exercise_resistant_child(natural_return=False, extra_cancels=2)

    async def test_cancel_during_natural_cleanup_is_deferred_and_propagated(self):
        await self.exercise_resistant_child(natural_return=True, extra_cancels=2)

    async def test_cancel_during_client_close_waits_before_releasing(self):
        entered, finish = self.event(), self.event()

        async def close():
            entered.set()
            await finish.wait()
            self.namespace["order"].append("close")

        self.brain.bili.close.side_effect = close
        parent = self.task(self.brain.run())
        await self.reached(entered)
        for _ in range(2):
            parent.cancel()
            await asyncio.sleep(0)
        self.assertFalse(parent.done())
        self.assertTrue(self.namespace["lock"].held)
        self.namespace["_release_bot_lock"].assert_not_called()
        finish.set()
        with self.assertRaises(asyncio.CancelledError):
            await parent
        self.assert_finished()

    async def test_close_failure_uses_shared_logger_and_still_releases(self):
        failure = RuntimeError(CANARY)
        self.brain.bili.close.side_effect = failure
        self.assertIsNone(await self.brain.run())
        self.brain.bili.close.assert_awaited_once_with()
        self.namespace["_release_bot_lock"].assert_called_once_with()
        self.assertFalse(self.namespace["lock"].held)
        self.log_exception.assert_called_once()
        self.assertIs(self.log_exception.call_args.args[1], failure)
        self.assertNotIn(CANARY, str(self.log.call_args_list))

    async def test_release_failure_is_logged_once_without_replacing_result(self):
        failure = OSError(CANARY)
        self.namespace["_release_bot_lock"].side_effect = failure
        self.assertIsNone(await self.brain.run())
        self.brain.bili.close.assert_awaited_once_with()
        self.namespace["_release_bot_lock"].assert_called_once_with()
        self.log_exception.assert_called_once()
        self.assertIs(self.log_exception.call_args.args[1], failure)
        self.assertNotIn(CANARY, str(self.log.call_args_list))

    async def test_release_callable_is_captured_before_runtime_reload(self):
        original_release = self.namespace["_release_bot_lock"]
        replacement = Mock(side_effect=AssertionError("new module released old lock"))

        async def session(brain):
            self.namespace["_release_bot_lock"] = replacement
            return "stop"

        self.use_session(session)
        self.assertEqual(await self.brain.run(), "stop")
        original_release.assert_called_once_with()
        replacement.assert_not_called()
        self.assertEqual(self.namespace["order"], ["acquire", "close", "release"])

    async def test_closing_rejects_spawn_and_closes_coroutine_without_warning(self):
        entered, cancelling, finish = (self.event() for _ in range(3))

        async def child():
            entered.set()
            try:
                await finish.wait()
            except asyncio.CancelledError:
                cancelling.set()
                await finish.wait()

        async def session(brain):
            self.spawn(brain, child())
            await entered.wait()
            return "stop"

        async def rejected():
            raise AssertionError("spawned during closing")

        self.use_session(session)
        parent = self.task(self.brain.run())
        await self.reached(cancelling)
        with warnings.catch_warnings(record=True) as recorded:
            warnings.simplefilter("always", RuntimeWarning)
            coroutine = rejected()
            with self.assertRaises(RuntimeError):
                self.brain._spawn_background_task(coroutine, "rejected")
            self.assertIsNone(coroutine.cr_frame)
            del coroutine
            gc.collect()
            self.assertFalse([item for item in recorded if issubclass(item.category, RuntimeWarning)])
        self.assertEqual(len(self.brain._background_tasks), 1)
        finish.set()
        self.assertEqual(await parent, "stop")
        coroutine = rejected()
        try:
            with self.assertRaises(RuntimeError):
                self.brain._spawn_background_task(coroutine, "after-close")
            self.assertIsNone(coroutine.cr_frame)
        finally:
            coroutine.close()
        self.assert_finished()

    async def test_spawn_before_run_is_rejected_and_coroutine_closed(self):
        async def child():
            raise AssertionError("spawned before lifecycle")

        coroutine = child()
        try:
            with self.assertRaises(RuntimeError):
                self.brain._spawn_background_task(coroutine, "before-run")
            self.assertIsNone(coroutine.cr_frame)
        finally:
            coroutine.close()

    async def test_finished_and_failed_children_are_unregistered_and_observed(self):
        failure = RuntimeError(CANARY)

        async def child(fail):
            if fail:
                raise failure
            return "finished"

        async def session(brain):
            good = self.spawn(brain, child(False), "good")
            bad = self.spawn(brain, child(True), "bad")
            self.assertEqual(brain._background_tasks, {good, bad})
            self.assertEqual(await asyncio.gather(good, bad, return_exceptions=True), ["finished", failure])
            await asyncio.sleep(0)
            self.assertFalse(brain._background_tasks)
            return "stop"

        self.use_session(session)
        self.assertEqual(await self.brain.run(), "stop")
        self.log_exception.assert_called_once_with("后台任务 [bad] 异常", failure)
        self.assertNotIn(CANARY, str(self.log.call_args_list))
        self.assert_finished()

    async def test_other_instance_and_unrelated_host_task_are_not_touched(self):
        a_ready, b_ready, host_ready, a_finish, b_finish, host_finish = (self.event() for _ in range(6))
        children = {}

        async def child(ready):
            ready.set()
            await self.event().wait()

        async def session_a(brain):
            children["a"] = self.spawn(brain, child(a_ready), "child-a")
            await a_finish.wait()
            return "stop"

        async def session_b(brain):
            children["b"] = self.spawn(brain, child(b_ready), "child-b")
            await b_finish.wait()
            return "monitor"

        async def host_job():
            host_ready.set()
            await host_finish.wait()
            return "host-result"

        self.use_session(session_a)
        other_namespace = self.make_namespace()
        other = self.make_brain(other_namespace, session_b)
        a, b, host = self.task(self.brain.run()), self.task(other.run()), self.task(host_job())
        for ready in (a_ready, b_ready, host_ready):
            await self.reached(ready)
        self.assertIsNot(self.brain._background_tasks, other._background_tasks)
        a_finish.set()
        self.assertEqual(await a, "stop")
        self.assertTrue(children["a"].cancelled())
        self.assertFalse(children["b"].done())
        self.assertEqual(children["b"].cancelling(), 0)
        self.assertFalse(b.done())
        self.assertFalse(host.done())
        self.assertEqual(host.cancelling(), 0)
        other.bili.close.assert_not_awaited()
        other_namespace["_release_bot_lock"].assert_not_called()
        self.assert_finished()
        b_finish.set()
        host_finish.set()
        self.assertEqual(await b, "monitor")
        self.assertEqual(await host, "host-result")
        self.assert_finished(other_namespace, other)

    async def test_repeated_or_concurrent_run_is_rejected_without_second_cleanup(self):
        entered, finish = self.event(), self.event()

        async def session(brain):
            entered.set()
            await finish.wait()
            return "stop"

        self.use_session(session)
        parent = self.task(self.brain.run())
        await self.reached(entered)
        with self.assertRaises(RuntimeError):
            await self.brain.run()
        self.brain.bili.close.assert_not_awaited()
        self.namespace["_release_bot_lock"].assert_not_called()
        finish.set()
        self.assertEqual(await parent, "stop")
        with self.assertRaises(RuntimeError):
            await self.brain.run()
        self.namespace["_acquire_bot_lock"].assert_called_once_with()
        self.assert_finished()

    def test_six_detached_sites_use_owned_spawn_with_original_safe_callback_names(self):
        tree = ast.parse((ROOT / "brain/_brain_loop.py").read_text(encoding="utf-8"))
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "BrainLoopMixin")
        methods = {node.name: node for node in cls.body if isinstance(node, ast.AsyncFunctionDef)}
        session = methods.get("_run_session", methods["run"])
        spawns = [node for node in ast.walk(session) if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute) and node.func.attr == "_spawn_background_task"]
        expected = {
            "_dive_async()": "agent_dive_async",
            "self.verify_knowledge_file(bvid, title)": "verify_knowledge_file",
            "self._prefetch_recommendations()": "prefetch_recs",
            "self.psycho_profile.deep_analyze()": "deep_analyze",
            "self._agent_goal_async(goal1, score=score)": "agent_goal1",
            "self._agent_goal_async(goal2, score=score)": "agent_goal2",
        }
        self.assertEqual({ast.unparse(node.args[0]): node.args[1].value for node in spawns}, expected)
        direct = [node for node in ast.walk(session) if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute) and node.func.attr == "create_task"]
        self.assertEqual({ast.unparse(node.args[0]) for node in direct}, {
            "self.check_and_handle_comments()", "self.check_and_handle_private_messages()",
            "self.check_and_handle_mentions()",
        })
        self.assertFalse([node for node in ast.walk(tree) if isinstance(node, ast.Attribute)
                          and node.attr in {"all_tasks", "set_task_factory"}])


if __name__ == "__main__":
    unittest.main()
