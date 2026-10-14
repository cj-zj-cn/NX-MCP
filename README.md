# NX-MCP：让 Claude 实时控制 Siemens NX

```
Claude ⇄ (MCP, stdio) ⇄ nx_mcp_server.py ⇄ (127.0.0.1 + token) ⇄ nx_bridge_listener.py（在 NX 里运行）⇄ NX Open
```

Claude 把 NX Open Python 代码发进你正在用的 NX 窗口，在 NX 主线程执行，模型实时出现在屏幕上。
全部用 Python 标准库实现，不需要 pip 安装任何东西，也不需要 NX Open 作者许可证。

## 文件

| 文件 | 作用 |
|---|---|
| `nx_bridge_listener.py` | 在 NX 里运行的监听器 |
| `startup/nx_mcp_autostart.py` | NX 启动时自动加载监听器 |
| `nx_mcp_server.py` | MCP 服务器（Claude 通过它控制 NX），也可以在命令行自检 |
| `nx_test_block.py` | 批处理示例脚本（方块 + 通孔） |
| `cache/` | API 文档索引、批处理临时脚本（可以随时删除，会自动重建） |

## 版本支持

| 版本 | 实时控制 | 后台批处理 |
|---|---|---|
| Designcenter 2606（Python 3.12） | ✅ | ✅ |
| NX 12.0（Python 3.6） | ❌ 监听器会拒绝启动 | ✅ |

NX 12 把 Python 脚本放在子解释器里运行，而且空闲时一直持有 GIL，Win32 定时回调一进 Python 就会死锁、NX 卡死。
监听器启动前会检测这种情况并拒绝启动。NX 12 请用 `nx_batch_run`。

## 1. 在 NX 里启动监听器

**手动（每次打开 NX 后做一次）**：Alt+F8（工具 → 操作记录 → 播放），选择 `nx_bridge_listener.py`。
信息窗口显示"监听已启动，端口 48765"就成功了。再次播放会重启监听器，并加载最新代码。

> **`nx_bridge_listener.py` 开头的 `# nx: threaded` 不能删。** Designcenter 2606 默认把播放的 Python 脚本放进一次性子解释器，
> 播放结束就销毁，监听器随之关闭（症状：显示"监听已启动"但连不上）。这行注释是 NX `nxpythonldr.dll` 的开关，
> 让脚本在主解释器里运行。Ctrl+U 不能运行 .py。

换电脑请看 [迁移说明.md](迁移说明.md)。

**自动（推荐）**：让 NX 启动时加载 `startup` 文件夹。设置一个用户环境变量后重新打开 NX：

```powershell
[Environment]::SetEnvironmentVariable('UGII_USER_DIR', 'D:\NX-MCP', 'User')
```

NX 12 也会读到这个设置，但监听器会自己检测出来并跳过，不影响 NX 12 正常使用。
如果你已经有自己的 `UGII_USER_DIR`，就把 `startup\nx_mcp_autostart.py` 复制到那个目录的 `startup` 文件夹里。

## 2. 注册到 Claude Code

```powershell
claude mcp add -s user nx -- "python.exe 的完整路径" "D:\NX-MCP\nx_mcp_server.py"
```

注册后需要**新开一个 Claude 会话**，新会话里才有这些工具。

## 工具

| 工具 | 作用 |
|---|---|
| `nx_status` | 当前部件、特征列表、实体和面数 |
| `nx_run_python` | 在 NX 窗口里执行 NX Open Python 代码。自动设撤销标记，出错自动回滚 |
| `nx_screenshot` | 截取 NX 图形窗口（可切换视图、白底） |
| `nx_undo` | 撤销 Claude 最近的几步操作 |
| `nx_batch_run` | 用 run_journal.exe 在后台 NX 进程里跑完整脚本（NX 12 用这个） |
| `nx_api_lookup` | 查本机 NX 自带的 NX Open API 文档（2606 约 15 万条，NX 12 约 10 万条） |

`nx_run_python` 预置的变量与 NX 录制的 Journal 一致（`theSession`、`workPart`、`displayPart`、`theUI`、`theUfSession`），
所以在 NX 里录制的操作记录可以直接粘贴给 Claude 执行或修改。

## 自检与排错

```powershell
python nx_mcp_server.py --check                      # 列出已连接的 NX 及当前部件
python nx_mcp_server.py --lookup CylinderBuilder     # 查 API
python nx_mcp_server.py --lookup ChamferBuilder --nx 12
```

- 监听器日志：`%TEMP%\nx_mcp_bridge\listener.log`
- Claude 提示"NX 没有响应"：NX 里开着对话框或正在执行命令，关掉对话框再试。
- 同时开着多个 NX 时，工具的 `target` 参数可以填版本号（如 `2606`）或进程 pid 来选择。

## 安全

- 只监听 `127.0.0.1`，外部机器连不进来。
- 每次请求都要带上监听器启动时随机生成的 token，token 写在 `%TEMP%\nx_mcp_bridge\<pid>.json`，只有当前 Windows 用户能读。
- 监听器能在 NX 里执行任意 Python 代码，权限和你本人运行 NX 相同。不用的时候关掉 NX，监听器就停止了。
