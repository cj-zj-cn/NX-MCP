# -*- coding: utf-8 -*-
# nx: threaded
# ↑ 不能删：让 NX 在主解释器里运行本脚本，否则监听器会随一次性子解释器一起被销毁。
# NX 启动时自动加载 NX-MCP 监听器。
# 让 NX 能找到本文件：把 NX-MCP 文件夹设为 UGII_USER_DIR，或加入 custom_dirs.dat（见 README.md）。
import os
import sys
import traceback

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _start():
    try:
        import nx_bridge_listener
        nx_bridge_listener.start(force_restart=False, listing=False)
    except Exception:
        try:
            import nx_bridge_listener
            nx_bridge_listener._log("自动启动失败:\n" + traceback.format_exc())
        except Exception:
            pass


def ufsta(*args):
    _start()


def startup(*args):
    _start()


Startup = startup


def GetUnloadOption(*args):
    import NXOpen
    return int(NXOpen.Session.LibraryUnloadOption.AtTermination)


# 不确定 NX 对 startup 目录里的 Python 文件调用哪个入口，模块加载时也启动一次（重复调用会被忽略）
_start()
