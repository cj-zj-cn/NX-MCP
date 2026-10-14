# -*- coding: utf-8 -*-
"""
NX-MCP 服务器：让 Claude 通过 MCP 控制正在运行的 Siemens NX / Designcenter 图形界面。
只用 Python 标准库（3.8+），不需要 pip 安装任何东西。

架构：
  Claude ⇄ (stdio, MCP) ⇄ 本文件 ⇄ (127.0.0.1 TCP + token) ⇄ nx_bridge_listener.py（在 NX 里运行）⇄ NX Open

注册到 Claude Code：
  claude mcp add -s user nx -- python <本文件完整路径>

命令行自检：
  python nx_mcp_server.py --check                 查看已连接的 NX
  python nx_mcp_server.py --lookup CylinderBuilder [--nx 12]
  python nx_mcp_server.py --build-index [--nx 2606]
"""

import ctypes
import glob
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
import xml.etree.ElementTree as ET

SERVER_NAME = "nx-mcp"
SERVER_VERSION = "0.1.0"
HERE = os.path.dirname(os.path.abspath(__file__))
LISTENER_PATH = os.path.join(HERE, "nx_bridge_listener.py")
INFO_DIR = os.path.join(tempfile.gettempdir(), "nx_mcp_bridge")
CACHE_DIR = os.path.join(HERE, "cache")
INDEX_VERSION = "1"

NOT_CONNECTED = (
    "没有连接到 NX。请让用户在 NX 里按 Alt+F8（工具 → 操作记录 → 播放），"
    "选择 %s 运行，然后再试。" % LISTENER_PATH
)

INSTRUCTIONS = """\
NX-MCP 用来控制用户电脑上正在运行的 Siemens NX / Designcenter 图形界面（实时，在用户看得到的窗口里建模）。

推荐流程：
1. 先调用 nx_status，确认已连接，查看当前部件、特征和实体。
2. 用 nx_run_python 执行 NX Open Python 代码。预置变量与 NX 录制的 Journal 一致：
   theSession、workPart、displayPart、theUI、theUfSession（别名 session、work_part、display_part、ui、uf）。
   NXOpen 已导入；NXOpen.Features、NXOpen.GeometricUtilities 等子模块需自行 import。
   命名空间在多次调用之间保留，可以先定义辅助函数再复用。最后一行是表达式时返回其值，也可以给 result 赋值。
3. 每次修改后用 nx_screenshot 看效果，用 nx_status 核对特征和实体。
4. 不确定类名、方法签名、枚举值时，用 nx_api_lookup 查本机 NX 自带的 API 文档；
   也可以在 nx_run_python 里用 dir() / help() 查实时 API（以实际运行的 NX 版本为准）。

要点：
- 没有打开部件时 workPart 为 None，需要先新建：
  theSession.Parts.NewDisplay(r"C:\\路径\\名称.prt", NXOpen.Part.Units.Millimeters)（文件不能已存在）。
- Builder 模式：b = workPart.Features.CreateXxxBuilder(NXOpen.Features.Feature.Null) → 设置参数 →
  b.CommitFeature() 或 b.Commit() → 在 finally 里 b.Destroy()。尺寸表达式传字符串，如 "100"。
- NX Open 的 Python 接口不会把 int 自动转成 float：NXOpen.Point3d / Vector3d 等的坐标必须写成 0.0、
  float(x)，写 0 会报"期望的是 double 类型"。
- 每次 nx_run_python 自动设一个可见撤销标记（用户可在 NX 里 Ctrl+Z 撤销），出错时默认回滚到调用前状态
  （调用前没有打开任何部件时不设标记，也就无法回滚）。
  纯查询的代码请传 undo=false，避免在 NX 撤销列表里留下空步骤。nx_undo 可撤销 Claude 之前的操作。
- 不要调用会弹出模态对话框、等待用户点击的 API（例如 NXMessageBox.Show），否则会卡住直到用户关掉对话框。
- 除非用户要求，不要保存、关闭或覆盖用户的部件文件。
- API 文档是 .NET 版，Python 中类名、方法名、属性名一致；属性直接用 obj.Name 读写；
  .NET 的 out 参数（文档里带 @ 的参数）在 Python 中作为返回值（多个时为元组）。
- NX 12 用 Python 3.6，新版用 Python 3.12；新版才有的 API 在 NX 12 中不存在。
- NX 12 不支持实时控制（监听器会拒绝启动，否则 NX 会卡死）。对 NX 12 用 nx_batch_run 后台生成/修改文件。
"""


# =============================================================================
# 与 NX 内监听器通信
# =============================================================================

class BridgeError(Exception):
    pass


def _pid_alive(pid):
    if os.name != "nt":
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _load_sessions(target=None):
    sessions = []
    for path in glob.glob(os.path.join(INFO_DIR, "*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                info = json.load(f)
        except Exception:
            continue
        if not _pid_alive(info.get("pid", 0)):
            try:
                os.remove(path)
            except Exception:
                pass
            continue
        info["_file"] = path
        sessions.append(info)
    sessions.sort(key=lambda s: s.get("started", 0), reverse=True)
    if target:
        t = str(target).strip().lower()
        sessions = [s for s in sessions
                    if t == str(s.get("pid")) or _version_match(t, os.path.basename(s.get("nx_base_dir", "")))]
    return sessions


def _raw_call(info, method, params, timeout):
    with socket.create_connection(("127.0.0.1", int(info["port"])), timeout=3) as conn:
        conn.settimeout(timeout)
        payload = {"token": info["token"], "method": method, "params": params}
        conn.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        chunks = []
        while True:
            chunk = conn.recv(1 << 16)
            if not chunk:
                break
            chunks.append(chunk)
            if b"\n" in chunk:
                break
    data = b"".join(chunks)
    if not data:
        raise BridgeError("NX 没有返回数据（监听器可能已停止）")
    return json.loads(data.decode("utf-8"))


def bridge_call(method, params=None, target=None, timeout=120):
    sessions = _load_sessions(target)
    if not sessions:
        if target:
            raise BridgeError("没有找到匹配 target=%r 的 NX 会话。\n%s" % (target, NOT_CONNECTED))
        raise BridgeError(NOT_CONNECTED)
    last_err = None
    for info in sessions:
        try:
            return info, _raw_call(info, method, params or {}, timeout)
        except socket.timeout:
            raise BridgeError(
                "NX 在 %d 秒内没有响应。可能正在执行耗时操作，或者 NX 里开着对话框/命令。"
                "请让用户关闭 NX 中打开的对话框后重试。" % timeout)
        except (ConnectionRefusedError, ConnectionResetError, ConnectionAbortedError) as e:
            last_err = e
            continue
    raise BridgeError("%s\n（最后一次连接错误：%s）" % (NOT_CONNECTED, last_err))


def _nx_label(info):
    return "%s (pid %s, 端口 %s, Python %s)" % (
        os.path.basename(info.get("nx_base_dir", "")) or "NX", info.get("pid"), info.get("port"), info.get("python"))


# =============================================================================
# NX Open API 文档查询（解析本机 NX 自带的 NXOpen*.xml）
# =============================================================================

DOC_FILES = ("NXOpen.xml", "NXOpenUI.xml", "NXOpen.UF.xml", "NXOpen.Utilities.xml")
KIND_LABEL = {"T": "类型", "M": "方法", "P": "属性", "F": "枚举值/字段", "E": "事件"}
SIG_REPLACE = (
    ("System.String", "str"), ("System.Int32", "int"), ("System.Int64", "long"),
    ("System.Double", "float"), ("System.Boolean", "bool"), ("System.Object", "object"),
    ("System.IntPtr", "IntPtr"), ("System.UInt32", "uint"),
)


def _version_numbers(name):
    return re.findall(r"\d+(?:\.\d+)?", name or "")


def _version_match(query, name):
    q = str(query).strip().lower()
    if not q:
        return True
    if q == (name or "").lower():
        return True
    return any(n == q or n.startswith(q + ".") for n in _version_numbers(name))


def find_nx_installs():
    candidates = []
    if os.environ.get("UGII_BASE_DIR"):
        candidates.append(os.environ["UGII_BASE_DIR"])
    for extra in os.environ.get("NX_MCP_NX_DIRS", "").split(";"):
        if extra.strip():
            candidates.append(extra.strip())
    roots = []
    for env in ("ProgramW6432", "ProgramFiles"):
        if os.environ.get(env):
            roots.append(os.environ[env])
    roots.append(r"C:\Program Files")
    for root in roots:
        candidates.extend(sorted(glob.glob(os.path.join(root, "Siemens", "*"))))

    found = {}
    for base in candidates:
        for sub in (os.path.join("NXBIN", "managed"), os.path.join("UGII", "managed")):
            docs = os.path.join(base, sub)
            if os.path.isfile(os.path.join(docs, "NXOpen.xml")):
                key = os.path.normcase(os.path.abspath(base))
                if key not in found:
                    found[key] = {"name": os.path.basename(base.rstrip("\\/")), "base": base, "docs": docs}
                break
    return list(found.values())


def pick_install(nx_version=None):
    installs = find_nx_installs()
    if not installs:
        raise BridgeError("没有找到 NX 安装目录里的 NXOpen.xml 文档。可设置环境变量 NX_MCP_NX_DIRS 指定 NX 安装目录。")
    if not nx_version:
        return installs[0]
    for inst in installs:
        if _version_match(nx_version, inst["name"]):
            return inst
    raise BridgeError("没有找到版本 %r。已安装：%s" % (nx_version, ", ".join(i["name"] for i in installs)))


def _flatten(elem):
    out = []
    if elem.text:
        out.append(elem.text)
    for child in elem:
        tag = child.tag
        if tag in ("see", "seealso"):
            label = "".join(child.itertext()).strip()
            cref = re.sub(r"^[A-Z]:", "", child.get("cref") or child.get("langword") or "").split("(")[0]
            out.append(" %s " % (label or cref))
        elif tag in ("paramref", "typeparamref"):
            out.append(child.get("name", ""))
        elif tag == "para":
            out.append("\n" + _flatten(child) + "\n")
        else:
            out.append(_flatten(child))
        if child.tail:
            out.append(child.tail)
    return "".join(out)


def _clean(text):
    lines = []
    for line in (text or "").splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if not line or line.startswith("License requirements"):
            continue
        lines.append(line)
    return "\n".join(lines)


def _short_sig(sig):
    for a, b in SIG_REPLACE:
        sig = sig.replace(a, b)
    return sig.replace("@", " (out)")


def _fingerprint(inst):
    parts = [INDEX_VERSION]
    for name in DOC_FILES:
        path = os.path.join(inst["docs"], name)
        if os.path.isfile(path):
            st = os.stat(path)
            parts.append("%s:%d:%d" % (name, st.st_size, int(st.st_mtime)))
    return "|".join(parts)


def _index_path(inst):
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", inst["name"])
    return os.path.join(CACHE_DIR, "nxopen_api_%s.sqlite" % safe)


def build_index(inst, log=None):
    if not os.path.isdir(CACHE_DIR):
        os.makedirs(CACHE_DIR)
    path = _index_path(inst)
    tmp = path + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    t0 = time.time()
    db = sqlite3.connect(tmp)
    db.executescript("""
        CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
        CREATE TABLE api (kind TEXT, full TEXT, parent TEXT, short TEXT, sig TEXT,
                          summary TEXT, remarks TEXT, params TEXT, returns TEXT);
    """)
    total = 0
    for name in DOC_FILES:
        xml_path = os.path.join(inst["docs"], name)
        if not os.path.isfile(xml_path):
            continue
        rows = []
        for _event, elem in ET.iterparse(xml_path, events=("end",)):
            if elem.tag != "member":
                continue
            member = elem.get("name") or ""
            if len(member) > 2 and member[1] == ":":
                kind, rest = member[0], member[2:]
                if "(" in rest:
                    full, sig = rest.split("(", 1)
                    sig = "(" + sig
                else:
                    full, sig = rest, ""
                parent, _, short = full.rpartition(".")
                summary = _clean(_flatten(elem.find("summary"))) if elem.find("summary") is not None else ""
                remarks = _clean(_flatten(elem.find("remarks"))) if elem.find("remarks") is not None else ""
                params = [[p.get("name", ""), _clean(_flatten(p))] for p in elem.findall("param")]
                ret = elem.find("returns")
                returns = _clean(_flatten(ret)) if ret is not None else ""
                rows.append((kind, full, parent, short, sig, summary, remarks,
                             json.dumps(params, ensure_ascii=False) if params else "", returns))
            elem.clear()
        db.executemany("INSERT INTO api VALUES (?,?,?,?,?,?,?,?,?)", rows)
        total += len(rows)
        if log:
            log("  %s: %d 条" % (name, len(rows)))
    db.executescript("""
        CREATE INDEX idx_full ON api(full COLLATE NOCASE);
        CREATE INDEX idx_parent ON api(parent);
        CREATE INDEX idx_short ON api(short COLLATE NOCASE);
    """)
    db.execute("INSERT INTO meta VALUES ('fingerprint', ?)", (_fingerprint(inst),))
    db.commit()
    db.close()
    if os.path.exists(path):
        os.remove(path)
    os.rename(tmp, path)
    if log:
        log("索引完成：%s，共 %d 条，用时 %.1f 秒" % (inst["name"], total, time.time() - t0))
    return path


def open_index(inst, log=None):
    path = _index_path(inst)
    if os.path.exists(path):
        try:
            db = sqlite3.connect(path)
            row = db.execute("SELECT v FROM meta WHERE k='fingerprint'").fetchone()
            if row and row[0] == _fingerprint(inst):
                return db
            db.close()
        except sqlite3.Error:
            pass
    build_index(inst, log=log)
    return sqlite3.connect(path)


_COLS = "kind, full, parent, short, sig, summary, remarks, params, returns"


def _row(r):
    return dict(zip(("kind", "full", "parent", "short", "sig", "summary", "remarks", "params", "returns"), r))


def _first_line(text, limit=110):
    line = " ".join((text or "").split("\n"))
    return line if len(line) <= limit else line[:limit - 1] + "…"


def _render_type(db, t, limit):
    lines = ["## %s %s" % (KIND_LABEL.get(t["kind"], t["kind"]), t["full"])]
    if t["summary"]:
        lines.append(t["summary"])
    if t["remarks"]:
        lines.append(t["remarks"])
    children = [_row(r) for r in db.execute(
        "SELECT %s FROM api WHERE parent = ? ORDER BY kind, short" % _COLS, (t["full"],))]
    groups = (("P", "属性（Python 里直接 obj.名称 读写）"), ("M", "方法"), ("F", "枚举值 / 字段"), ("T", "嵌套类型"), ("E", "事件"))
    shown = 0
    for kind, title in groups:
        items = [c for c in children if c["kind"] == kind]
        if not items:
            continue
        lines.append("\n### %s（%d）" % (title, len(items)))
        for c in items[:limit]:  # limit 按每组计算，避免属性太多把方法挤掉
            sig = _short_sig(c["sig"]) if kind == "M" else ""
            lines.append("- %s%s — %s" % (c["short"], sig, _first_line(c["summary"])))
            shown += 1
    if len(children) > shown:
        lines.append("\n（还有 %d 个成员未列出，可把 limit 调大，或查询 类名.成员名 看详情）" % (len(children) - shown))
    if t["kind"] == "T":
        lines.append("\n注：这里只列出本类自己声明的成员，继承的成员请查父类，或在 NX 里用 dir() 查看。")
    return "\n".join(lines)


def _render_member(m):
    lines = ["## %s %s%s" % (KIND_LABEL.get(m["kind"], m["kind"]), m["full"], _short_sig(m["sig"]))]
    if m["summary"]:
        lines.append(m["summary"])
    if m["remarks"]:
        lines.append(m["remarks"])
    if m["params"]:
        lines.append("参数：")
        for name, desc in json.loads(m["params"]):
            lines.append("- %s: %s" % (name, desc or ""))
    if m["returns"]:
        lines.append("返回：%s" % m["returns"])
    return "\n".join(lines)


def _render_list(rows, title):
    lines = [title]
    for r in rows:
        sig = _short_sig(r["sig"]) if r["kind"] == "M" else ""
        lines.append("- [%s] %s%s — %s" % (KIND_LABEL.get(r["kind"], r["kind"]), r["full"], sig, _first_line(r["summary"], 90)))
    return "\n".join(lines)


def _escape_like(s):
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def api_lookup(query, nx_version=None, limit=40, search_docs=False, log=None):
    inst = pick_install(nx_version)
    db = open_index(inst, log=log)
    try:
        q = (query or "").strip().strip("`").replace("::", ".")
        q = re.sub(r"\(.*$", "", q)  # 去掉参数列表
        if not q:
            return "请提供要查询的类名、方法名或关键词。"
        header = "（%s 的 NX Open 文档）\n\n" % inst["name"]
        order = "ORDER BY CASE kind WHEN 'T' THEN 0 WHEN 'M' THEN 1 WHEN 'P' THEN 2 ELSE 3 END, length(full)"

        # 1. 完整名称精确匹配 / 2. 后缀匹配（如 BlockFeatureBuilder、CylinderBuilder.Diameter）
        rows = [_row(r) for r in db.execute(
            "SELECT %s FROM api WHERE full = ? COLLATE NOCASE OR full = ? COLLATE NOCASE" % _COLS, (q, "NXOpen." + q))]
        if not rows:
            rows = [_row(r) for r in db.execute(
                "SELECT %s FROM api WHERE full LIKE ? ESCAPE '\\' %s LIMIT 200" % (_COLS, order),
                ("%." + _escape_like(q),))]
        if rows:
            fulls = sorted(set(r["full"] for r in rows))
            if len(fulls) == 1:
                types = [r for r in rows if r["kind"] == "T"]
                if types:
                    return header + _render_type(db, types[0], limit)
                return header + "\n\n".join(_render_member(r) for r in rows[:10])
            return header + _render_list(rows[:limit], "找到 %d 个同名条目，请用完整名称再查：" % len(fulls))

        # 3. 关键词（全部包含在完整名称中）
        tokens = [t for t in re.split(r"[\s,]+", q) if t]
        where = " AND ".join(["full LIKE ? ESCAPE '\\'"] * len(tokens))
        args = ["%" + _escape_like(t) + "%" for t in tokens]
        rows = [_row(r) for r in db.execute(
            "SELECT %s FROM api WHERE %s %s LIMIT ?" % (_COLS, where, order), args + [limit])]
        parts = []
        if rows:
            parts.append(_render_list(rows, "名称包含 %s 的条目（前 %d 个）：" % (" + ".join(tokens), len(rows))))

        # 4. 在说明文字里搜索
        if search_docs or not rows:
            where = " AND ".join(["summary LIKE ? ESCAPE '\\'"] * len(tokens))
            doc_rows = [_row(r) for r in db.execute(
                "SELECT %s FROM api WHERE %s %s LIMIT ?" % (_COLS, where, order), args + [limit])]
            if doc_rows:
                parts.append(_render_list(doc_rows, "说明文字包含 %s 的条目（前 %d 个）：" % (" + ".join(tokens), len(doc_rows))))
        if not parts:
            return header + "没有找到与 %r 相关的条目。可以换英文关键词（文档是英文的），或用 search_docs=true 搜索说明文字。" % query
        return header + "\n\n".join(parts)
    finally:
        db.close()


# =============================================================================
# MCP 工具
# =============================================================================

TARGET_PROP = {
    "type": "string",
    "description": "同时开了多个 NX 时用来选择：版本号（如 \"2606\"、\"12\"）或进程 pid。不填则用最近启动监听的那个。",
}

TOOLS = [
    {
        "name": "nx_status",
        "description": "查看已连接的 NX 会话：NX 版本、当前显示/工作部件、所有特征（名称、类型、是否抑制）、实体及面数。建模前后都可以用它核对。",
        "inputSchema": {"type": "object", "properties": {"target": TARGET_PROP}},
    },
    {
        "name": "nx_run_python",
        "description": (
            "在用户正在使用的 NX 图形界面里执行 NX Open Python 代码（NX 主线程，结果立刻显示在 NX 窗口中）。"
            "预置变量：theSession、workPart、displayPart、theUI、theUfSession（别名 session、work_part、display_part、ui、uf），NXOpen 已导入。"
            "print 的内容会返回；最后一行如果是表达式会返回它的值，也可以给 result 赋值。"
            "每次调用自动设置撤销标记，出错默认回滚。命名空间在多次调用间保留。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "要执行的 NX Open Python 代码"},
                "label": {"type": "string", "description": "NX 撤销列表里显示的步骤名，默认 Claude"},
                "undo": {"type": "boolean", "description": "是否设置撤销标记，默认 true。纯查询代码请传 false"},
                "rollback_on_error": {"type": "boolean", "description": "出错时是否回滚到执行前状态，默认 true"},
                "reset_namespace": {"type": "boolean", "description": "清空之前调用保留的变量，默认 false"},
                "timeout_s": {"type": "number", "description": "等待 NX 执行完成的秒数，默认 300"},
                "target": TARGET_PROP,
            },
            "required": ["code"],
        },
    },
    {
        "name": "nx_screenshot",
        "description": "截取 NX 图形窗口的当前画面（PNG），用来查看建模结果。可以先切换到标准视图并缩放到合适大小。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "view": {"type": "string",
                         "enum": ["current", "trimetric", "isometric", "top", "bottom", "front", "back", "left", "right"],
                         "description": "截图前切换到的视图方向，默认 current（不改变方向）"},
                "fit": {"type": "boolean", "description": "截图前是否缩放到适合窗口，默认 true"},
                "white_background": {"type": "boolean", "description": "使用白色背景，默认 false（保持 NX 背景）"},
                "target": TARGET_PROP,
            },
        },
    },
    {
        "name": "nx_undo",
        "description": "撤销 Claude 通过 nx_run_python 做的最近几步操作（回到对应调用之前的状态）。注意：用户在这之后手动做的操作也会一起被撤销。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "steps": {"type": "integer", "description": "撤销几步，默认 1", "minimum": 1},
                "target": TARGET_PROP,
            },
        },
    },
    {
        "name": "nx_batch_run",
        "description": (
            "后台批处理：用 run_journal.exe 在一个独立的、不显示界面的 NX 进程里运行一段完整的 NX Open Python 脚本，"
            "运行完进程就退出（约 15-30 秒）。适用于：不支持实时控制的 NX 12、批量生成或转换文件、用户没开 NX 的时候。"
            "脚本没有预置变量，要自己 theSession = NXOpen.Session.GetSession()，自己新建/打开部件并保存；"
            "args 会作为 sys.argv[1:] 传给脚本。改的是文件，不是用户正开着的 NX 窗口。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "完整的 NX Open Python 脚本"},
                "nx_version": {"type": "string", "description": "用哪个 NX 版本运行，如 \"12\"、\"2606\"。默认为 UGII_BASE_DIR 指向的版本"},
                "args": {"type": "array", "items": {"type": "string"}, "description": "传给脚本的参数（sys.argv[1:]）"},
                "timeout_s": {"type": "number", "description": "超时秒数，默认 600"},
            },
            "required": ["code"],
        },
    },
    {
        "name": "nx_api_lookup",
        "description": (
            "查询本机 NX 自带的 NX Open API 文档（类、方法签名、参数说明、属性、枚举值）。写代码前不确定名称时使用。"
            "可以查类名（BlockFeatureBuilder，返回成员列表）、类名.成员名（CylinderBuilder.Axis，返回详情），"
            "或英文关键词（\"Chamfer Builder\"）。文档是 .NET 版，Python 中名称一致。首次查询某个版本时需要建索引，约半分钟。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "类名、类名.成员名，或空格分隔的英文关键词"},
                "nx_version": {"type": "string", "description": "查哪个 NX 版本的文档，如 \"2606\"、\"12\"。默认为 UGII_BASE_DIR 指向的版本"},
                "limit": {"type": "integer", "description": "最多列出多少条，默认 40"},
                "search_docs": {"type": "boolean", "description": "同时在说明文字里搜索关键词，默认 false"},
            },
            "required": ["query"],
        },
    },
]


def _text(s):
    return {"type": "text", "text": s}


def _tool_result(content, is_error=False):
    if isinstance(content, str):
        content = [_text(content)]
    return {"content": content, "isError": bool(is_error)}


def _fmt_json(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


def tool_nx_status(args):
    sessions = _load_sessions(args.get("target"))
    if not sessions:
        return _tool_result(NOT_CONNECTED, True)
    blocks = []
    for info in sessions:
        try:
            resp = _raw_call(info, "info", {}, timeout=15)
            blocks.append("# %s\n%s" % (_nx_label(info), _fmt_json(resp)))
        except socket.timeout:
            blocks.append("# %s\nNX 15 秒内没有响应（可能正忙或开着对话框）" % _nx_label(info))
        except Exception as e:
            blocks.append("# %s\n无法连接：%s" % (_nx_label(info), e))
    return _tool_result("\n\n".join(blocks))


def tool_nx_run_python(args):
    code = args.get("code") or ""
    if not code.strip():
        return _tool_result("code 不能为空", True)
    params = {
        "code": code,
        "label": args.get("label") or "Claude",
        "undo": args.get("undo", True),
        "rollback_on_error": args.get("rollback_on_error", True),
        "reset_namespace": args.get("reset_namespace", False),
    }
    timeout = float(args.get("timeout_s") or 300)
    info, resp = bridge_call("exec", params, args.get("target"), timeout=timeout)
    ok = resp.get("ok")
    lines = ["NX: %s" % _nx_label(info)]
    if ok:
        lines.append("状态: 成功（%.2f 秒）" % resp.get("elapsed_s", 0))
    else:
        lines.append("状态: 出错%s" % ("，已回滚到执行前状态" if resp.get("rolled_back") else ""))
    if resp.get("stdout"):
        lines.append("--- 输出 ---\n" + resp["stdout"].rstrip())
    if resp.get("value") is not None:
        value = resp["value"]
        lines.append("--- 返回值 ---\n" + (value if isinstance(value, str) else _fmt_json(value)))
    if resp.get("error"):
        lines.append("--- 错误 ---\n" + resp["error"].rstrip())
    wp = resp.get("work_part")
    if wp:
        lines.append("工作部件: %s" % (wp.get("fullpath") or wp.get("leaf")))
    elif ok:
        lines.append("工作部件: 无（还没有打开或新建部件）")
    return _tool_result("\n".join(lines), not ok)


def tool_nx_screenshot(args):
    params = {
        "view": args.get("view") or "current",
        "fit": args.get("fit", True),
        "white_background": args.get("white_background", False),
    }
    info, resp = bridge_call("screenshot", params, args.get("target"), timeout=60)
    if not resp.get("ok"):
        return _tool_result("截图失败：%s" % resp.get("error"), True)
    return _tool_result([
        {"type": "image", "data": resp["png_base64"], "mimeType": "image/png"},
        _text("NX 截图：%s（文件 %s）" % (_nx_label(info), resp.get("path"))),
    ])


def tool_nx_undo(args):
    steps = int(args.get("steps") or 1)
    info, resp = bridge_call("undo", {"steps": steps}, args.get("target"), timeout=120)
    text = "已撤销 %s 步，还可撤销 %s 步。" % (resp.get("undone"), resp.get("remaining"))
    if resp.get("skipped_invalid"):
        text += "\n跳过了 %s 个已失效的撤销标记（用户在 NX 里撤销过，或那一步新建/切换了部件）。" % resp["skipped_invalid"]
    if resp.get("error"):
        text += "\n错误：\n" + resp["error"]
    return _tool_result(text, not resp.get("ok"))


def _decode_output(data):
    for enc in ("utf-8", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("utf-8", errors="replace")


def tool_nx_batch_run(args):
    code = args.get("code") or ""
    if not code.strip():
        return _tool_result("code 不能为空", True)
    inst = pick_install(args.get("nx_version"))
    exe = os.path.join(inst["base"], "NXBIN", "run_journal.exe")
    if not os.path.isfile(exe):
        return _tool_result("找不到 %s" % exe, True)

    # 脚本放在项目内的短路径下：NX 12 对过长的路径支持不好
    batch_dir = os.path.join(CACHE_DIR, "batch")
    if not os.path.isdir(batch_dir):
        os.makedirs(batch_dir)
    old = sorted(glob.glob(os.path.join(batch_dir, "batch_*.py")))
    for path in old[:-20]:
        try:
            os.remove(path)
        except OSError:
            pass
    script = os.path.join(batch_dir, "batch_%s.py" % time.strftime("%Y%m%d_%H%M%S"))
    with open(script, "w", encoding="utf-8") as f:
        f.write(code)

    env = dict(os.environ)
    env["UGII_BASE_DIR"] = inst["base"]
    env["UGII_ROOT_DIR"] = os.path.join(inst["base"], "UGII")
    cmd = [exe, script]
    if args.get("args"):
        cmd += ["-args"] + [str(a) for a in args["args"]]
    timeout = float(args.get("timeout_s") or 600)
    t0 = time.time()
    try:
        # stdin/stdout 不能继承：它们是 MCP 协议通道
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              env=env, timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return _tool_result("运行超过 %d 秒，已终止。脚本：%s" % (timeout, script), True)
    output = _decode_output(proc.stdout or b"").strip()
    failed = proc.returncode != 0 or "Traceback (most recent call last)" in output
    text = "NX: %s（批处理，%.1f 秒，退出码 %d）\n脚本：%s\n--- 输出 ---\n%s" % (
        inst["name"], time.time() - t0, proc.returncode, script, output or "（无输出）")
    return _tool_result(text, failed)


def tool_nx_api_lookup(args):
    text = api_lookup(args.get("query", ""), args.get("nx_version"),
                      int(args.get("limit") or 40), bool(args.get("search_docs", False)))
    return _tool_result(text)


TOOL_HANDLERS = {
    "nx_status": tool_nx_status,
    "nx_run_python": tool_nx_run_python,
    "nx_screenshot": tool_nx_screenshot,
    "nx_undo": tool_nx_undo,
    "nx_batch_run": tool_nx_batch_run,
    "nx_api_lookup": tool_nx_api_lookup,
}


def call_tool(name, args):
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return _tool_result("未知工具：%s" % name, True)
    try:
        return handler(args or {})
    except BridgeError as e:
        return _tool_result(str(e), True)
    except Exception:
        return _tool_result("工具执行异常：\n" + traceback.format_exc(), True)


# =============================================================================
# MCP stdio 协议（JSON-RPC 2.0，每行一条消息）
# =============================================================================

def _handle(msg):
    if not isinstance(msg, dict):
        return None
    method = msg.get("method")
    if method is None:
        return None  # 客户端对我们请求的响应（本服务器不发请求）
    is_request = "id" in msg
    mid = msg.get("id")
    params = msg.get("params") or {}
    try:
        if method == "initialize":
            result = {
                "protocolVersion": params.get("protocolVersion") or "2025-06-18",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": INSTRUCTIONS,
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            result = call_tool(params.get("name"), params.get("arguments") or {})
        elif not is_request:
            return None  # notifications/initialized、notifications/cancelled 等
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "Method not found: %s" % method}}
    except Exception:
        if not is_request:
            return None
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": traceback.format_exc()}}
    if not is_request:
        return None
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def serve():
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    sys.stdout = sys.stderr  # 防止任何意外的 print 污染协议通道

    def send(obj):
        stdout.write(json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n")
        stdout.flush()

    for raw in stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line.decode("utf-8"))
        except Exception:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            continue
        for m in (msg if isinstance(msg, list) else [msg]):
            resp = _handle(m)
            if resp is not None:
                send(resp)


# =============================================================================
# 命令行
# =============================================================================

def _cli(argv):
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    nx = None
    if "--nx" in argv:
        i = argv.index("--nx")
        nx = argv[i + 1]
        del argv[i:i + 2]
    if argv[0] == "--check":
        print(tool_nx_status({"target": nx})["content"][0]["text"])
    elif argv[0] == "--lookup":
        print(api_lookup(" ".join(argv[1:]), nx, log=print))
    elif argv[0] == "--build-index":
        installs = [pick_install(nx)] if nx else find_nx_installs()
        for inst in installs:
            print("建立索引：%s（%s）" % (inst["name"], inst["docs"]))
            build_index(inst, log=print)
    else:
        print(__doc__)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        _cli(sys.argv[1:])
    else:
        serve()
