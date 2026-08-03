"""ob_bridge/health.py — OB 健康检查 + 自动拉起 + 模式自动切换"""

import asyncio
import os
import shlex
import subprocess
import sys

from ob_bridge.client import OBClient
from ob_bridge.types import OBMode, OBStatus
from utils.display import log


# 安全:拒绝包含 shell 元字符或控制字符的命令,避免命令注入
_BAD_CMD_CHARS = ("\n", "\r", "\x00")


def _safe_parse_launch_command(launch_command: str) -> list[str] | None:
    """将启动命令解析为 argv 列表

    Returns:
        argv 列表;若命令为空/包含危险字符则返回 None

    说明:
        使用 ``shlex.split`` 按 POSIX 风格拆分(支持引号转义),不经过 shell 解析。
        与 ``subprocess.Popen(..., shell=False)`` 配合使用,可消除
        ``shell=True`` 带来的命令注入风险(CWE-78)。
    """
    if not isinstance(launch_command, str):
        return None
    stripped = launch_command.strip()
    if not stripped:
        return None
    if any(c in stripped for c in _BAD_CMD_CHARS):
        return None
    try:
        argv = shlex.split(stripped, posix=True)
    except ValueError:
        # 引号未闭合等解析错误
        return None
    if not argv:
        return None
    return argv


async def detect_ob(base_url: str = "http://127.0.0.1:8420", timeout: float = 5.0) -> OBStatus:
    """检测 OB 是否在线"""
    client = OBClient(base_url=base_url, timeout=timeout)
    online = await client.health_check()
    return OBStatus(
        online=online,
        url=base_url,
        error="" if online else f"无法连接到 {base_url}",
    )


def launch_ob(launch_cwd: str = "", launch_command: str = "openbiliclaw serve") -> bool:
    """尝试后台拉起 OB 服务

    Args:
        launch_cwd: OB 项目目录
        launch_command: 启动命令

    Returns:
        True 启动成功，False 失败
    """
    try:
        cwd = launch_cwd if launch_cwd else None
        if cwd and not os.path.isdir(cwd):
            log(f"[OB] 启动目录不存在: {cwd}", "WARN")
            return False

        # 安全:解析为 argv 并禁用 shell,防止命令注入(CWE-78)。
        # 配置项 launch_command 经由 web panel /api/config 可被已认证用户改写,
        # 若继续使用 shell=True,恶意命令(如 "; rm -rf /")会被直接执行。
        argv = _safe_parse_launch_command(launch_command)
        if argv is None:
            log("[OB] 启动命令不合法,已拒绝执行", "WARN")
            return False

        log(f"[OB] 尝试启动: {launch_command}" + (f" (cwd={cwd})" if cwd else ""), "CONFIG")

        if sys.platform == "win32":
            # Windows: 使用 CREATE_NEW_CONSOLE 打开新窗口，不阻塞
            subprocess.Popen(
                argv,
                cwd=cwd,
                shell=False,
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
        else:
            # Linux/macOS: 后台运行，输出重定向
            subprocess.Popen(
                argv,
                cwd=cwd,
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        return True
    except Exception as e:
        log(f"[OB] 启动失败: {e}", "WARN")
        return False


async def ensure_ob_ready(
    base_url: str = "http://127.0.0.1:8420",
    auto_launch: bool = True,
    launch_cwd: str = "",
    launch_command: str = "openbiliclaw serve",
    wait_seconds: float = 15.0,
    poll_interval: float = 2.0,
) -> OBStatus:
    """确保 OB 在线：先检测 → 不在线则自动拉起 → 轮询等待就绪

    Returns:
        OBStatus 最终状态
    """
    # 1. 先检测是否已在线
    status = await detect_ob(base_url)
    if status.online:
        log(f"[OB] ✅ 已在线: {base_url}", "SUCCESS")
        return status

    # 2. 不在线，尝试自动拉起
    if not auto_launch:
        return status  # 不自动拉起，直接返回离线状态

    log(f"[OB] ⚠️ 不在线，尝试自动拉起...", "CONFIG")
    launched = launch_ob(launch_cwd, launch_command)
    if not launched:
        return status

    # 3. 轮询等待就绪
    log(f"[OB] ⏳ 等待 OB 就绪（最多 {wait_seconds}s）...", "INFO")
    elapsed = 0.0
    while elapsed < wait_seconds:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
        status = await detect_ob(base_url)
        if status.online:
            log(f"[OB] ✅ OB 就绪（{elapsed:.0f}s）: {base_url}", "SUCCESS")
            return status

    log(f"[OB] ❌ OB 启动超时（{wait_seconds}s），将降级为旧推荐流", "WARN")
    return OBStatus(online=False, url=base_url, error=f"启动超时 ({wait_seconds}s)")


async def auto_detect_mode(client: OBClient) -> OBMode:
    """自动检测并设置推荐模式

    规则：
    - 画像中有用户自定义兴趣 → 精准模式
    - 画像为空 → 探索模式
    """
    has_interests = await client.has_interests()
    if has_interests:
        client.set_mode(OBMode.PRECISION)
        log("[OB] 🎯 精准模式：检测到用户画像", "INFO")
    else:
        client.set_mode(OBMode.EXPLORE)
        log("[OB] 🔍 探索模式：画像为空，广撒网帮你发现兴趣", "INFO")
    return client.mode
