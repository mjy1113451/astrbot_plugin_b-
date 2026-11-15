"""bili/auth.py — B站 登录/认证"""
import asyncio
import json
import os
import tempfile
import time
import random
import qrcode
from io import BytesIO
from pathlib import Path

from colorama import Fore, Style
from bilibili_api import Credential
from bilibili_api.login_v2 import QrCodeLoginEvents, QrCodeLogin

from ..core.config import COOKIE_FILE, config, save_config
from ..core.user_data import managed_file_path, resolve_output_path, write_private_json
from .. import _runtime
from ..utils.display import log, log_exception
from .throttle import _bili_throttle, _bili_trigger_cooldown

def clear_login_info():
    """清除登录信息"""
    if os.path.exists(COOKIE_FILE):
        try:
            os.remove(COOKIE_FILE)
            print(f"{Fore.GREEN}[OK] 登录信息已清除！{Style.RESET_ALL}")
        except Exception as e:
            print(f"{Fore.RED}[ERROR] 清除失败: {e}{Style.RESET_ALL}")
    else:
        print(f"{Fore.YELLOW}[WARN]  没有找到登录信息！{Style.RESET_ALL}")



def is_bili_logged_in():
    """检查是否已登录（文件存在且含有效 SESSDATA 和 DedeUserID）"""
    try:
        path = managed_file_path(COOKIE_FILE)
        if not path.exists():
            return False
        with path.open('r', encoding='utf-8') as f:
            cookies = json.load(f)
        return bool(cookies.get('SESSDATA', '').strip()) and bool(cookies.get('DedeUserID', '').strip())
    except Exception:
        return False


def check_login_status():
    """检查登录状态"""
    if not os.path.exists(COOKIE_FILE):
        print(f"{Fore.RED}[ERROR] Cookie文件不存在！{Style.RESET_ALL}")
        return

    try:
        with managed_file_path(COOKIE_FILE).open('r', encoding='utf-8') as f:
            cookies = json.load(f)

        print(f"{Fore.CYAN}════════════════════════════════════════════════{Style.RESET_ALL}")
        print(f"{Fore.CYAN}                登录状态检查{Style.RESET_ALL}")
        print(f"{Fore.CYAN}════════════════════════════════════════════════{Style.RESET_ALL}")

        print(f"\n{Fore.YELLOW}📋 Cookie信息:{Style.RESET_ALL}")
        for key, value in cookies.items():
            if key in ['SESSDATA', 'bili_jct']:
                print(f"  • {key}: {value[:10]}...{value[-5:]}")
            elif key == 'DedeUserID':
                print(f"  • {key}: {value}")

        print(f"\n{Fore.YELLOW}[FILE] 文件信息:{Style.RESET_ALL}")
        print(f"  • 文件路径: {COOKIE_FILE}")
        print(f"  • 文件大小: {os.path.getsize(COOKIE_FILE)} 字节")
        print(f"  • 修改时间: {time.ctime(os.path.getmtime(COOKIE_FILE))}")

        print(f"\n{Fore.CYAN}════════════════════════════════════════════════{Style.RESET_ALL}")

    except Exception as e:
        print(f"{Fore.RED}[ERROR] 读取Cookie文件失败: {e}{Style.RESET_ALL}")


# ==============================================================================
# 📚 知识库分类系统
# ==============================================================================

async def login_bilibili():
    # Each attempt owns only its uniquely allocated QR file. Cleanup also runs
    # on timeout, cancellation, or credential-save failure; never sweep a folder.
    created_paths = []
    try:
        return await _login_bilibili(created_paths)
    finally:
        for path in created_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                log_exception("删除二维码图片失败", exc)


async def _login_bilibili(created_paths):
    log("正在初始化登录...", "LOGIN")
    
    log("正在请求二维码数据...", "LOGIN")
    try:
        qr_login = QrCodeLogin()
        await qr_login.generate_qrcode()
        login_key = qr_login._QrCodeLogin__qr_key
        url = qr_login._QrCodeLogin__qr_link
    except json.decoder.JSONDecodeError as e:
        log_exception("无法获取登录二维码：B站返回异常 JSON", e)
        return False
    except Exception as e:
        log_exception("获取登录二维码失败", e)
        return False
    log(f"获取到登录链接", "LOGIN")

    if not _runtime.plugin_mode:
        print("\n" + "="*50)
        print("           📱 B站登录二维码")
        print("="*50)

    # QR credentials stay in this profile, never the package or Android gallery.
    qr_dir = resolve_output_path("qr_codes")
    try:
        qr_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix="bilibili_login_", suffix=".png", dir=qr_dir,
                                         delete=False) as qr_file:
            qr_path = Path(qr_file.name)
            created_paths.append(qr_path)
        qr_png = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=12,
            border=4,
        )
        qr_png.add_data(url)
        qr_png.make(fit=True)
        qr_png.make_image(fill_color="black", back_color="white").save(qr_path)
        log(f"二维码已保存: {qr_path}", "LOGIN")
    except Exception as e:
        log_exception("保存登录二维码失败", e)

    if not _runtime.plugin_mode:
        print("📱 终端二维码预览：")
        qr_term = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            border=1,
        )
        qr_term.add_data(url)
        qr_term.make(fit=True)
        qr_term.print_ascii(invert=True)
        print("\n扫描二维码后，请在手机上确认登录")

    scan_detected = False
    cred = None
    poll_errors = 0  # 连续错误计数
    max_poll_errors = 10  # 最多允许 10 次连续网络错误
    while True:
        try:
            status = await qr_login.check_state()
            poll_errors = 0  # 成功则重置

            if status == QrCodeLoginEvents.DONE:
                log("扫码成功！登录完成！", "SUCCESS")
                cred = qr_login.get_credential()
                break
            elif status == QrCodeLoginEvents.SCAN:
                # SCAN = 未扫码
                if not _runtime.plugin_mode:
                    print("⏳ 等待扫码...", end="\r")
            elif status == QrCodeLoginEvents.CONF:
                # CONF = 已扫码，等待确认
                if not scan_detected:
                    log("[OK] 二维码已扫描，请在手机上确认登录...", "LOGIN")
                    scan_detected = True
                if not _runtime.plugin_mode:
                    print("📱 请在手机上点击确认...", end="\r")
            elif status == QrCodeLoginEvents.TIMEOUT:
                # TIMEOUT = 已失效
                log("二维码已过期，请重新运行", "ERROR")
                return False

            await asyncio.sleep(2)

        except json.decoder.JSONDecodeError as e:
            poll_errors += 1
            log_exception(f"登录状态查询返回异常 JSON ({poll_errors}/{max_poll_errors})", e)
            if poll_errors >= max_poll_errors:
                log("连续网络错误过多，请检查网络后重试", "ERROR")
                return False
            await asyncio.sleep(3)
        except Exception as e:
            poll_errors += 1
            log_exception(f"登录状态查询失败 ({poll_errors}/{max_poll_errors})", e)
            if poll_errors >= max_poll_errors:
                log("连续网络错误过多，登录中止", "ERROR")
                return False
            await asyncio.sleep(3)

    if cred is None:
        log("登录失败：未获取到凭据", "ERROR")
        return False

    log("正在提取并保存 Cookie...", "LOGIN")
    try:
        cookies = {
            "SESSDATA": cred.sessdata,
            "bili_jct": cred.bili_jct,
            "DedeUserID": cred.dedeuserid,
            "buvid3": getattr(cred, 'buvid3', ''),
            "ac_time_value": getattr(cred, 'ac_time_value', '')
        }

        write_private_json(COOKIE_FILE, cookies, indent=4)

        log(f"成功！Cookie 已保存至: {COOKIE_FILE}", "SUCCESS")
        return True

    except Exception as e:
        log_exception("保存登录凭据失败", e)
        return False


# ==============================================================================
# [FIX] 安全Task回调：防止 asyncio.create_task 异常静默丢失
# ==============================================================================
