# NX Journal 参考资料收集

收集时间：2026-10-14。目标：从下列站点找网友上传的 NX journal（.vb / .cs / .py），作为本项目写 NX Open 脚本的参考。

## 结论速览

| 站点 | 结果 | 说明 |
|---|---|---|
| [CSDN 164239207](https://blog.csdn.net/weixin_28559803/article/details/164239207) | 4 段代码片段 → [csdn/164239207_snippets.vb](csdn/164239207_snippets.vb) | 只是示意片段，多处 API 名称存疑，不能直接跑 |
| [ugsnx.com 论坛](https://www.ugsnx.com/forum.php) | **主要来源**：3 份正文代码已保存，另有 8 个带 journal/脚本附件的帖子（需登录 + G币，未下载） | 扫完了“NX二次开发”（forum-118，75 页 1494 帖）和“NX外挂资源区”（forum-83，87 页 1735 帖）；全站搜索要登录 |
| [ugsnx thread-366447](https://www.ugsnx.com/thread-366447-1-1.html) | 没有 journal | 是“UG型腔铣过切问题”求助帖，只有一张截图 |
| [ugnx.net/wz](https://www.ugnx.net/wz) | 没有 journal | 站内搜 journal / 日志 / 录制 / VB / Python 都没有 journal 帖；相关的只有宏(.macro)、GRIP、C++ DLL 包 |
| [ugboke.com](https://www.ugboke.com/) | 没有 journal | 培训机构站点，内容是曲面教程、图纸、软件下载；站内搜索不可用 |
| [ugapi.com forum-19](https://www.ugapi.com/forum-19.html) | 没有 journal 附件 | 该版块是 NXOpen C/C++（24 页、约 390 帖）；站内搜索要登录，很多帖子正文要登录/VIP 才能看全 |

注：NX 中文版把 journal 叫“操作记录”（工具 → 操作记录，Alt+F8 打开操作记录管理器）。

## 已保存（ugsnx 正文里直接贴出的代码）

| 文件 | 来源帖 | 内容 | 状态 |
|---|---|---|---|
| [ugsnx/339595_nx12_python_setup.md](ugsnx/339595_nx12_python_setup.md) | [339595](https://www.ugsnx.com/thread-339595-1-1.html) | NX12 用外部 Python 3.6 跑 NXOpen、IDE 代码提示、PyDev 调试、UF 建块 + tag 转对象 | 与本项目同为 NX12 / Python 3.6.1，最有参考价值 |
| [ugsnx/366378_create_block.vb](ugsnx/366378_create_block.vb) | [366378](https://www.ugsnx.com/thread-366378-1-1.html) | VB：新建部件 + BlockFeatureBuilder 建居中长方体 | 只贴了主体，缺 Module 外壳和输入框类 |
| [ugsnx/349106_batch_post_rename.vb](ugsnx/349106_batch_post_rename.vb) | [349106](https://www.ugsnx.com/thread-349106-1-1.html) | VB：CAM 选中程序组 → 工序重命名 + 批量后处理 | 楼主自己报错跑不通，文件头注明了出错原因 |

## 带 journal / 脚本附件的帖子（未下载）

附件都要登录论坛账号才能下载，每个扣 1 G币。

| 帖子 | 附件 | 内容 |
|---|---|---|
| [359696 自制NX2412改刀号宏](https://www.ugsnx.com/thread-359696-1-1.html) | 1.0改刀号.zip (3.62 KB)、2.0批量改刀号.zip (4.98 KB) | 用 Alt+F8 运行的操作记录（CAM 选中程序组改刀号），NX2412 |
| [349103 UG12使用VB脚本对程序组进行自定义重命名](https://www.ugsnx.com/thread-349103-1-1.html) | jiaoben.rar (225 Bytes) | reneme_program_group.vb + open_file.py（体积很小，可能只是网盘链接） |
| [339370 用python实现customfeature](https://www.ugsnx.com/thread-339370-1-1.html) | application.zip (9.44 KB) | NXOpen Python 自定义特征（参照官方 Bsurface 样例） |
| [359736 Python 二次开发 VS Code 代码提示设置](https://www.ugsnx.com/thread-359736-1-1.html) | NX12.zip (813.72 KB) | NX12 NXOpen Python 存根，配给 Pylance 的 Extra Paths 用 |
| [339595 python二次开发NX环境搭建](https://www.ugsnx.com/thread-339595-1-1.html) | Debug_NXOpen_Python_with_NX12_and_Eclipse_PyDev.zip (866.77 KB) | NX12 + Eclipse PyDev 调试说明 |
| [351354 C#基于open开发导出钣金展开图](https://www.ugsnx.com/thread-351354-1-1.html) | 钣金导出.rar (3.29 KB) | C# 源码（依赖 netDXF） |
| [29112 NX Open API编程技术_书中配套代码](https://www.ugsnx.com/thread-29112-1-1.html) | part1.rar (400 KB) + part2.rar (265.74 KB) | 书的配套代码，以 C/C++ 为主 |
| [281631 用C#进行NXOPEN二次开发的基本流程](https://www.ugsnx.com/thread-281631-1-1.html) | UG NX .net平台开发环境配置.rar | C# 环境配置文档 |

不值得下载的：[333043 5000NX开发源代码](https://www.ugsnx.com/thread-333043-1-1.html)（157 Bytes）和 [345303 西门子官方二次开发文档](https://www.ugsnx.com/thread-345303-1-1.html)（292 Bytes，还要求阅读权限 50），附件只装了一个网盘链接。

## 其他站点里值得留意的帖子（未下载）

### ugapi.com（NXOpen C#，可作为 journal 写法参考，但正文部分需登录）
- [thread-10015 分享C# NXOpen源代码 组件视图管理器](https://www.ugapi.com/thread-10015.html)
- [thread-10016 C# winform 嵌入式NX 二次开发](https://www.ugapi.com/thread-10016.html)（可见部分完整：用 UFDisp 截图导出 bmp/png 等）
- [thread-10017 NXOpen C# 创建曲线](https://www.ugapi.com/thread-10017.html)（中间部分需登录）
- [thread-10286 使用C# 调用 UFun 创建直线代码](https://www.ugapi.com/thread-10286.html)（后半部分需登录）
- [thread-10256 录制的代码和UI界面结合的时候报错](https://www.ugapi.com/thread-10256.html)：结论是录制代码里的“选择对象”部分不能直接用，要换成自己界面选到的对象
- [thread-10293 NX10录制【凹面】问题](https://www.ugapi.com/thread-10293.html)：NX 10.0.0.24 录制凹面无效是版本 bug，升级 10.0.3.5 解决

### ugnx.net（不是 journal，仅供了解）
- [fx/628 UG使用宏文件macro将零件移动到绝对坐标](https://www.ugnx.net/fx/628)：附件是 `.macro` 宏文件（NX 老式宏，不是 NX Open journal）
- [fx/892 NX OPEN C++二次开发：创建长方体](https://www.ugnx.net/fx/892)：BlockFeatureBuilder 的 C++ 写法，与本项目 `nx_test_block.py` 里的 Python 写法一一对应
- [fx/1142 十字线](https://www.ugnx.net/fx/1142)：GRIP 源码
- [fx/393](https://www.ugnx.net/fx/393)、[fx/1034](https://www.ugnx.net/fx/1034)、[fx/854](https://www.ugnx.net/fx/854)：附件是编译好的 DLL 压缩包，来源不明，没有下载
