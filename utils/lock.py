"""Process-owned single-instance lock for bot and monitor processes."""
import atexit
import os

from colorama import Fore, Style
from core.config import DATA_DIR


_BOT_LOCK_FILE = None
_bot_lock_acquired = False


def _lock_path() -> str:
    global _BOT_LOCK_FILE
    if _BOT_LOCK_FILE is None:
        _BOT_LOCK_FILE = os.path.join(DATA_DIR, "bot.lock")
    return _BOT_LOCK_FILE


def _read_lock_pid(path: str) -> int | None:
    try:
        with open(path, "r", encoding="utf-8") as file:
            pid = int(file.read().strip())
        return pid if pid > 0 else None
    except (OSError, ValueError):
        return None


def _pid_is_running_windows(pid: int) -> bool:
    """Windows liveness check that never terminates the target process.

    os.kill(pid, 0) on Windows maps to TerminateProcess for signals other
    than Ctrl+C/Ctrl+Break, so probing a live PID would kill it. Use
    OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION) + GetExitCodeProcess
    instead, declaring argtypes/restype so the HANDLE is not truncated.
    """
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    ERROR_ACCESS_DENIED = 5
    STILL_ACTIVE = 259

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # Access denied means the process exists but is protected.
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _pid_is_running(pid: int) -> bool:
    if os.name == "nt":
        try:
            import psutil
            return bool(psutil.pid_exists(pid))
        except ImportError:
            return _pid_is_running_windows(pid)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def bot_lock_status(clean_stale: bool = False) -> dict[str, int | bool | None]:
    """Return lock ownership and optionally remove only a confirmed stale lock."""
    path = _lock_path()
    if not os.path.exists(path):
        return {"locked": False, "pid": None, "stale": False}
    pid = _read_lock_pid(path)
    active = pid is not None and _pid_is_running(pid)
    stale = not active
    if stale and clean_stale:
        try:
            if _read_lock_pid(path) == pid:
                os.remove(path)
        except OSError:
            pass
        return {"locked": False, "pid": pid, "stale": True}
    return {"locked": active, "pid": pid, "stale": stale}


def _acquire_bot_lock() -> bool:
    """Acquire the lock atomically and never overwrite another process's lock."""
    global _bot_lock_acquired
    path = _lock_path()
    os.makedirs(DATA_DIR, exist_ok=True)

    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            status = bot_lock_status(clean_stale=True)
            if status["stale"]:
                print(f"{Fore.YELLOW}[LOCK] Clearing stale lock file (PID: {status['pid']}){Style.RESET_ALL}")
                continue
            pid = status["pid"] or "unknown"
            print(
                f"{Fore.RED}[LOCK] A bot instance is already running (PID: {pid})."
                f"\n[LOCK] Stop that instance before starting another one.{Style.RESET_ALL}"
            )
            return False
        except OSError as exc:
            print(f"{Fore.RED}[LOCK] Cannot create lock file: {exc}{Style.RESET_ALL}")
            return False

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(str(os.getpid()))
            _bot_lock_acquired = True
            atexit.register(_release_bot_lock)
            return True
        except OSError as exc:
            try:
                os.close(fd)
            except OSError:
                pass
            print(f"{Fore.RED}[LOCK] Cannot write lock file: {exc}{Style.RESET_ALL}")
            return False
    return False


def _release_bot_lock():
    """Release only the lock written by this process."""
    global _bot_lock_acquired
    path = _lock_path()
    if not _bot_lock_acquired:
        return
    try:
        if _read_lock_pid(path) == os.getpid():
            os.remove(path)
    except OSError:
        pass
    finally:
        _bot_lock_acquired = False
