"""Explicit, per-installation permission for supported Bilibili account writes."""
from collections.abc import Mapping


ACTIONS = {
    "video_like": "视频点赞",
    "coin": "投币",
    "favorite": "平台收藏",
    "follow_up": "关注 UP 主",
    "unfollow_user": "取消关注",
    "public_comment": "公开评论与回复",
    "comment_like": "评论点赞",
    "private_reply": "发送私信",
    "send_danmaku": "发送弹幕",
    "danmaku_like": "弹幕点赞",
}


def defaults():
    return {"enabled": False, "actions": dict.fromkeys(ACTIONS, False)}


def allowed(action, config=None):
    """Read live permission without treating legacy automation or review as consent."""
    if not isinstance(action, str) or action not in ACTIONS:
        return False
    try:
        if config is None:
            from .config import load_config

            config = load_config()
        if not isinstance(config, Mapping):
            return False
        permission = config.get("ai_permissions")
        if not isinstance(permission, Mapping) or permission.get("enabled") is not True:
            return False
        actions = permission.get("actions")
        return isinstance(actions, Mapping) and actions.get(action) is True
    except Exception:
        return False


def require(action, config=None):
    if not allowed(action, config):
        raise PermissionError("Platform action is not authorized")
