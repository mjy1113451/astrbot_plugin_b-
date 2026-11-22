"""Real Agent/Brain timeout chains must not outlive their owned run."""
import asyncio
from datetime import datetime
import json
import sys
from types import MethodType, ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

import test_brain_lifecycle as lifecycle
from test_owned_async_tasks import wait_for_owned


class AgentTaskCleanupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.case = lifecycle.BrainLifecycleTests("test_natural_return_drains_background_before_close")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    async def asyncTearDown(self):
        await self.case.asyncTearDown()

    async def exercise_timeout_chain(self, kind):
        case = self.case
        entered, finish = case.event(), case.event()
        videos, registered = [], []

        async def analyze(*args, **kwargs):
            task = asyncio.current_task()
            videos.append(task)
            case.tasks.append(task)
            try:
                await asyncio.Event().wait()
            finally:
                entered.set()
                await finish.wait()
                case.namespace["order"].append("video-done")

        module = ModuleType(lifecycle.PACKAGE + ".brain.video_analysis")
        module.analyze_bilibili_video_input = analyze
        sys.modules[module.__name__] = module

        async def short_legacy_wait(coroutine, timeout):
            return await asyncio.wait_for(coroutine, timeout=0.01)

        async def outer_legacy_wait(coroutine, timeout):
            return await asyncio.wait_for(coroutine, timeout=0.02)

        async def short_owned_wait(coroutine, timeout):
            return await wait_for_owned(coroutine, timeout=0.01)

        async def outer_owned_wait(coroutine, timeout):
            return await wait_for_owned(coroutine, timeout=0.02)

        runner = SimpleNamespace(
            brain=case.brain, goal_log=[], _save_goal_log=Mock(),
            _make_plan=AsyncMock(return_value=[
                {"action": "search"}, {"action": "watch", "max_videos": 1},
            ]),
            _search_videos=AsyncMock(return_value=[{
                "bvid": "BV1Q541167Qg", "title": "fake title",
            }]),
        )
        namespace = {
            "__package__": lifecycle.PACKAGE + ".services",
            "_global_config": {"agent": {
                "deep_learning_timeout_seconds": 30, "deep_learning_max_videos": 1,
            }},
            "asyncio": SimpleNamespace(wait_for=short_legacy_wait,
                                       TimeoutError=asyncio.TimeoutError, sleep=asyncio.sleep),
            "wait_for_owned": short_owned_wait,
            "random": SimpleNamespace(uniform=lambda *args: 0), "log": Mock(),
            "json": json, "datetime": datetime,
            "AGENT_MAX_SEARCH_RESULTS": 1, "AGENT_MAX_VIDEOS_PER_PLAN": 1,
        }
        runner._watch_videos = MethodType(lifecycle.load_node(
            "services/agent_service.py", "_watch_videos", namespace,
        ), runner)
        runner.run_goal = MethodType(lifecycle.load_node(
            "services/agent_service.py", "run_goal", namespace,
        ), runner)
        case.brain.agent_runner = runner
        dive = lifecycle.load_node("brain/_brain_loop.py", "_dive_async", {
            "self": case.brain, "dive_topic": "fake goal", "log": Mock(), "log_exception": Mock(),
        })
        agent = lifecycle.load_node("brain/_brain_auto.py", "_agent_goal_async", {
            "asyncio": SimpleNamespace(wait_for=outer_legacy_wait, TimeoutError=asyncio.TimeoutError),
            "wait_for_owned": outer_owned_wait,
            "AGENT_ENABLED": True, "config": {"agent": {"deep_learning_timeout_seconds": 30}},
            "log": Mock(), "log_exception": Mock(),
        })

        async def session(brain):
            work = dive() if kind == "dive" else agent(brain, "fake goal")
            registered.append(case.spawn(brain, work, kind))
            await entered.wait()
            return "stop"

        case.use_session(session)
        parent = case.task(case.brain.run())
        await case.reached(entered)
        await asyncio.sleep(0.04)
        self.assertFalse(parent.done(), "Brain released resources while video cleanup was pending")
        self.assertFalse(registered[0].done())
        self.assertFalse(videos[0].done())
        self.assertTrue(case.namespace["lock"].held)
        case.brain.bili.close.assert_not_awaited()
        case.namespace["_release_bot_lock"].assert_not_called()
        finish.set()
        self.assertEqual(await asyncio.wait_for(parent, timeout=2), "stop")
        self.assertTrue(videos[0].done())
        self.assertEqual(case.namespace["order"], ["acquire", "video-done", "close", "release"])
        case.assert_finished()

    async def test_dive_cancel_during_inner_video_timeout_keeps_resources_until_video_done(self):
        await self.exercise_timeout_chain("dive")

    async def test_agent_cancel_during_nested_timeouts_keeps_resources_until_video_done(self):
        await self.exercise_timeout_chain("agent")


if __name__ == "__main__":
    unittest.main()
