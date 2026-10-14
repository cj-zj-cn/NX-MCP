# AGENTS.md

给在本项目里工作的 AI 编程助手（Claude Code、Codex 等）看的经验笔记。用户文档见 [README.md](README.md)。
这里记的是实际踩过的坑和验证过的做法，改代码或用 NX-MCP 建模之前先读一遍。

## 架构

```
MCP 客户端 ⇄ (stdio) ⇄ nx_mcp_server.py ⇄ (127.0.0.1 TCP + token) ⇄ nx_bridge_listener.py（在 NX 里）⇄ NX Open
```

- `nx_bridge_listener.py` 在 NX 进程内运行：Win32 `SetTimer` 每 50ms 在 NX 主线程检查非阻塞 socket，请求直接在主线程执行
  （NX Open 只能在主线程调用，所以不用后台线程）。
- 启动时把端口和随机 token 写到 `%TEMP%\nx_mcp_bridge\<pid>.json`，`nx_mcp_server.py` 读这个文件找到 NX。
- `nx_batch_run` 用 `run_journal.exe` 起一个无界面 NX 进程跑完整脚本，每次约 20–30 秒。
- 全部只用 Python 标准库；不需要 NX Open 作者许可证。

## NX 版本差异（最重要）

| 版本 | 实时控制 | 原因 / 做法 |
|---|---|---|
| Designcenter 2606（Python 3.12） | ✅ | 脚本必须带 `# nx: threaded`，见下 |
| NX 12.0（Python 3.6） | ❌ | 脚本在子解释器里运行且 NX 空闲时一直持有 GIL，ctypes 定时回调进 Python 就死锁、NX 卡死。监听器用 `PyThreadState_Get() != PyGILState_GetThisThreadState()` 检测并拒绝启动。用 `nx_batch_run` |

### `# nx: threaded`：不能删

- Designcenter 2606 用 Alt+F8 播放 Python 操作记录时，默认放进**一次性子解释器**，播放结束立刻 `Py_EndInterpreter`。
  监听 socket 跟着被关掉。症状：信息窗口显示"监听已启动"，但客户端报 `WinError 10061 拒绝连接`。
- `GetUnloadOption` 返回 `AtTermination` **对此无效**。删掉 `GetUnloadOption` 也没用（都试过）。
- 修复：脚本里放一行注释 `# nx: threaded`。这是 `NXBIN\nxpythonldr.dll` 里的开关
  （日志字符串 "Threaded extension usage flag found in line %d"），有它 NX 就在主解释器里运行脚本，对象常驻。
  `nx_bridge_listener.py` 和 `startup/nx_mcp_autostart.py` 顶部都有这一行。
- 排查方法：监听器启动时日志会记录"当前解释器"和"主解释器"地址，二者不同就说明在子解释器里；
  NX 的 syslog（`%TEMP%\<用户名><hex>.syslog`）里会有 `Unloading ...nx_bridge_listener.py`。
- Ctrl+U（执行 NX Open）不能运行 .py（报"Python 文件不能通过执行用户函数对话框来运行"）。只能 Alt+F8。
- `Session.Execute(...)` 调 Python 需要 NX Open 作者许可证，不能作为替代方案。

## 改监听器时注意

- 回调对象（ctypes `WINFUNCTYPE`）必须一直被引用，否则会被回收导致 NX 崩溃。监听器实例挂在 `sys._nx_mcp_bridge_state` 上。
- 重启监听器后，旧实例的定时器可能还会再触发几次（日志里的 `'NoneType' object has no attribute 'accept'`），
  `_on_timer` 开头要先判断 `self.sock is None`。
- 子解释器被销毁时模块全局变量会变成 None，旧回调里会出现 `'NoneType' object cannot be interpreted as an integer` 这类错误。
- 热重载：让正在运行的监听器执行 `importlib.reload(nx_bridge_listener); nx_bridge_listener.start(force_restart=True)`，
  或在 NX 里再播放一次。
- **不要用 TCP 连接去探测监听端口**（连上就断）：每个空连接都会在 NX 主线程上走一遍 accept/recv/发送失败日志。
  要看端口状态用 `netstat -ano`。
- 日志：`%TEMP%\nx_mcp_bridge\listener.log`。启动后 60 秒内每 5 秒有一条心跳，解释器退出时有 atexit 记录。

## NX Open Python 的坑（2606 实测）

- `Point3d` / `Vector3d` 的坐标必须是 float：写 `0` 会报"期望的是 double 类型"，要写 `0.0` 或 `float(x)`。
- `NXOpen.Matrix3x3(Xx=1.0, ...)` 关键字构造会让 `NXMatrices.Create` 报 "Second parameter is invalid"。
  要先 `m = NXOpen.Matrix3x3()`，再逐个给 `m.Xx`… 赋值。
- `ExtrudeBuilder` 没有 `AngleTolerance` 属性（`DistanceTolerance` 也不必设）。
- `MeasureManager.NewMassProperties([body], 0.99)` 报 "No overload matches"。
- `theUfSession.Modl.AskFaceData` 在 Python 里不存在；包围盒用 `uf.ModlGeneral.AskBoundingBox(body.Tag)`。
- `part.PartUnits` 返回 `NXOpen.BasePart.Units`（不是 `Part.Units`）。
- NX 12 读不到路径很长的 `UGII_USER_DIR`。
- .NET 文档里带 `@` 的 out 参数，在 Python 里是返回值（多个时是元组）。
- 不要调用会弹模态对话框的 API（如 `NXMessageBox.Show`），会卡住直到用户手动关掉。

## 验证过的建模写法

不用草图，直接建非关联曲线再拉伸，最稳：

```python
import NXOpen, NXOpen.Features, NXOpen.GeometricUtilities
Op = NXOpen.GeometricUtilities.BooleanOperation.BooleanType

def extrude(part, curves, direction, start, end, op, target=None):
    sec = part.Sections.CreateSection(0.0095, 0.01, 0.5)
    sec.SetAllowedEntityTypes(NXOpen.Section.AllowTypes.OnlyCurves)
    rule = part.ScRuleFactory.CreateRuleBaseCurveDumb(curves)
    sec.AddToSection([rule], curves[0], NXOpen.NXObject.Null, NXOpen.NXObject.Null,
                     NXOpen.Point3d(0.0, 0.0, 0.0), NXOpen.Section.Mode.Create, False)
    b = part.Features.CreateExtrudeBuilder(NXOpen.Features.Feature.Null)
    try:
        b.Section = sec
        b.Direction = part.Directions.CreateDirection(
            NXOpen.Point3d(0.0, 0.0, 0.0), NXOpen.Vector3d(*map(float, direction)),
            NXOpen.SmartObject.UpdateOption.WithinModeling)
        b.Limits.StartExtend.Value.RightHandSide = str(start)
        b.Limits.EndExtend.Value.RightHandSide = str(end)
        b.BooleanOperation.Type = op          # Op.Create / Op.Subtract / Op.Intersect
        if target is not None:
            b.BooleanOperation.SetTargetBodies([target])
        return b.CommitFeature()
    finally:
        b.Destroy()
```

- 直线：`part.Curves.CreateLine(p1, p2)`；圆/圆弧：`part.Curves.CreateArc(center, part.NXMatrices.Create(m), r, a0, a1)`（弧度，负起始角可用）。
- 板件常用套路：侧视轮廓沿宽度拉伸成主体 → 俯视外形拉伸后 `Intersect` 修边 → 圆拉伸 `Subtract` 打孔。
- 圆角 `CreateEdgeBlendBuilder` + `AddChainset(collector, "R")`；倒角 `CreateChamferBuilder`，
  `Option = ChamferOption.SymmetricOffsets`、`FirstOffset = "1"`、`SmartCollector = collector`。
  边收集器：`col = part.ScCollectors.CreateCollector(); col.ReplaceRules([part.ScRuleFactory.CreateRuleEdgeDumb(edges)], False)`。
- 按几何找边：`a, b = edge.GetVertices()` 比较坐标；孔边用 `edge.SolidEdgeType == NXOpen.Edge.EdgeType.Circular` 加 `edge.GetLength()` 判断直径。
- 建完的辅助曲线用 `theSession.DisplayManager.BlankObjects(curves)` 隐藏。
- 每一步后用 `nx_screenshot` 看图、用面数和包围盒核对；先算好"预期面数"再对比，能很快发现布尔失败。

## 与用户协作的约定

- 用户要的是**在 NX 窗口里看得见的实时操作**（类似 Blender MCP），不是只交一个文件。实时连不上时先修连接，
  批处理只作为临时备选，并说明清楚。
- 每次 `nx_run_python` 是一个 NX 撤销步，`label` 写成有意义的中文步骤名（如"链节-孔口倒角"）。纯查询传 `undo=false`。
- 不要保存、关闭或覆盖用户已打开的部件；新模型放到新文件里。
- 图纸上没标的尺寸（圆角、倒角、"~" 近似尺寸）自己取合理值，但要明确告诉用户哪些是估的。
- 用户在中文环境下工作（NX 界面也是简体中文，journal 叫"操作记录"），说明文档用中文写。

## 参考资料来源

- 最有用的中文 NX 二次开发论坛是 ugsnx.com（"NX二次开发" forum-118、"NX外挂资源区" forum-83），
  但每页都有算术题人机验证，搜索和附件下载要登录且扣 G币。
- ugapi.com 以 C/C++/GRIP 为主，很多内容要 VIP；ugnx.net、ugboke.com 基本没有 journal。
- 本机 NX 自带的 .NET API 文档（`nx_api_lookup` 会建 SQLite 索引）比网上资料可靠，优先查它，再用 `dir()` / `help()` 在运行中的 NX 里确认。
