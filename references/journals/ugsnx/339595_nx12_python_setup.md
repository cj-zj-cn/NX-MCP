# NX12 用外部 Python 跑 NXOpen 脚本（环境搭建）

来源：UG爱好者论坛 · NX二次开发《python二次开发NX环境搭建》（2024-02-01）
https://www.ugsnx.com/thread-339595-1-1.html
抓取：2026-10-14。以下是帖子要点和正文里贴出的代码；附件 `Debug_NXOpen_Python_with_NX12_and_Eclipse_PyDev.zip`（866.77 KB）尚未下载。

与本项目的关系：本机 NX 12 自带的 Python 也是 3.6.1（见 `output/test_block_nx12.log`）。

## 1. 让 NX 使用外部 Python 3.6

NX 12 内置 Python 3.6.1，外部要装 3.6.x。在 `NX 12.0\UGII\ugii_env.dat` 末尾加两行（路径按实际修改）：

```ini
UGII_PYTHON_LIBRARY_DIR="D:\Program Files\Python36"
UGII_PYTHONPATH="D:\Program Files\Python36;D:\Program Files\Python36\DLLs;D:\Program Files\Python36\Lib;D:\Program Files\Python36\Lib\site-packages;D:\Program Files\Python36\libs;G:\Program Files\Siemens\NX 12.0\NXBIN\python"
```

好处：可以用 pip 装的第三方库，IDE 也能给出代码提示。

## 2. 验证：Alt+F8 运行这个 .py

```python
import NXOpen

session=NXOpen.Session.GetSession()
lw=session.ListingWindow
lw.Open()
lw.WriteLine('Hello World')
```

## 3. 让 IDE 识别 NXOpen（代码提示）

在 `Python36\Lib\site-packages\` 里新建 `nxopen.pth`，内容是一行：

```
G:\Program Files\Siemens\NX 12.0\NXBIN\python
```

楼主的结论：IDLE、Eclipse+PyDev（官方推荐）、PyCharm 都能提示；VS Code 当时没成功。
另见 [thread-359736](https://www.ugsnx.com/thread-359736-1-1.html)：用 VS Code Pylance 的 `Analysis: Extra Paths` 指向 NX12 存根包（附件 NX12.zip，813.72 KB），可以解决 VS Code 的提示问题。

## 4. 用 PyDev 远程调试 + UF 方式建块

前三行用于挂调试器（`#nx: threaded` 让 NX 在线程里跑这个脚本）：

```python
#nx: threaded
import pydevd
pydevd.settrace()

import NXOpen_UF
import NXOpen

theUfSession: NXOpen_UF.UFSession = NXOpen_UF.UFSession.GetUFSession()
modl: NXOpen_UF.ModlFeatures = theUfSession.ModlFeatures
ui: NXOpen_UF.Ui = theUfSession.Ui
ui.OpenListingWindow()

block_t = modl.CreateBlock(NXOpen_UF.ModlFeatureSigns.NULLSIGN, 0, [0.0, 0.0, 0.0],
                           ["100", "50", "30"])
block_t1 = modl.CreateBlock1(NXOpen_UF.ModlFeatureSigns.NULLSIGN, [
                             100.0, 0.0, 0.0], ["100", "50", "30"])
block_o = NXOpen.TaggedObjectManager.GetTaggedObject(block_t)
block_o1 = NXOpen.TaggedObjectManager.GetTaggedObject(block_t1)

ui.WriteListingWindow(f"{block_t}:{block_o}\n")
ui.WriteListingWindow(f"{block_t1}:{block_o1}\n")
```

要点：UF 函数返回的是 tag（int），用 `NXOpen.TaggedObjectManager.GetTaggedObject(tag)` 转成 NXOpen 对象。
