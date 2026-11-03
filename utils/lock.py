"\"\"\"Process-owned single-instance lock for bot and monitor processes.\"\"\"\nimport atexit\nimport os\n\n\nfrom colorama import Fore, Style\nfrom core.config import DATA_DIR\n\n\n\n\n_BOT_LOCK_FILE = None\n_bot_lock_acquired = False\n\n\n\n\ndef _lock_path() -> str:\n    global _BOT_LOCK_FILE\n    if _BOT_LOCK_FILE is None:\n        _BOT_LOCK_FILE = os.path.join(DATA_DIR, \"bot.lock\")\n    return _BOT_LOCK_FILE\n\n\n\n\ndef _read_lock_pid(path: str) -> int | None:\n    try:\n        with open(path, \"r\", encoding=\"utf-8\") as file:\n            pid = int(file.read().strip())\n        return pid if pid > 0 else None\n    except (OSError, ValueError):\n        return None\n\n\n\n\ndef _pid_is_running_windows(pid: int) -> bool:
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


def _pid_is_running(pid: int) -> bool:\n    if os.name == \"nt\":\n        try:\n            import psutil\n            return bool(psutil.pid_exists(pid))\n        except ImportError:\n            pass\n    try:\n        os.kill(pid, 0)\n        return True\n    except ProcessLookupError:\n        return False\n    except PermissionError:\n        return True\n    except OSError:\n        return False\n\n\n\n\ndef bot_lock_status(clean_stale: bool = False) -> dict[str, int | bool | None]:\n    \"\"\"Return lock ownership and optionally remove only a confirmed stale lock.\"\"\"\n    path = _lock_path()\n    if not os.path.exists(path):\n        return {\"locked\": False, \"pid\": None, \"stale\": False}\n    pid = _read_lock_pid(path)\n    active = pid is not None and _pid_is_running(pid)\n    stale = not active\n    if stale and clean_stale:\n        try:\n            if _read_lock_pid(path) == pid:\n                os.remove(path)\n        except OSError:\n            pass\n        return {\"locked\": False, \"pid\": pid, \"stale\": True}\n    return {\"locked\": active, \"pid\": pid, \"stale\": stale}\n\n\n\n\ndef _acquire_bot_lock() -> bool:\n    \"\"\"Acquire the lock atomically and never overwrite another process's lock.\"\"\"\n    global _bot_lock_acquired\n    path = _lock_path()\n    os.makedirs(DATA_DIR, exist_ok=True)\n\n\n    for _ in range(2):\n        try:"
