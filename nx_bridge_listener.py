# -*- coding: utf-8 -*-
# nx: threaded
# ↑ 不能删：nxpythonldr.dll 看到 "nx: threaded" 才在主解释器里运行本脚本。
#   没有它时 Designcenter 2606 用一次性的子解释器，播放结束即销毁，监听 socket 随之关闭。
"""
NX-MCP 监听器：在 NX 图形界面里运行，让外部的 MCP 服务器把 NX Open Python 代码
发进当前 NX 会话执行。

启动方式：
  - 手动：在 NX 里 Alt+F8（工具 → 操作记录 → 播放），选择本文件。再次播放 = 重启监听器。
  - 自动：见 startup/nx_mcp_autostart.py 和 README.md。

原理：
  NX Open 只能在 NX 主线程调用。这里不用后台线程，而是用 Win32 SetTimer 在 NX 主线程
  挂一个定时回调，每 50ms 用非阻塞 socket 检查一次本机端口；有请求就直接在主线程执行。
  只监听 127.0.0.1，每次请求都要带上启动时随机生成的 token（写在 %TEMP%\\nx_mcp_bridge\\ 里）。

兼容 NX 12（Python 3.6）和 NX 2xxx / Designcenter（Python 3.1x）。
"""

import ast
import base64
import contextlib
import ctypes
import io
import json
import linecache
import os
import secrets
import socket
import sys
import tempfile
import time
import traceback
from ctypes import wintypes

import NXOpen

HOST = "127.0.0.1"
BASE_PORT = int(os.environ.get("NX_MCP_PORT", "48765"))
TICK_MS = 50
INFO_DIR = os.path.join(tempfile.gettempdir(), "nx_mcp_bridge")
LOG_FILE = os.path.join(INFO_DIR, "listener.log")
STATE_ATTR = "_nx_mcp_bridge_state"
MAX_REQUEST_BYTES = 64 * 1024 * 1024
MAX_TEXT = 200000

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_TIMERPROC = ctypes.WINFUNCTYPE(None, wintypes.HWND, wintypes.UINT, ctypes.c_size_t, wintypes.DWORD)
_user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, _TIMERPROC]
_user32.SetTimer.restype = ctypes.c_size_t
_user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
_user32.KillTimer.restype = wintypes.BOOL

_CANNED_VIEWS = {
    "trimetric": "Trimetric",
    "isometric": "Isometric",
    "top": "Top",
    "bottom": "Bottom",
    "front": "Front",
    "back": "Back",
    "left": "Left",
    "right": "Right",
}


class UnsupportedInterpreter(RuntimeError):
    pass


def _check_interpreter():
    """定时回调由 ctypes 经 PyGILState_Ensure 进入 Python。

    NX 12 把脚本放在子解释器里运行，并且空闲时一直持有 GIL：当前线程状态和 GILState 记录的
    不是同一个，回调一触发就会在自己持有的 GIL 上死锁，NX 界面卡死。这里提前检测并拒绝启动。
    （Designcenter 2606 在主解释器里运行脚本，两者相同，可以正常工作。）
    """
    api = ctypes.pythonapi
    api.PyThreadState_Get.restype = ctypes.c_void_p
    api.PyGILState_GetThisThreadState.restype = ctypes.c_void_p
    current = api.PyThreadState_Get()
    gilstate = api.PyGILState_GetThisThreadState()
    if current != gilstate:
        raise UnsupportedInterpreter(
            "这个 NX 版本把 Python 脚本放在子解释器里运行（Python %s），实时控制会让 NX 卡死，"
            "已停止启动监听器。这个版本请用命令行批处理方式（run_journal.exe）。"
            % sys.version.split()[0])


def _log(msg):
    line = "%s  %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        if not os.path.isdir(INFO_DIR):
            os.makedirs(INFO_DIR)
        with io.open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _truncate(text, limit=MAX_TEXT):
    if text is None or len(text) <= limit:
        return text
    return text[:limit] + "\n...（已截断，共 %d 字符）" % len(text)


def _to_jsonable(value):
    if value is None:
        return None
    try:
        json.dumps(value)
        return value
    except Exception:
        pass
    text = repr(value)
    ident = getattr(value, "JournalIdentifier", None)
    if ident:
        text += "  [JournalIdentifier=%s]" % ident
    return _truncate(text, 20000)


class Bridge(object):
    def __init__(self):
        self.session = NXOpen.Session.GetSession()
        self.namespace = {}
        self.marks = []          # 每次 exec 设置的撤销标记，最新的在最后
        self.busy = False
        self.sock = None
        self.port = None
        self.timer_id = 0
        self.token = secrets.token_hex(16)
        self.started = time.time()
        self.requests = 0
        self.exec_counter = 0
        self.info_file = os.path.join(INFO_DIR, "%d.json" % os.getpid())
        # 回调对象必须一直被引用，否则会被回收导致 NX 崩溃
        self._timer_proc = _TIMERPROC(self._on_timer)

    # ---------------------------------------------------------------- 生命周期
    def start(self):
        _check_interpreter()
        last_err = None
        for port in range(BASE_PORT, BASE_PORT + 20):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                s.bind((HOST, port))
                s.listen(16)
                s.setblocking(False)
                self.sock, self.port = s, port
                break
            except OSError as e:
                last_err = e
                s.close()
        if self.sock is None:
            raise RuntimeError("找不到可用端口 %d-%d: %s" % (BASE_PORT, BASE_PORT + 19, last_err))

        self.timer_id = _user32.SetTimer(None, 0, TICK_MS, self._timer_proc)
        if not self.timer_id:
            err = ctypes.get_last_error()
            self.sock.close()
            raise ctypes.WinError(err)

        if not os.path.isdir(INFO_DIR):
            os.makedirs(INFO_DIR)
        info = {
            "pid": os.getpid(),
            "port": self.port,
            "token": self.token,
            "nx_base_dir": os.environ.get("UGII_BASE_DIR", ""),
            "python": sys.version.split()[0],
            "started": self.started,
        }
        with io.open(self.info_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(info, ensure_ascii=False))
        _log("监听已启动: 端口 %d, pid %d, %s" % (self.port, os.getpid(), info["nx_base_dir"]))
        # 诊断：解释器退出时留记录（排查播放结束后端口被关的问题）
        try:
            import atexit
            atexit.register(_log, "Python 解释器正在退出 (atexit)")
            api = ctypes.pythonapi
            api.PyInterpreterState_Get.restype = ctypes.c_void_p
            api.PyInterpreterState_Main.restype = ctypes.c_void_p
            _log("解释器: 当前=%s 主=%s" % (hex(api.PyInterpreterState_Get() or 0),
                                          hex(api.PyInterpreterState_Main() or 0)))
        except Exception as e:
            _log("诊断信息获取失败: %r" % (e,))

    def stop(self):
        if self.timer_id:
            _user32.KillTimer(None, self.timer_id)
            self.timer_id = 0
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None
        try:
            with io.open(self.info_file, "r", encoding="utf-8") as f:
                if json.loads(f.read()).get("token") == self.token:
                    os.remove(self.info_file)
        except Exception:
            pass
        _log("监听已停止")

    # ---------------------------------------------------------------- 网络
    def _on_timer(self, hwnd, msg, id_event, tick):
        # 诊断：启动后 60 秒内每 5 秒记一次心跳，确认定时器和 socket 还活着
        try:
            now = time.time()
            if now - self.started < 60 and now - getattr(self, "_last_beat", 0) >= 5:
                self._last_beat = now
                _log("心跳: sock=%s fileno=%s" % (self.sock is not None,
                                                  self.sock.fileno() if self.sock is not None else None))
        except Exception:
            pass
        # 执行代码时 NX 可能处理消息并再次触发定时器，用 busy 防止重入
        if self.busy or self.sock is None:
            return
        self.busy = True
        try:
            for _ in range(8):
                if self.sock is None:  # 执行的代码里可能重启/停止了监听器
                    break
                try:
                    conn, _addr = self.sock.accept()
                except OSError:
                    break
                self._serve(conn)
        except Exception:
            _log("定时回调异常:\n" + traceback.format_exc())
        finally:
            self.busy = False

    def _serve(self, conn):
        resp = None
        try:
            conn.setblocking(True)
            conn.settimeout(10.0)
            chunks, size = [], 0
            while True:
                chunk = conn.recv(1 << 16)
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if b"\n" in chunk:
                    break
                if size > MAX_REQUEST_BYTES:
                    raise ValueError("请求过大")
            req = json.loads(b"".join(chunks).decode("utf-8"))
            if req.get("token") != self.token:
                resp = {"ok": False, "error": "token 不匹配（监听器可能已重启，请重试）"}
            else:
                self.requests += 1
                resp = self._dispatch(req.get("method"), req.get("params") or {})
        except Exception:
            resp = {"ok": False, "error": traceback.format_exc()}
        try:
            conn.settimeout(60.0)
            data = json.dumps(resp, ensure_ascii=False, default=str) + "\n"
            conn.sendall(data.encode("utf-8"))
        except Exception:
            _log("发送响应失败:\n" + traceback.format_exc())
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _dispatch(self, method, params):
        handler = getattr(self, "m_" + str(method), None)
        if handler is None:
            return {"ok": False, "error": "未知方法: %s" % method}
        try:
            return handler(params)
        except Exception:
            return {"ok": False, "error": traceback.format_exc()}

    # ---------------------------------------------------------------- 工具
    def _ui(self):
        try:
            return NXOpen.UI.GetUI()
        except Exception:
            return None

    def _uf(self):
        try:
            import NXOpen.UF
            return NXOpen.UF.UFSession.GetUFSession()
        except Exception:
            return None

    def _part_info(self, part):
        if part is None:
            return None
        info = {}
        for key in ("Leaf", "FullPath"):
            try:
                info[key.lower()] = getattr(part, key)
            except Exception:
                pass
        try:
            units = part.PartUnits  # NXOpen.BasePart.Units 枚举（不是 Part.Units）
            for name in ("Millimeters", "Inches", "Meters", "Micrometers", "Mix"):
                if hasattr(NXOpen.BasePart.Units, name) and units == getattr(NXOpen.BasePart.Units, name):
                    info["units"] = name
                    break
        except Exception:
            pass
        try:
            info["modified"] = bool(part.IsModified)
        except Exception:
            pass
        return info

    # ---------------------------------------------------------------- 方法
    def m_ping(self, p):
        return {"ok": True, "pid": os.getpid(), "port": self.port,
                "nx_base_dir": os.environ.get("UGII_BASE_DIR", ""),
                "python": sys.version.split()[0],
                "uptime_s": round(time.time() - self.started, 1),
                "requests": self.requests}

    def m_info(self, p):
        s = self.session
        work, disp = s.Parts.Work, s.Parts.Display
        result = self.m_ping(p)
        result["display_part"] = self._part_info(disp)
        result["work_part"] = self._part_info(work)
        open_parts = []
        try:
            for part in s.Parts:
                open_parts.append(getattr(part, "Leaf", repr(part)))
        except Exception:
            pass
        result["open_parts"] = open_parts

        max_features = int(p.get("max_features", 300))
        features, bodies = [], []
        if work is not None:
            try:
                feats = work.Features.GetFeatures()
                for f in feats[:max_features]:
                    item = {"id": f.JournalIdentifier}
                    for key, attr in (("name", "GetFeatureName"),):
                        try:
                            item[key] = getattr(f, attr)()
                        except Exception:
                            pass
                    for key, attr in (("type", "FeatureType"), ("suppressed", "Suppressed")):
                        try:
                            item[key] = getattr(f, attr)
                        except Exception:
                            pass
                    features.append(item)
                if len(feats) > max_features:
                    features.append({"note": "共 %d 个特征，只列出前 %d 个" % (len(feats), max_features)})
            except Exception:
                features.append({"error": traceback.format_exc(limit=2)})
            try:
                for b in work.Bodies:
                    item = {"id": b.JournalIdentifier}
                    try:
                        item["solid"] = bool(b.IsSolidBody)
                        item["faces"] = len(b.GetFaces())
                    except Exception:
                        pass
                    bodies.append(item)
            except Exception:
                bodies.append({"error": traceback.format_exc(limit=2)})
        result["features"] = features
        result["bodies"] = bodies
        result["claude_undo_steps"] = len(self.marks)
        return result

    def m_exec(self, p):
        code = p.get("code") or ""
        label = p.get("label") or "Claude"
        use_undo = p.get("undo", True)
        rollback = p.get("rollback_on_error", True)
        s = self.session
        ns = self.namespace
        if p.get("reset_namespace"):
            ns.clear()
        work, disp = s.Parts.Work, s.Parts.Display
        ui, uf = self._ui(), self._uf()
        ns.update({
            "__name__": "__main__",
            "NXOpen": NXOpen,
            # 与 NX 录制的 Journal 一致的变量名，录制代码可直接粘贴运行
            "theSession": s, "workPart": work, "displayPart": disp,
            "theUI": ui, "theUfSession": uf,
            # 简短别名
            "session": s, "work_part": work, "display_part": disp, "ui": ui, "uf": uf,
        })
        ns.pop("result", None)

        self.exec_counter += 1
        filename = "<claude-%d>" % self.exec_counter
        linecache.cache[filename] = (len(code), None, code.splitlines(True), filename)

        # 没有打开的部件时不设撤销标记：之后新建的部件里这个标记是无效的
        mark = None
        if use_undo and work is not None:
            mark = s.SetUndoMark(NXOpen.Session.MarkVisibility.Visible, label)

        out = io.StringIO()
        value, error, rolled_back = None, None, False
        t0 = time.time()
        try:
            tree = ast.parse(code, filename, "exec")
            last_expr = None
            if tree.body and isinstance(tree.body[-1], ast.Expr):
                last_expr = tree.body.pop()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                exec(compile(tree, filename, "exec"), ns)
                if last_expr is not None:
                    value = eval(compile(ast.Expression(last_expr.value), filename, "eval"), ns)
            if value is None and "result" in ns:
                value = ns["result"]
        except BaseException as e:  # 包括 SystemExit（录制的 journal 里可能有 sys.exit）
            if isinstance(e, KeyboardInterrupt):
                raise
            error = traceback.format_exc()

        if mark is not None:
            if error is not None and rollback:
                try:
                    s.UndoToMark(mark, None)
                    s.DeleteUndoMark(mark, None)
                    rolled_back = True
                except Exception:
                    error += "\n（回滚失败）\n" + traceback.format_exc()
            else:
                try:
                    s.UpdateManager.DoUpdate(mark)
                except Exception:
                    pass
                self.marks.append(mark)

        return {
            "ok": error is None,
            "stdout": _truncate(out.getvalue()),
            "value": _to_jsonable(value),
            "error": _truncate(error),
            "rolled_back": rolled_back,
            "elapsed_s": round(time.time() - t0, 3),
            "work_part": self._part_info(s.Parts.Work),
        }

    def m_undo(self, p):
        steps = int(p.get("steps", 1))
        done, skipped, errors = 0, 0, []
        while done < steps and self.marks:
            mark = self.marks.pop()
            try:
                self.session.UndoToMark(mark, None)
            except NXOpen.NXException:
                # 标记已失效（用户在 NX 里撤销过、切换/关闭了部件等），跳过它继续撤销更早的一步
                skipped += 1
                continue
            except Exception:
                errors.append(traceback.format_exc(limit=3))
                break
            try:
                self.session.DeleteUndoMark(mark, None)
            except Exception:
                pass
            done += 1
        return {"ok": not errors, "undone": done, "skipped_invalid": skipped,
                "remaining": len(self.marks), "error": "\n".join(errors) or None}

    def m_screenshot(self, p):
        s = self.session
        part = s.Parts.Display
        if part is None:
            return {"ok": False, "error": "当前没有打开的部件，无法截图"}
        view_name = (p.get("view") or "").lower()
        fit = p.get("fit", True)
        work_view = part.ModelingViews.WorkView
        if view_name and view_name != "current":
            canned = getattr(NXOpen.View.Canned, _CANNED_VIEWS[view_name])
            scale = NXOpen.View.ScaleAdjustment.Fit if fit else NXOpen.View.ScaleAdjustment.Keep
            work_view.Orient(canned, scale)
        elif fit:
            work_view.Fit()

        path = os.path.join(INFO_DIR, "shot_%d.png" % os.getpid())
        if os.path.exists(path):
            os.remove(path)
        white = bool(p.get("white_background", False))
        errors = []
        try:
            self._export_png_builder(path, white)
        except Exception:
            errors.append(traceback.format_exc(limit=3))
            try:
                self._export_png_uf(path, white)
            except Exception:
                errors.append(traceback.format_exc(limit=3))
        if not os.path.exists(path):
            return {"ok": False, "error": "截图失败:\n" + "\n".join(errors)}
        with open(path, "rb") as f:
            data = base64.b64encode(f.read()).decode("ascii")
        return {"ok": True, "png_base64": data, "path": path}

    def _export_png_builder(self, path, white):
        # ImageExportBuilder 从 NX 12.0.1 开始才有；更早的版本走 _export_png_uf
        import NXOpen.Gateway
        try:
            builder = NXOpen.UI.GetUI().CreateImageExportBuilder()
        except Exception:
            builder = self.session.Parts.Display.Views.CreateImageExportBuilder()
        try:
            builder.RegionMode = False
            builder.FileFormat = NXOpen.Gateway.ImageExportBuilder.FileFormats.Png
            builder.FileName = path
            opts = NXOpen.Gateway.ImageExportBuilder.BackgroundOptions
            if white:
                builder.BackgroundOption = opts.CustomColor
                builder.SetCustomBackgroundColor([1.0, 1.0, 1.0])
            else:
                builder.BackgroundOption = opts.Original
            builder.EnhanceEdges = False
            builder.Commit()
        finally:
            builder.Destroy()

    def _export_png_uf(self, path, white):
        import NXOpen.UF
        uf = NXOpen.UF.UFSession.GetUFSession()
        fmt_enum = NXOpen.UF.DispImageFormat
        bg_enum = NXOpen.UF.DispBackgroundColor
        fmt = getattr(fmt_enum, "Png", None) or getattr(fmt_enum, "PNG")
        bg_name = "White" if white else "Original"
        bg = getattr(bg_enum, bg_name, None) or getattr(bg_enum, bg_name.upper())
        uf.Disp.CreateImage(path, fmt, bg)

    def m_stop(self, p):
        self.stop()
        if getattr(sys, STATE_ATTR, None) is self:
            delattr(sys, STATE_ATTR)
        return {"ok": True}


def _notify(msg, listing):
    _log(msg)
    try:
        NXOpen.Session.GetSession().LogFile.WriteLine("[NX-MCP] " + msg)
    except Exception:
        pass
    try:
        import NXOpen.UF
        NXOpen.UF.UFSession.GetUFSession().Ui.SetStatus("NX-MCP: " + msg)
    except Exception:
        pass
    if listing:
        try:
            lw = NXOpen.Session.GetSession().ListingWindow
            lw.Open()
            lw.WriteLine("[NX-MCP] " + msg)
        except Exception:
            pass


def start(force_restart=False, listing=False):
    """启动监听器。已在运行时：force_restart=True 则重启（加载新代码），否则保持不变。"""
    old = getattr(sys, STATE_ATTR, None)
    if old is not None:
        if not force_restart:
            return old
        try:
            old.stop()
        except Exception:
            _log("停止旧监听器失败:\n" + traceback.format_exc())
        delattr(sys, STATE_ATTR)
    bridge = Bridge()
    bridge.start()
    # 挂在 sys 上：即使 NX 卸载了这个脚本模块，回调对象也不会被回收
    setattr(sys, STATE_ATTR, bridge)
    _notify("监听已启动，端口 %d（Claude 现在可以控制这个 NX 窗口）" % bridge.port, listing)
    return bridge


def stop():
    old = getattr(sys, STATE_ATTR, None)
    if old is not None:
        old.stop()
        delattr(sys, STATE_ATTR)


def GetUnloadOption(*args):
    return int(NXOpen.Session.LibraryUnloadOption.AtTermination)


def main(*args):
    try:
        start(force_restart=True, listing=True)
    except Exception:
        msg = traceback.format_exc()
        _log("启动失败:\n" + msg)
        try:
            lw = NXOpen.Session.GetSession().ListingWindow
            lw.Open()
            lw.WriteLine("[NX-MCP] 启动失败:\n" + msg)
        except Exception:
            pass


if __name__ == "__main__":
    main()
