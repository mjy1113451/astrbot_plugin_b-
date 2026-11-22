"""Local coroutine ownership across timeouts and caller cancellation."""
import asyncio


async def wait_for_owned(coroutine, timeout):
    """Wait with a deadline, but always drain the owned coroutine before exiting."""
    try:
        task = asyncio.create_task(coroutine)
    except BaseException:
        coroutine.close()
        raise
    try:
        done, _ = await asyncio.wait((task,), timeout=timeout)
        if not done:
            raise asyncio.TimeoutError
        return task.result()
    finally:
        if not task.done():
            if not task.cancelling():
                # The coroutine must enter its resource try/finally even on early cancel.
                asyncio.get_running_loop().call_soon(task.cancel)
            cancelled = None
            while not task.done():
                try:
                    await asyncio.wait((task,))
                except asyncio.CancelledError as exc:
                    cancelled = exc
            if not task.cancelled():
                task.exception()
            if cancelled is not None:
                raise cancelled
        elif not task.cancelled():
            task.exception()
