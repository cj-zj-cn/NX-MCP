' 来源：UG爱好者论坛 · NX二次开发《创建带长方体 源程序分享》
'       https://www.ugsnx.com/thread-366378-1-1.html
' 抓取：2026-10-14（正文直接贴出的代码，非附件）
'
' 注意：帖子只贴了 Main 里的主体片段，缺少 Module 外壳、theSession/theUI 定义，
' 以及自定义输入框类 BlockInputDialog（WinForms，3 个输入框）。不能直接回放。
' 要点：BlockFeatureBuilder 的“原点+边长”写法，原点取 (-L/2,-W/2,-H/2) 让长方体中心落在原点；
' SetOriginAndLengths 的边长参数本应是 String，这里传 Double 依赖 VB 的隐式转换（Option Strict Off）。
' 对照本项目 nx_test_block.py 的 create_block()：同一套 API。

'创建新零件
Dim newPart As Part = Nothing
Dim partName As String = InputBox("请输入新零件名称（不带扩展名）：", "新建零件", "block_part")
If partName Is Nothing OrElse partName = "" Then Return

Try
    ' 创建新部件文件，使用毫米单位
    newPart = theSession.Parts.NewBaseDisplay(partName & ".prt", Part.Units.Millimeters)
    If newPart Is Nothing Then
        theUI.NXMessageBox.Show("错误", NXMessageBox.DialogType.Error, "创建新零件失败！")
        Return
    End If

    Dim workPart As Part = newPart

    ' 获取用户输入的长宽高（使用自定义对话框，3个独立输入框）
    Dim dialog As New BlockInputDialog()
    Dim result As DialogResult = dialog.ShowDialog()
    If result <> DialogResult.OK OrElse Not dialog.DialogOK Then
        Return
    End If

    Dim length As Double = dialog.Length
    Dim width As Double = dialog.Width
    Dim height As Double = dialog.Height

    ' 开始
    Dim nullFeatures_Feature As Features.Feature = Nothing
    Dim blockFeatureBuilder As Features.BlockFeatureBuilder
    blockFeatureBuilder = workPart.Features.CreateBlockFeatureBuilder(nullFeatures_Feature)

    ' 设置长方体类型为原点和边长
    blockFeatureBuilder.Type = Features.BlockFeatureBuilder.Types.OriginAndEdgeLengths

    ' 设置原点，使长方体中心与工作坐标系原点重合
    ' 长方体的一个角点为 (-length/2, -width/2, -height/2)，这样中心就在 (0, 0, 0)
    Dim origin As Point3d = New Point3d(-length/2, -width/2, -height/2)
    blockFeatureBuilder.SetOriginAndLengths(origin, length, width, height)

    ' 应用并生成长方体
    Dim nXObject1 As NXObject
    nXObject1 = blockFeatureBuilder.Commit()
    blockFeatureBuilder.Destroy()

' （帖子到此结束，后面的 Catch/End Try 未贴出）
