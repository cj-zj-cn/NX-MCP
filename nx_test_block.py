# NX Open 测试脚本：创建一个 L x W x H 的方块，中心打一个直径 D 的通孔，保存为 .prt
#
# 用法（命令行后台运行）：
#   run_journal.exe nx_test_block.py -args <输出.prt> [L W H D]
# 也可以在 NX 里 Alt+F8 直接播放（此时不带参数，用默认值，输出到脚本同目录）。

import math
import os
import sys
import traceback

import NXOpen
import NXOpen.Features
import NXOpen.GeometricUtilities

LOG_LINES = []


def log(msg):
    msg = str(msg)
    LOG_LINES.append(msg)
    print(msg)


def parse_args(argv):
    here = os.path.dirname(os.path.abspath(__file__))
    out = argv[0] if len(argv) > 0 else os.path.join(here, "output", "test_block.prt")
    dims = [float(x) for x in argv[1:5]] if len(argv) >= 5 else [100.0, 50.0, 20.0, 10.0]
    return os.path.abspath(out), dims


def create_block(part, length, width, height):
    builder = part.Features.CreateBlockFeatureBuilder(NXOpen.Features.Feature.Null)
    try:
        builder.Type = NXOpen.Features.BlockFeatureBuilder.Types.OriginAndEdgeLengths
        builder.BooleanOption.Type = NXOpen.GeometricUtilities.BooleanOperation.BooleanType.Create
        builder.SetOriginAndLengths(NXOpen.Point3d(0.0, 0.0, 0.0),
                                    str(length), str(width), str(height))
        return builder.CommitFeature()
    finally:
        builder.Destroy()


def cut_through_hole(part, target_body, cx, cy, height, diameter):
    builder = part.Features.CreateCylinderBuilder(NXOpen.Features.Feature.Null)
    try:
        builder.Type = NXOpen.Features.CylinderBuilder.Types.AxisDiameterAndHeight
        builder.Diameter.RightHandSide = str(diameter)
        # 圆柱从底面下方 5mm 开始，高度多出 10mm，保证完全贯穿
        builder.Height.RightHandSide = str(height + 10.0)

        builder.BooleanOption.Type = NXOpen.GeometricUtilities.BooleanOperation.BooleanType.Subtract
        builder.BooleanOption.SetTargetBodies([target_body])

        base = NXOpen.Point3d(cx, cy, -5.0)
        direction = part.Directions.CreateDirection(
            base, NXOpen.Vector3d(0.0, 0.0, 1.0), NXOpen.SmartObject.UpdateOption.WithinModeling)
        axis = builder.Axis
        axis.Direction = direction
        axis.Point = part.Points.CreatePoint(base)

        return builder.CommitFeature()
    finally:
        builder.Destroy()


def report_volume(part, body):
    try:
        units = [NXOpen.Unit.Null] * 5
        units[0] = part.UnitCollection.GetBase("Area")
        units[1] = part.UnitCollection.GetBase("Volume")
        units[2] = part.UnitCollection.GetBase("Mass")
        units[3] = part.UnitCollection.GetBase("Length")
        measure = part.MeasureManager.NewMassProperties(units, 0.99, [body])
        return measure.Volume
    except Exception:
        log("（体积测量失败，跳过）\n" + traceback.format_exc())
        return None


def main(argv):
    out_path, (L, W, H, D) = parse_args(argv)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    if os.path.exists(out_path):
        os.remove(out_path)

    session = NXOpen.Session.GetSession()
    log("NX 安装目录: %s" % os.environ.get("UGII_BASE_DIR", "?"))
    log("Python: %s" % sys.version.split()[0])
    log("参数: L=%g W=%g H=%g D=%g -> %s" % (L, W, H, D, out_path))

    part = session.Parts.NewDisplay(out_path, NXOpen.Part.Units.Millimeters)

    block = create_block(part, L, W, H)
    body = block.GetBodies()[0]
    log("方块创建完成: %s" % block.JournalIdentifier)

    hole = cut_through_hole(part, body, L / 2.0, W / 2.0, H, D)
    log("通孔创建完成: %s" % hole.JournalIdentifier)

    faces = body.GetFaces()
    log("实体面数: %d（带通孔的方块应为 7）" % len(faces))

    vol = report_volume(part, body)
    expected = L * W * H - math.pi * (D / 2.0) ** 2 * H
    if vol is not None:
        log("实体体积: %.3f mm^3（理论值 %.3f）" % (vol, expected))

    part.Save(NXOpen.BasePart.SaveComponents.TrueValue, NXOpen.BasePart.CloseAfterSave.FalseValue)
    log("已保存: %s" % out_path)


if __name__ == "__main__":
    args = sys.argv[1:]
    log_path = None
    try:
        out_path, _ = parse_args(args)
        log_path = os.path.splitext(out_path)[0] + ".log"
        main(args)
        log("RESULT: OK")
    except Exception:
        log("RESULT: FAILED\n" + traceback.format_exc())
    finally:
        if log_path:
            try:
                os.makedirs(os.path.dirname(log_path), exist_ok=True)
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(LOG_LINES) + "\n")
            except Exception:
                pass
