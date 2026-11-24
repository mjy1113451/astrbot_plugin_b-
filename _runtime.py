"""Package-local runtime settings shared without importing business modules."""
from pathlib import Path


PLUGIN_NAME = "astrbot_plugin_bilibili_learning_bot"
plugin_mode = False
data_root = None
brain_runtime = None


def configure_plugin(root):
    global plugin_mode, data_root
    resolved = Path(root).expanduser().resolve()
    if data_root is not None and data_root != resolved:
        raise RuntimeError("Plugin data directory is already bound for this package")
    plugin_mode = True
    data_root = resolved


def configure_standalone():
    global plugin_mode
    if data_root is not None:
        raise RuntimeError("Cannot switch an initialized plugin to standalone mode")
    plugin_mode = False
