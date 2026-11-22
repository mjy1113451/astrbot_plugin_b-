"""Deadline-owned coroutines must drain even during repeated cancellation."""
import asyncio
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("_owned_async_tasks", ROOT / "utils/async_tasks.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
wait_for_owned = MODULE.wait_for_owned


class OwnedAsyncTasksTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.started = asyncio.Event()
        self.closing = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = asyncio.Event()
        self.worker = None
        self.parent = None

    async def asyncTearDown(self):
        self.release.set()
        if self.parent is not None and not self.parent.done():
            self.parent.cancel()
        if self.parent is not None:
            await asyncio.gather(self.parent, return_exceptions=True)

    async def work(self):
        self.worker = asyncio.current_task()
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.closing.set()
            await self.release.wait()
            self.closed.set()

    async def test_normal_result(self):
        async def work():
            return "result"

        self.assertEqual(await wait_for_owned(work(), 1), "result")

    async def test_normal_exception(self):
        error = ValueError("synthetic failure")

        async def work():
            raise error

        with self.assertRaises(ValueError) as caught:
            await wait_for_owned(work(), 1)
        self.assertIs(caught.exception, error)

    async def test_timeout_waits_for_child_finally(self):
        self.parent = asyncio.create_task(wait_for_owned(self.work(), 0.01))
        await self.closing.wait()
        self.assertFalse(self.parent.done())
        self.assertFalse(self.closed.is_set())
        self.assertEqual(self.worker.cancelling(), 1)
        self.release.set()
        with self.assertRaises(asyncio.TimeoutError):
            await self.parent
        self.assertTrue(self.closed.is_set())

    async def test_caller_cancellation_waits_for_child_finally(self):
        self.parent = asyncio.create_task(wait_for_owned(self.work(), 10))
        await self.started.wait()
        self.parent.cancel()
        await self.closing.wait()
        self.assertFalse(self.parent.done())
        self.release.set()
        with self.assertRaises(asyncio.CancelledError):
            await self.parent
        self.assertTrue(self.closed.is_set())

    async def test_cancellation_during_timeout_drain_never_recancels_child(self):
        self.parent = asyncio.create_task(wait_for_owned(self.work(), 0.01))
        await self.closing.wait()
        for _ in range(3):
            self.parent.cancel()
            await asyncio.sleep(0)
        self.assertFalse(self.parent.done())
        self.assertFalse(self.closed.is_set())
        self.assertEqual(self.worker.cancelling(), 1)
        self.release.set()
        with self.assertRaises(asyncio.CancelledError):
            await self.parent
        self.assertTrue(self.closed.is_set())

    async def test_already_cancelling_child_is_not_cancelled_again(self):
        async def work():
            self.worker = asyncio.current_task()
            self.worker.cancel()
            try:
                await asyncio.sleep(0)
            finally:
                self.closing.set()
                await self.release.wait()
                self.closed.set()

        self.parent = asyncio.create_task(wait_for_owned(work(), 0.01))
        await self.closing.wait()
        await asyncio.sleep(0.03)
        self.assertFalse(self.parent.done())
        self.assertEqual(self.worker.cancelling(), 1)
        self.release.set()
        with self.assertRaises(asyncio.TimeoutError):
            await self.parent
        self.assertTrue(self.closed.is_set())

    async def test_nested_deadlines_cannot_detach_inner_cleanup(self):
        async def inner():
            return await wait_for_owned(self.work(), 0.01)

        self.parent = asyncio.create_task(wait_for_owned(inner(), 0.02))
        await self.closing.wait()
        await asyncio.sleep(0.03)
        self.parent.cancel()
        await asyncio.sleep(0)
        self.assertFalse(self.parent.done())
        self.assertFalse(self.closed.is_set())
        self.assertEqual(self.worker.cancelling(), 1)
        self.release.set()
        with self.assertRaises(asyncio.CancelledError):
            await self.parent
        self.assertTrue(self.closed.is_set())

    async def test_timeout_before_first_worker_step_still_enters_finally(self):
        self.release.set()
        with self.assertRaises(asyncio.TimeoutError):
            await wait_for_owned(self.work(), 0)
        self.assertTrue(self.started.is_set())
        self.assertTrue(self.closed.is_set())

    async def test_late_child_exception_is_observed_and_timeout_preserved(self):
        async def work():
            self.started.set()
            try:
                await asyncio.Event().wait()
            finally:
                raise ValueError("synthetic late failure")

        with self.assertRaises(asyncio.TimeoutError):
            await wait_for_owned(work(), 0.01)
        self.assertTrue(self.started.is_set())


if __name__ == "__main__":
    unittest.main()
