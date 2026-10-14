' 来源：UG爱好者论坛 · NX二次开发《UG12使用VB脚本批量输出后处理，如何获取程序组所有工序》
'       https://www.ugsnx.com/thread-349106-1-1.html
' 抓取：2026-10-14（求助帖正文贴出的代码，非附件）
'
' 目标（楼主原意）：在 CAM 里选中程序组后运行：逐个工序按“工序名前三个字符+刀具名”重命名，
' 并后处理输出到 E:\工作文件\Bln\Bln\<程序组名>\ 下。后处理器 YIYI_360（NX 12）。
'
' 状态：这份代码本身跑不通，楼主报错“第 62 行：未定义类型 CAM.Operations”。
' 可参考的有：CAM 环境检查（AskApplicationModule）、读取导航器里选中对象、CAMSetup.IsGroup 判断。
' 出错处（未在本机验证的修改方向）：
'   - NCGroup 没有 .Operations 集合，取成员一般用 theNcGroup.GetMembers()，再 TryCast 成 CAM.Operation；
'   - CAMSetup 没有 PostBuilder.PostOperation，后处理一般用 CAMSetup.PostprocessWithSetting(...)；
'   - operation.Tool 也不是现成属性，刀具要从工序的父级刀具组里取。

Option Strict Off
Imports System
Imports System.IO
Imports System.Windows.Forms
Imports NXOpen
Imports NXOpen.UF
Imports NXOpen.Utilities
Imports System.Collections.Generic ' 添加此行以引用 List 类型

Module TestRenameCamObjects
    Dim theSession    As Session   = Session.GetSession()
    Dim theUFSession  As UFSession = UFSession.GetUFSession()
    Dim theUI         As UI        = UI.GetUI()
    Dim TempPath      As String    = Environment.GetEnvironmentVariable("TMP")
    Dim UGRelease     As String    = Nothing
    Dim UGFullRelease As String    = Nothing
    Dim postProcessorPath As String = "D:\Program Files\Siemens\NX 12.0\MACH\resource\postprocessor\YIYI_360.tcl"
    Dim postProcessorDefPath As String = "D:\Program Files\Siemens\NX 12.0\MACH\resource\postprocessor\YIYI_360.def"
    Dim outputBasePath As String = "E:\工作文件\Bln\Bln"

    Sub Main(Args As String())
        theSession.LogFile.WriteLine("Executing ... " & _
            System.Reflection.Assembly.GetExecutingAssembly().Location)

        Threading.Thread.CurrentThread.CurrentCulture = New Globalization.CultureInfo("en-US")
        Threading.Thread.CurrentThread.CurrentUICulture = New Globalization.CultureInfo("en-US")

        UGRelease = theSession.GetEnvironmentVariableValue("UGII_VERSION")
        UGFullRelease = theSession.GetEnvironmentVariableValue("UGII_FULL_VERSION")

        System.Windows.Forms.Application.EnableVisualStyles()

        ' 检查是否在CAM环境中
        Dim AppID As Integer = UFConstants.UF_APP_NONE
        theUFSession.UF.AskApplicationModule(AppID)
        If AppID <> UFConstants.UF_APP_CAM Then
            theUI.NXMessageBox.Show("Application mismatch", NXMessageBox.DialogType.Error, "No NX CAM session, Exiting!")
            Exit Sub
        End If

        ' 获取选中的程序组
        Dim selectedGroups As List(Of TaggedObject) = New List(Of TaggedObject)()
        For i As Integer = 0 To theUI.SelectionManager.GetNumSelectedObjects() - 1
            Dim theSelectedObject As TaggedObject = theUI.SelectionManager.GetSelectedTaggedObject(i)
            If theSession.Parts.Work.CAMSetup.IsGroup(theSelectedObject) Then
                selectedGroups.Add(theSelectedObject)
            End If
        Next

        ' 对选中的程序组进行处理
        For Each theSelectedObject As TaggedObject In selectedGroups
            Dim theNcGroup As CAM.NCGroup = CType(theSelectedObject, CAM.NCGroup)
            Dim groupName As String = theNcGroup.Name
            Dim groupOutputPath As String = Path.Combine(outputBasePath, groupName)

            ' 创建子文件夹
            If Not Directory.Exists(groupOutputPath) Then
                Directory.CreateDirectory(groupOutputPath)
            End If

            ' 获取程序组中的所有工序
            Dim operations As CAM.Operations = theNcGroup.Operations   ' <- 楼主报错的第 62 行
            For Each operation As CAM.Operation In operations
                Dim operationName As String = operation.Name
                Dim toolName As String = operation.Tool.Name
                Dim newOperationName As String = operationName.Substring(0, 3) & toolName

                ' 重命名工序
                operation.SetName(newOperationName)

                ' 输出工序
                Dim postBuilder As CAM.PostBuilder = theSession.Parts.Work.CAMSetup.PostBuilder
                postBuilder.PostOperation(operation, postProcessorPath, postProcessorDefPath, groupOutputPath, False)
            Next
        Next
    End Sub

    Public Function GetUnloadOption(ByVal dummy As String) As Integer
        Return CInt(Session.LibraryUnloadOption.Immediately)
    End Function
End Module
