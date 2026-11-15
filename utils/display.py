"""utils/display.py — 显示/日志工具函数"""
from colorama import Fore, Style
import re
import sys

from .. import _runtime


_ANSI = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-_])"
    r"|\x9b[0-?]*[ -/]*[@-~]"
)
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_SENSITIVE_LABEL = re.compile(
    r"(?i)(?<![\w-])(?P<label>[\"']?(?:[a-z0-9]+[_ -])*"
    r"(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|"
    r"secret(?:[_ -]?key)?|password|passwd|pwd|sessdata|bili[_ -]?jct|dedeuserid|"
    r"(?:set[_ -]?)?cookies?|authorization|proxy[_ -]?(?:authorization|auth|"
    r"password|user(?:name)?)|auth(?:[_ -]?token)?|key|token|recovery[_ -]?(?:code|answer))"
    r"[\"']?)(?:[ \t]*[:=][ \t]*|[ \t]+(?=(?:Bearer|Basic)\b))"
)
_URL_AUTH = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://)[^/\s@\"'<>]+@")
_AUTH_TOKEN = re.compile(r"(?i)\b(Bearer|Basic)[ \t]+[^\s,;\"'<>}\]]+")


def _secret_value_end(text, start):
    """Skip one quoted/container value, or the whole unquoted header line."""
    closing = {"{": "}", "[": "]", "(": ")"}
    stack = []
    quote = None
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
                if not stack:
                    return index + 1
        elif char in "\"'":
            quote = char
        elif char in closing:
            stack.append(closing[char])
        elif stack and char == stack[-1]:
            stack.pop()
            if not stack:
                return index + 1
        elif not stack and char in "\r\n":
            return index
    return len(text)


def redact_sensitive_text(value):
    """Redact credential labels (including JSON), complete headers and URL auth."""
    text = _CONTROL.sub("", _ANSI.sub("", str(value) if value is not None else ""))
    parts = []
    cursor = 0
    for match in _SENSITIVE_LABEL.finditer(text):
        if match.start() < cursor:
            continue
        start = match.end()
        end = _secret_value_end(text, start)
        parts.append(text[cursor:start])
        quote = text[start:start + 1]
        parts.append(f"{quote}***{quote}" if quote in ("\"", "'") else "***")
        cursor = end
    parts.append(text[cursor:])
    text = _URL_AUTH.sub(r"\1***@", "".join(parts))
    return _AUTH_TOKEN.sub(r"\1 ***", text)


def mask_secret(value):
    if not value:
        return "(未配置)"
    if len(value) <= 12:
        return "*" * len(value)
    return f"{value[:6]}...{value[-4:]}"


def log(msg, level="INFO"):
    """Use the host logger in plugin mode, and retain the standalone terminal."""
    level = str(level).upper()
    # Only inspect this installation's already-loaded config; never initialize data.
    package = __package__.rsplit(".", 1)[0]
    loaded_config = sys.modules.get(f"{package}.core.config")
    cfg = vars(loaded_config).get("config") if loaded_config is not None else None
    system = cfg.get("system") if isinstance(cfg, dict) else None
    if isinstance(system, dict) and system.get("quiet_mode", False) and level in ("INFO", "SCAN", "DM"):
        return
    message = redact_sensitive_text(msg)
    if _runtime.plugin_mode:
        from astrbot.api import logger

        method = {
            "DEBUG": "debug", "DIAG": "debug", "TRACE": "debug",
            "WARN": "warning", "WARNING": "warning",
            "ERR": "error", "ERROR": "error",
            "CRITICAL": "critical", "FATAL": "critical",
        }.get(level, "info")
        getattr(logger, method)(f"[{level}] {message}")
        return
    colors = {
        "INFO": Fore.WHITE, "SUCCESS": Fore.GREEN, "WARN": Fore.YELLOW, "ERROR": Fore.RED,
        "SCAN": Fore.CYAN, "EYE": Fore.MAGENTA, "BRAIN": Fore.BLUE, "ACT": Fore.GREEN,
        "MEM": Fore.LIGHTBLUE_EX, "NOTE": Fore.WHITE, "COIN": Fore.YELLOW, "DIAG": Fore.LIGHTBLACK_EX,
        "LEARN": Fore.LIGHTMAGENTA_EX, "ENERGY": Fore.LIGHTCYAN_EX, "LOGIN": Fore.LIGHTYELLOW_EX,
        "CONFIG": Fore.LIGHTGREEN_EX, "KB": Fore.LIGHTMAGENTA_EX, "INTEREST": Fore.LIGHTYELLOW_EX,
        "COMMENT": Fore.LIGHTCYAN_EX, "EVOLVE": Fore.LIGHTMAGENTA_EX, "SUBTITLE": Fore.CYAN
    }
    icons = {
        "SCAN": "[SCAN]", "EYE": "[EYE]", "BRAIN": "[BRAIN]", "ACT": "[FAST]", "MEM": "[MEM]", "NOTE": "[NOTE]",
        "WARN": "[WARN]", "ERROR": "[ERROR]", "SUCCESS": "[OK]", "COIN": "[COIN]", "INFO": "[INFO]", "DIAG": "[DIAG]",
        "LEARN": "[LEARN]", "ENERGY": "[FAST]", "LOGIN": "[LOGIN]", "CONFIG": "[CONFIG]", "KB": "[KB]",
        "INTEREST": "[TARGET]", "COMMENT": "[MSG]", "DM": "[DM]", "EVOLVE": "[EVOLVE]", "SUBTITLE": "[SUB]"
    }

    color = colors.get(level, Fore.WHITE)
    icon = icons.get(level, '[INFO]')

    # [FIX] Windows GBK终端无法打印emoji，用ASCII标签替代
    text = f"{icon} [{level:<7}] {message}"
    try:
        print(f"{color}{text}{Style.RESET_ALL}")
    except UnicodeEncodeError:
        # Some embedded/background Windows processes still expose a GBK stream.
        # Replace only unsupported glyphs so logging can never abort real work.
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        safe_text = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
        try:
            print(safe_text)
        except UnicodeEncodeError:
            sys.stdout.buffer.write((safe_text + "\n").encode(encoding, errors="replace"))


def log_exception(action, exc):
    """Log a diagnostic stack without exception text, responses, source or locals.

    Use the exception's own traceback so task done-callbacks work outside except.
    Do not use logger.exception/format_exception: both can expose API credentials.
    """
    lines = [f"{action}: {type(exc).__name__}"]
    tb = exc.__traceback__
    while tb is not None:
        code = tb.tb_frame.f_code
        # Full paths may contain user names or credentials; only identify the file.
        filename = code.co_filename.replace("\\", "/").rsplit("/", 1)[-1]
        lines.append(f"  at {filename}:{tb.tb_lineno} in {code.co_name}")
        tb = tb.tb_next
    log("\n".join(lines), "ERROR")
