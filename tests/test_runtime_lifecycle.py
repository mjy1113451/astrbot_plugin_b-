"""Issue #47: instance-owned plugin tasks, reload and non-destructive stop."""
import asyncio
from contextlib import ExitStack
import importlib
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_package_imports import PACKAGE_A, PACKAGE_B, _package_environment


class RuntimeLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.environment = self.stack.enter_context(_package_environment())
        self.main = self.environment.load_main(PACKAGE_A)
        self.runtime = self.environment.install_runtime(PACKAGE_A)
        self.bot = self.environment.construct_bot(self.main)
        self.event = SimpleNamespace(plain_result=lambda text: text)
        self.tasks = []
        self.release_cleanup = asyncio.Event()

    async def asyncTearDown(self):
        self.release_cleanup.set()
        for task in self.tasks:
            if not task.done():
                task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
            await asyncio.sleep(0)

    async def start(self, bot=None):
        bot = bot or self.bot
        replies = [reply async for reply in bot.start_bot(self.event)]
        self.assertIn("已在后台启动", replies[0])
        task = bot._brain_runtime.task
        self.tasks.append(task)
        await asyncio.sleep(0)
        return task

    async def stop(self, bot=None):
        return [reply async for reply in (bot or self.bot).stop_bot(self.event)]

    async def status(self, bot=None):
        return [reply async for reply in (bot or self.bot).status(self.event)]

    def blocking_cleanup(self):
        entered = asyncio.Event()
        cleanup_started = asyncio.Event()
        cancellations = []

        async def run():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleanup_started.set()
                while not self.release_cleanup.is_set():
                    try:
                        await self.release_cleanup.wait()
                    except asyncio.CancelledError:
                        cancellations.append("extra cancel")

        self.runtime.brain.AgentBrain.return_value = SimpleNamespace(run=run)
        return entered, cleanup_started, cancellations

    async def test_stop_waits_for_cleanup_then_allows_another_run(self):
        task = await self.start()
        replies = await self.stop()
        self.assertIn("已停止", replies[0])
        self.assertTrue(task.done())
        self.assertIsNone(self.bot._brain_runtime.task)
        replacement = await self.start()
        self.assertIsNot(task, replacement)
        self.runtime.app._release_bot_lock.assert_not_called()

    async def test_start_reply_never_keeps_state_lock_while_host_sends_message(self):
        for state in ("running", "stopping", "closed", "initialization_failure"):
            with self.subTest(state=state):
                bot = self.environment.construct_bot(self.main)
                if state == "initialization_failure":
                    self.runtime.config.initialize_config.side_effect = ValueError("fake failure")
                elif state == "closed":
                    await bot.terminate()
                else:
                    await self.start(bot)
                    if state == "stopping":
                        bot._brain_runtime.stopping = True
                command = bot.start_bot(self.event)
                try:
                    await anext(command)
                    self.assertFalse(bot._brain_runtime.lock.locked())
                finally:
                    await command.aclose()
                    self.runtime.config.initialize_config.side_effect = None

    async def test_stop_immediately_after_start_still_enters_brain_finally(self):
        entered, _, _ = self.blocking_cleanup()
        self.release_cleanup.set()
        replies = [reply async for reply in self.bot.start_bot(self.event)]
        self.assertIn("已在后台启动", replies[0])
        task = self.bot._brain_runtime.task
        self.tasks.append(task)
        self.assertFalse(entered.is_set())
        self.assertIn("已停止", (await self.stop())[0])
        self.assertTrue(entered.is_set())
        self.assertTrue(task.done())

    async def test_terminate_cancellation_while_waiting_for_state_lock_still_drains(self):
        _, cleanup_started, cancellations = self.blocking_cleanup()
        task = await self.start()
        await self.bot._brain_runtime.lock.acquire()
        unload = asyncio.create_task(self.bot.terminate())
        await asyncio.sleep(0)
        unload.cancel()
        await asyncio.sleep(0)
        self.bot._brain_runtime.lock.release()
        self.assertFalse(unload.done())
        await asyncio.wait_for(cleanup_started.wait(), timeout=2)
        self.assertFalse(task.done())
        self.release_cleanup.set()
        with self.assertRaises(asyncio.CancelledError):
            await unload
        self.assertTrue(task.done())
        self.assertEqual(cancellations, [])

    async def test_concurrent_starts_create_only_one_owned_task(self):
        async def start_command():
            return [reply async for reply in self.bot.start_bot(self.event)]

        replies = await asyncio.gather(start_command(), start_command())
        self.tasks.append(self.bot._brain_runtime.task)
        self.assertEqual(sum("已在后台启动" in reply[0] for reply in replies), 1)
        self.assertEqual(sum("已在后台运行" in reply[0] for reply in replies), 1)
        self.runtime.brain.AgentBrain.assert_called_once_with()

    async def test_timeout_does_not_cancel_cleanup_again_or_allow_restart(self):
        _, cleanup_started, cancellations = self.blocking_cleanup()
        task = await self.start()
        self.bot._brain_runtime.stop_timeout = 0.01
        replies = await self.stop()
        await cleanup_started.wait()
        self.assertIn("仍在收尾", replies[0])
        self.assertFalse(task.done())
        self.assertIs(self.bot._brain_runtime.task, task)
        self.assertEqual(cancellations, [])
        self.assertIn("停止中", (await self.status())[0])
        self.assertIn("停止中", (await self.stop())[0])
        retry = [reply async for reply in self.bot.start_bot(self.event)]
        self.assertIn("停止中", retry[0])
        self.runtime.brain.AgentBrain.assert_called_once_with()
        self.runtime.app._release_bot_lock.assert_not_called()
        self.release_cleanup.set()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
        self.assertIsNone(self.bot._brain_runtime.task)

    async def test_concurrent_stops_cancel_once(self):
        _, cleanup_started, cancellations = self.blocking_cleanup()
        task = await self.start()
        self.bot._brain_runtime.stop_timeout = 0.01
        first = asyncio.create_task(self.stop())
        await cleanup_started.wait()
        second = await self.stop()
        self.assertIn("停止中", second[0])
        self.assertIn("仍在收尾", (await first)[0])
        self.assertEqual(task.cancelling(), 1)
        self.assertEqual(cancellations, [])

    async def test_cancelling_stop_caller_propagates_without_recancelling_target(self):
        _, cleanup_started, cancellations = self.blocking_cleanup()
        task = await self.start()
        command = asyncio.create_task(self.stop())
        await cleanup_started.wait()
        command.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await command
        self.assertFalse(task.done())
        self.assertEqual(task.cancelling(), 1)
        self.assertEqual(cancellations, [])
        self.assertIn("停止中", (await self.status())[0])

    async def test_stop_tracks_cleanup_after_cancelled_run_ends(self):
        parent_entered, cleanup_entered = asyncio.Event(), asyncio.Event()
        brain = SimpleNamespace(_cleanup_task=None, _closing=False)

        async def cleanup():
            cleanup_entered.set()
            await self.release_cleanup.wait()

        async def run():
            parent_entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                brain._closing = True
                brain._cleanup_task = asyncio.create_task(cleanup())
                self.tasks.append(brain._cleanup_task)
                await asyncio.shield(brain._cleanup_task)

        brain.run = run
        self.runtime.brain.AgentBrain.return_value = brain
        task = await self.start()
        await parent_entered.wait()
        self.bot._brain_runtime.stop_timeout = 0.01
        command = asyncio.create_task(self.stop())
        self.tasks.append(command)
        await cleanup_entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIn("仍在收尾", (await command)[0])
        self.assertIn("停止中", (await self.status())[0])
        self.assertIn("停止中", [reply async for reply in self.bot.start_bot(self.event)][0])
        self.release_cleanup.set()
        await brain._cleanup_task
        self.assertEqual(await self.bot._brain_runtime.state(), "idle")

    async def test_terminate_waits_for_cleanup_and_closes_instance_to_new_starts(self):
        _, cleanup_started, cancellations = self.blocking_cleanup()
        task = await self.start()
        unload = asyncio.create_task(self.bot.terminate())
        await cleanup_started.wait()
        self.assertFalse(unload.done())
        self.assertFalse(task.done())
        self.release_cleanup.set()
        await unload
        self.assertTrue(task.done())
        self.assertEqual(cancellations, [])
        self.assertIsNone(self.bot._brain_runtime.task)
        self.assertIn("卸载", [reply async for reply in self.bot.start_bot(self.event)][0])
        await self.bot.terminate()

    async def test_terminate_after_stop_timeout_does_not_cancel_again(self):
        _, cleanup_started, cancellations = self.blocking_cleanup()
        task = await self.start()
        self.bot._brain_runtime.stop_timeout = 0.01
        await self.stop()
        await cleanup_started.wait()
        unload = asyncio.create_task(self.bot.terminate())
        await asyncio.sleep(0)
        self.assertFalse(unload.done())
        self.assertEqual(task.cancelling(), 1)
        self.release_cleanup.set()
        await unload
        self.assertEqual(cancellations, [])

    async def test_cancelled_terminate_caller_still_waits_for_owned_cleanup(self):
        _, cleanup_started, cancellations = self.blocking_cleanup()
        task = await self.start()
        unload = asyncio.create_task(self.bot.terminate())
        await cleanup_started.wait()
        unload.cancel()
        await asyncio.sleep(0)
        self.assertFalse(unload.done())
        self.assertFalse(task.done())
        self.release_cleanup.set()
        with self.assertRaises(asyncio.CancelledError):
            await unload
        self.assertTrue(task.done())
        self.assertEqual(cancellations, [])

    async def test_second_unload_cancel_ends_wait_without_recancelling_cleanup(self):
        _, cleanup_started, cancellations = self.blocking_cleanup()
        task = await self.start()
        unload = asyncio.create_task(self.bot.terminate())
        self.tasks.append(unload)
        await asyncio.wait_for(cleanup_started.wait(), timeout=2)
        unload.cancel()
        await asyncio.sleep(0)
        self.assertFalse(unload.done())
        unload.cancel()
        done, _ = await asyncio.wait((unload,), timeout=2)
        self.assertIn(unload, done)
        self.assertTrue(unload.cancelled())
        self.assertFalse(task.done())
        self.assertEqual(cancellations, [])

    async def test_in_place_reload_does_not_lose_old_instance_task(self):
        task = await self.start()
        owner = self.bot._brain_runtime
        importlib.reload(self.main)
        self.assertIs(self.bot._brain_runtime, owner)
        self.assertIn("运行中", (await self.status())[0])
        await self.stop()
        self.assertTrue(task.done())
        self.assertIsNone(owner.task)
        self.runtime.app._release_bot_lock.assert_not_called()

    async def test_host_terminate_then_namespace_purge_creates_independent_owner(self):
        old_task = await self.start()
        old_owner = self.bot._brain_runtime
        await self.bot.terminate()
        self.assertTrue(old_task.done())
        self.environment.drop_namespace(PACKAGE_A)
        fresh_main = self.environment.load_main(PACKAGE_A)
        fresh_runtime = self.environment.install_runtime(PACKAGE_A)
        fresh_bot = self.environment.construct_bot(fresh_main)
        new_task = await self.start(fresh_bot)
        old_owner._on_done(old_task)
        self.assertIs(fresh_bot._brain_runtime.task, new_task)
        self.assertFalse(new_task.done())
        self.runtime.app._release_bot_lock.assert_not_called()
        fresh_runtime.app._release_bot_lock.assert_not_called()
        self.environment.assert_isolated(self)

    async def test_stopping_one_install_leaves_another_install_running(self):
        other_main = self.environment.load_main(PACKAGE_B)
        other_runtime = self.environment.install_runtime(PACKAGE_B)
        other_bot = self.environment.construct_bot(other_main)
        first = await self.start()
        second = await self.start(other_bot)
        await self.bot.terminate()
        self.assertTrue(first.done())
        self.assertFalse(second.done())
        self.assertIs(other_bot._brain_runtime.task, second)
        other_runtime.app._release_bot_lock.assert_not_called()
        self.environment.assert_isolated(self)

    async def test_same_package_instances_do_not_share_task_handles(self):
        other_bot = self.environment.construct_bot(self.main)
        task = await self.start()
        self.assertIsNot(other_bot._brain_runtime.lock, self.bot._brain_runtime.lock)
        self.assertIsNone(other_bot._brain_runtime.task)
        await other_bot.terminate()
        self.assertFalse(task.done())
        self.assertIs(self.bot._brain_runtime.task, task)

    async def test_stale_completion_callback_does_not_clear_replacement(self):
        old_task = await self.start()
        self.runtime.finished.set()
        await old_task
        await asyncio.sleep(0)
        self.runtime.finished.clear()
        replacement = await self.start()
        self.bot._brain_runtime._on_done(old_task)
        self.assertIs(self.bot._brain_runtime.task, replacement)
        self.assertFalse(replacement.done())
        self.runtime.app._release_bot_lock.assert_not_called()

    async def test_background_failure_is_safely_logged_without_cli_lock_release(self):
        async def fail():
            raise ValueError("PRIVATE_RUNTIME_CANARY")

        self.runtime.brain.AgentBrain.return_value = SimpleNamespace(run=fail)
        task = await self.start()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
        self.assertIsNone(self.bot._brain_runtime.task)
        self.runtime.display.log_exception.assert_called_once()
        self.assertNotIn("PRIVATE_RUNTIME_CANARY", str(self.environment.logger.mock_calls))
        self.runtime.app._release_bot_lock.assert_not_called()

    async def test_natural_cleanup_reports_stopping_without_cancelling_again(self):
        cleanup_started = asyncio.Event()
        brain = SimpleNamespace(_closing=False)

        async def run():
            brain._closing = True
            cleanup_started.set()
            await self.release_cleanup.wait()

        brain.run = run
        self.runtime.brain.AgentBrain.return_value = brain
        task = await self.start()
        await cleanup_started.wait()
        self.assertFalse(task.done())
        self.assertIn("停止中", (await self.status())[0])
        self.assertIn("停止中", (await self.stop())[0])
        retry = [reply async for reply in self.bot.start_bot(self.event)]
        self.assertIn("停止中", retry[0])
        self.assertEqual(task.cancelling(), 0)
        self.release_cleanup.set()
        await task
        await asyncio.sleep(0)
        self.assertIsNone(self.bot._brain_runtime.task)

    async def test_running_status_never_loads_cli_background_display(self):
        await self.start()
        with patch.object(self.main, "_cli_app", side_effect=AssertionError("CLI imported")):
            self.assertIn("运行中", (await self.status())[0])

    async def test_unmanaged_lock_diagnostics_are_read_only_and_do_not_claim_global_stop(self):
        cases = (
            ({"locked": True, "pid": 123, "stale": False}, "存在未由当前实例管理的运行或锁占用"),
            ({"locked": True, "pid": None, "stale": False}, "状态未知"),
            ({"locked": False, "pid": None, "stale": False}, "未发现锁占用"),
            ({"locked": False, "pid": 123, "stale": True}, "已退出的进程遗留"),
        )
        for snapshot, expected in cases:
            with self.subTest(snapshot=snapshot):
                self.runtime.lock.bot_lock_status.return_value = snapshot
                for command in (self.stop, self.status):
                    text = (await command())[0]
                    self.assertIn("未登记受管任务", text)
                    self.assertIn(expected, text)
                    self.assertNotIn("当前没有正在运行的机器人", text)
                    self.assertNotIn("已停止", text)
                self.runtime.lock.bot_lock_status.assert_called_with(clean_stale=False)
                self.runtime.app._release_bot_lock.assert_not_called()

    async def test_stop_idle_reply_says_it_stopped_nothing_and_other_entries_may_run(self):
        """Issue #49: an empty task handle must not read as "the bot is stopped"."""
        for snapshot in (
            {"locked": True, "pid": 123, "stale": False},
            {"locked": True, "pid": None, "stale": False},
            {"locked": False, "pid": None, "stale": False},
            {"locked": False, "pid": 123, "stale": True},
        ):
            with self.subTest(snapshot=snapshot):
                self.runtime.lock.bot_lock_status.return_value = snapshot
                text = (await self.stop())[0]
                self.assertIn("本命令没有停止任何任务", text)
                self.assertIn("其他插件实例、CLI 入口或其他进程", text)
                self.assertNotIn("已停止", text)
                # The status command shares the diagnostic but never claims to stop.
                status = (await self.status())[0]
                self.assertNotIn("本命令没有停止任何任务", status)
                self.assertIn("不由当前插件实例管理", status)

    async def test_lock_read_failure_reports_unknown_without_exception_body(self):
        self.runtime.lock.bot_lock_status.side_effect = OSError("PRIVATE_LOCK_CANARY")
        for command in (self.stop, self.status):
            text = (await command())[0]
            self.assertIn("锁状态未知", text)
            self.assertNotIn("PRIVATE_LOCK_CANARY", text)
            self.assertNotIn("已停止", text)
        self.runtime.app._release_bot_lock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
