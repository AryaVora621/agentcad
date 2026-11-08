"""A 5-inch FPV quadcopter frame: two CNC-cut carbon plates, four printed arms and the parts that sit on them.

Axes: +x is forward, +y is left, +z is up. Front/rear are the two ends along x, left/right the two sides along y.
Plates are cut flat from carbon sheet; arms print flat on the bed and bolt to the bottom plate; the standoffs clamp
the top plate on the arm roots. Motors, props, stack, camera and battery are stand-ins built from real part sizes
so clearances can be measured on the assembled pose.
"""
import math

import cadquery as cq

from agentcad import Part, Pose

# Frame parameters (the things a designer chooses).
MOTOR_X = 72.0  # fore-aft distance from the frame centre to each motor axis
MOTOR_Y = 86.0  # side distance from the frame centre to each motor axis
POCKET_R = 2.0  # corner radius of the lightening pockets in the plates
ROOT_X, ROOT_Y = 34.0, 18.0  # where each arm starts on the plate
ARM_T = 6.0
BOTTOM_T, TOP_T = 4.0, 2.0
STANDOFF_H = 22.0
M3_HOLE = 3.4  # clearance for M3 in the plates and arms

# Real component sizes (datasheet values). They never come from the frame parameters above.
PROP_DIA = 129.5  # 5.1 inch
MOTOR_BELL_D = 27.9  # 2207 motor bell
MOTOR_PATTERN = 16.0  # M3 square under a 2207 motor
STACK_SIZE = 36.0
STACK_PATTERN = 30.5
TOOL_D = 3.175  # router endmill, 1/8 inch
MIN_PROP_GAP = 8.0
MIN_PROP_FRAME_GAP = 2.0

Z_ARM = BOTTOM_T
Z_ARM_TOP = Z_ARM + ARM_T
Z_TOP_PLATE = Z_ARM_TOP + STANDOFF_H
Z_PLATE_TOP = Z_TOP_PLATE + TOP_T
MOTOR_H = 27.0  # arm top to the prop plane, measured on the stand-in below
Z_PROP = Z_ARM_TOP + MOTOR_H

CORNERS = {"fl": (1, 1), "fr": (1, -1), "rl": (-1, 1), "rr": (-1, -1)}
OUTLINE = [(60, 12), (46, 36), (-46, 36), (-60, 12), (-60, -12), (-46, -36), (46, -36), (60, -12)]
STACK_HOLES = [(sx * STACK_PATTERN / 2, sy * STACK_PATTERN / 2) for sx in (-1, 1) for sy in (-1, 1)]
POCKETS = [(0, 0, 16, 16), (44, 0, 18, 20), (-44, 0, 18, 20), (0, 27, 16, 12), (0, -27, 16, 12)]
STRAPS = [(-22, 22.5), (22, 22.5), (-22, -22.5), (22, -22.5)]


def arm_frame(sx, sy):
    """Root point, heading in degrees and length of the arm that reaches the motor at corner (sx, sy)."""
    rx, ry = sx * ROOT_X, sy * ROOT_Y
    dx, dy = sx * MOTOR_X - rx, sy * MOTOR_Y - ry
    return rx, ry, math.degrees(math.atan2(dy, dx)), math.hypot(dx, dy)


def arm_bolts(sx, sy):
    """The two root bolt positions in plate coordinates; the first one (larger |y|) also carries a standoff."""
    rx, ry, heading, _ = arm_frame(sx, sy)
    u = (math.cos(math.radians(heading)), math.sin(math.radians(heading)))
    v = (-u[1], u[0])
    pts = [(rx + 10 * u[0] + s * 8 * v[0], ry + 10 * u[1] + s * 8 * v[1]) for s in (1, -1)]
    return sorted(pts, key=lambda p: -abs(p[1]))


def holes(points, diameter, thickness):
    return cq.Workplane("XY").pushPoints(points).circle(diameter / 2).extrude(thickness)


def plate(thickness, extra_holes):
    body = cq.Workplane("XY").polyline(OUTLINE).close().extrude(thickness).edges("|Z").fillet(6)
    standoff_pts = [arm_bolts(sx, sy)[0] for sx, sy in CORNERS.values()]
    body = body.cut(holes(STACK_HOLES + standoff_pts + extra_holes, M3_HOLE, thickness))
    body = body.cut(cq.Workplane("XY").pushPoints(STRAPS).slot2D(22, 4).extrude(thickness))
    for x, y, w, h in POCKETS:
        cutter = (cq.Workplane("XY").center(x, y).rect(w, h).extrude(thickness)
                  .edges("|Z").fillet(POCKET_R))
        body = body.cut(cutter)
    return body


def arm(length):
    """One arm along +x from its root at the origin to the motor axis at (length, 0), flat on the bed."""
    web = (cq.Workplane("XY")
           .polyline([(0, -13), (30, -13), (length - 16, -8), (length, -8), (length, 8), (length - 16, 8),
                      (30, 13), (0, 13)]).close().extrude(ARM_T))
    body = web.union(cq.Workplane("XY").center(length, 0).circle(16).extrude(ARM_T))
    body = body.edges("|Z").edges("<X").fillet(5)
    bolts = [(10, 8), (10, -8)]
    motor_pts = [(length + sx * MOTOR_PATTERN / 2, sy * MOTOR_PATTERN / 2) for sx in (-1, 1) for sy in (-1, 1)]
    body = body.cut(holes(bolts + motor_pts, M3_HOLE, ARM_T))
    return body.cut(holes([(length, 0)], 10.0, ARM_T))  # relief for the motor shaft clip


def motor():
    """2207 motor on its mounting base, shaft up; origin on the base underside."""
    base = cq.Workplane("XY").circle(12.5).extrude(3.5)
    stator = cq.Workplane("XY").workplane(offset=3.5).circle(10.5).extrude(6)
    bell = cq.Workplane("XY").workplane(offset=9.5).circle(MOTOR_BELL_D / 2).extrude(14.5)
    collar = cq.Workplane("XY").workplane(offset=24).circle(6).extrude(3)
    shaft = cq.Workplane("XY").workplane(offset=24).circle(2.5).extrude(MOTOR_H - 24 + 4)
    return base.union(stator).union(bell).union(collar).union(shaft)


def prop(toward_deg):
    """Three-blade 5.1 inch prop; one blade points toward_deg (the worst case for a neighbour)."""
    blade = (cq.Workplane("XY")
             .polyline([(5, -3), (18, -9), (40, -10), (56, -7), (PROP_DIA / 2, 0), (56, 4), (40, 3), (18, 2), (5, 3)])
             .close().extrude(1.6))
    body = cq.Workplane("XY").circle(8).extrude(4)
    for k in range(3):
        body = body.union(blade.rotate((0, 0, 0), (0, 0, 1), toward_deg + 120 * k))
    body = body.cut(cq.Workplane("XY").circle(2.6).extrude(4))
    return body.union(cq.Workplane("XY").workplane(offset=4).circle(4).extrude(4))


def parts():
    out = [
        Part("bottom_plate", plate(BOTTOM_T, [p for sx, sy in CORNERS.values() for p in arm_bolts(sx, sy)[1:]]),
             cut="cnc", material="4 mm carbon fibre plate", color="#2d2f34"),
        Part("top_plate", plate(TOP_T, []), use=Pose(z=Z_TOP_PLATE), cut="cnc",
             material="2 mm carbon fibre plate", color="#44474e"),
    ]
    for name, (sx, sy) in CORNERS.items():
        rx, ry, heading, length = arm_frame(sx, sy)
        out.append(Part(f"arm_{name}", arm(length), use=Pose(x=rx, y=ry, z=Z_ARM, rz=heading), print=Pose(),
                        material="PETG", color="#ff9500"))
        mx, my = sx * MOTOR_X, sy * MOTOR_Y
        out.append(Part(f"motor_{name}", motor(), use=Pose(x=mx, y=my, z=Z_ARM_TOP), printed=False,
                        material="2207 brushless motor", color="#8d949c"))
        out.append(Part(f"prop_{name}", prop(180 if sx > 0 else 0), use=Pose(x=mx, y=my, z=Z_PROP), printed=False,
                        material="5.1 inch tri-blade prop", color="#e8ecef"))
        px, py = arm_bolts(sx, sy)[0]
        out.append(Part(f"standoff_{name}", cq.Workplane("XY").center(px, py).circle(2.5).extrude(STANDOFF_H),
                        use=Pose(z=Z_ARM_TOP), printed=False, material="M3 aluminium standoff", color="#c3c8cf"))
    posts = cq.Workplane("XY").pushPoints(STACK_HOLES).circle(2.5).extrude(8.0).translate((0, 0, BOTTOM_T))
    stack = (cq.Workplane("XY").box(STACK_SIZE, STACK_SIZE, 16, centered=(True, True, False))
             .translate((0, 0, BOTTOM_T + 8.0)).union(posts))
    stack = stack.cut(holes(STACK_HOLES, 3.2, 40).translate((0, 0, -2)))
    out.append(Part("flight_stack", stack, printed=False, material="FC + ESC stack", color="#1f5f4a"))
    camera = (cq.Workplane("XY").box(21, 19, 19, centered=(True, True, False)).translate((53, 0, Z_PLATE_TOP))
              .union(cq.Workplane("YZ").workplane(offset=63.5).center(0, Z_PLATE_TOP + 9.5).circle(7).extrude(5)))
    out.append(Part("camera", camera, printed=False, material="micro FPV camera", color="#15181d"))
    battery = cq.Workplane("XY").box(80, 34, 32, centered=(True, True, False)).translate((0, 0, Z_PLATE_TOP))
    out.append(Part("battery", battery, printed=False, material="6S 1100 mAh LiPo", color="#34476b"))
    return out


def axis_xy(shape):
    box = shape.BoundingBox()
    return (box.xmin + box.xmax) / 2, (box.ymin + box.ymax) / 2


def checks(ctx):
    axes = {k: axis_xy(ctx.solid(f"motor_{k}")) for k in CORNERS}
    sweep = {k: max(math.hypot(v.X - axes[k][0], v.Y - axes[k][1]) for v in ctx.solid(f"prop_{k}").Vertices())
             for k in CORNERS}
    names = list(CORNERS)
    gaps = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            spacing = math.hypot(axes[a][0] - axes[b][0], axes[a][1] - axes[b][1])
            gaps.append((spacing - sweep[a] - sweep[b], a, b, spacing))
    gap, a, b, spacing = min(gaps)
    along_x = abs(axes[a][0] - axes[b][0]) > abs(axes[a][1] - axes[b][1])
    need = (sweep[a] + sweep[b] + MIN_PROP_GAP) / 2
    ctx.expect("prop_tip_gap", gap, min=MIN_PROP_GAP, part=f"prop_{a}",
               summary=f"props {a}/{b}: motors {spacing:.1f} mm apart, sweep radii {sweep[a]:.2f} and "
                       f"{sweep[b]:.2f} mm, tip gap",
               hint=f"Props {a} and {b} are too close. Move the motors apart: set "
                    f"{'MOTOR_X' if along_x else 'MOTOR_Y'} to at least {need:.1f} mm in model.py.")

    # A spinning prop sweeps a disc, so test the disc (measured radius, measured height band), not the blade
    # positions the solid happens to freeze at.
    frame = ["top_plate", "bottom_plate", "flight_stack", "camera", "battery"]
    discs = {}
    for k in CORNERS:
        box = ctx.solid(f"prop_{k}").BoundingBox()
        discs[k] = (cq.Workplane("XY").center(*axes[k]).circle(sweep[k]).extrude(box.zmax - box.zmin)
                    .translate((0, 0, box.zmin)))
    near = min((ctx.distance(discs[k], f), k, f) for k in CORNERS for f in frame)
    ctx.expect("prop_frame_clearance", near[0], min=MIN_PROP_FRAME_GAP, part=f"prop_{near[1]}",
               summary=f"closest swept prop disc to the frame body is prop_{near[1]} to {near[2]}",
               hint=f"The swept disc of prop_{near[1]} is too close to {near[2]}. Raise the props "
                    f"(MOTOR_H, the motor height) or move {near[2]} away from that motor.")

    # M3 shank probes on the standard 16 x 16 mm motor square, turned to the arm heading measured from the solids.
    clash, worst_gap = 0.0, 0.0
    for k in CORNERS:
        arm_solid = ctx.solid(f"arm_{k}")
        ax, ay = axes[k]
        centre = arm_solid.Center()
        heading = math.atan2(ay - centre.y, ax - centre.x)
        c, s = math.cos(heading), math.sin(heading)
        for lx in (-MOTOR_PATTERN / 2, MOTOR_PATTERN / 2):
            for ly in (-MOTOR_PATTERN / 2, MOTOR_PATTERN / 2):
                x, y = ax + lx * c - ly * s, ay + lx * s + ly * c
                screw = cq.Workplane("XY").center(x, y).circle(1.5).extrude(14).translate((0, 0, Z_ARM - 2))
                clash += ctx.interference(f"arm_{k}", screw)
                worst_gap = max(worst_gap, ctx.distance(f"arm_{k}", screw))
    ctx.expect("motor_screws_free", clash, max=0.01, unit="mm3", part="arm_fl",
               hint="An M3 screw on the 16 x 16 mm motor square hits the arm: move the arm's motor holes to a "
                    "16 mm square around the motor axis.")
    ctx.expect("motor_screws_seated", worst_gap, max=0.5, part="arm_fl",
               hint="A motor screw is more than 0.5 mm from its hole wall: the hole is not on the 16 x 16 mm square.")

    # The 30.5 mm stack pattern through both plates and the board stack.
    clash, worst_gap = 0.0, 0.0
    for x, y in [(sx * 15.25, sy * 15.25) for sx in (-1, 1) for sy in (-1, 1)]:
        screw = cq.Workplane("XY").center(x, y).circle(1.5).extrude(40).translate((0, 0, -3))
        for name in ("bottom_plate", "flight_stack", "top_plate"):
            clash += ctx.interference(name, screw)
            worst_gap = max(worst_gap, ctx.distance(name, screw))
    ctx.expect("stack_screws_free", clash, max=0.01, unit="mm3", part="bottom_plate",
               hint="The 30.5 x 30.5 mm M3 stack screws hit a plate or the stack: re-centre STACK_HOLES.")
    ctx.expect("stack_screws_seated", worst_gap, max=0.5, part="bottom_plate",
               hint="A stack screw is more than 0.5 mm from a hole wall: the holes are off the 30.5 mm pattern.")

    # Effective corner radius of every cut-out, from its own outline: perimeter = 2(w+h) - (8 - 2 pi) r.
    radius = min(((2 * (w + h) - p) / (8 - 2 * math.pi), name)
                 for name, z in (("bottom_plate", BOTTOM_T / 2), ("top_plate", Z_TOP_PLATE + TOP_T / 2))
                 for loop in ctx.section(name, "z", z)["loop_list"] if loop["hole"]
                 for w, h, p in [(loop["bbox_mm"]["max_mm"][0] - loop["bbox_mm"]["min_mm"][0],
                                  loop["bbox_mm"]["max_mm"][1] - loop["bbox_mm"]["min_mm"][1],
                                  loop["perimeter_mm"])])
    ctx.expect("pocket_corner_radius", radius[0], min=TOOL_D / 2, part=radius[1],
               summary=f"smallest inside corner radius in {radius[1]} against the {TOOL_D:g} mm endmill",
               hint=f"The endmill cannot cut corners this tight. Raise POCKET_R in model.py to at least "
                    f"{TOOL_D / 2 + 0.1:.1f} mm.")
