' 来源：CSDN《UG画图外挂组合拳：角色定制+Journal+NXOpen二次开发》
'       https://blog.csdn.net/weixin_28559803/article/details/164239207
' 抓取：2026-10-14
'
' 注意：文章里的 4 段代码都是片段/示意，不是可直接运行的 journal。
' 下面标了 [存疑] 的调用在 NX Open API 里大概率不存在或名称不对，
' 实际写法以 NX 录制出来的 journal 为准（尚未在本机 NX 12 上验证）。

' ---------------------------------------------------------------
' 片段 1：修改制图对象线宽（文章说是“录制线宽修改后能看到的片段”）
' [存疑] DrawingSheets.CreateSplineEdit / NXOpen.LineWidth 不像真实 API；
'        录制出来通常是 theSession.DisplayManager.NewDisplayModification()
'        + displayModification1.NewWidth = DisplayableObject.ObjectWidth.Thick
' ---------------------------------------------------------------
Dim edit1 As NXOpen.Drawing.ISplineEdit = workPart.DrawingSheets.CreateSplineEdit
' 或者线宽设置相关调用
displayModification1.NewLineWidth = NXOpen.LineWidth.Thick

' ---------------------------------------------------------------
' 片段 2：遍历所有图纸页批量导出 PDF（把录制的单页导出改成循环）
' [存疑] pdfExporter1 的来源没给出；真实 API 一般是
'        workPart.PlotManager.CreatePrintPdfbuilder()，图纸通过 SourceBuilder.SetSheets 设置
' ---------------------------------------------------------------
Dim sheets As NXOpen.Drawing.SheetCollection = workPart.DrawingSheets
For Each sheet As NXOpen.Drawing.Sheet In sheets
    ' 设置导出参数，调用PDF导出命令
    pdfExporter1.Sheet = sheet
    pdfExporter1.OutputFile = "D:\export\" & sheet.Name & ".pdf"
    pdfExporter1.Commit()
Next

' ---------------------------------------------------------------
' 片段 3（C#）：取工作视图窗口句柄，用于强制刷新显示
' [存疑] View.WindowHandle() 不像真实 API
' ---------------------------------------------------------------
' NXOpen.View view = workPart.Views.WorkView;
' IntPtr hwnd = view.WindowHandle();

' ---------------------------------------------------------------
' 片段 4：遍历 PMI 标注
' [存疑] PMIManager.PMIs 存在，但元素类型一般是 NXOpen.Annotations.Pmi，而不是 NXOpen.PMI.IPMI
' ---------------------------------------------------------------
Dim pmiCollection As NXOpen.PMI.PMICollection = workPart.PMIManager.PMIs
For Each pmi As NXOpen.PMI.IPMI In pmiCollection
    ' 读取PMI的位置和标注字符串等信息
    Dim annotation As NXOpen.Annotations.Annotation = TryCast(pmi, NXOpen.Annotations.Annotation)
    If annotation IsNot Nothing Then
        ' 处理标注
    End If
Next
