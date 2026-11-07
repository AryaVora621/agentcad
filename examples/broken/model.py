"""NEGATIVE FIXTURE. Every gate in this project fails on purpose; do not copy it as a starting point.

It exists so the test suite can prove that each gate can fail:
  left, right  overlap by 10 x 20 x 20 mm in the use pose        -> interference FAIL
  tee          20 x 20 mm flat ceiling 25 mm above the bed        -> overhang FAIL
  long_bar     300 mm long, the generic-220 bed is 220 mm         -> bed_fit FAIL
  thin_cup     0.6 mm walls with checks.min_wall_mm = 1.2         -> min_wall FAIL
  checks()     one expectation that fails
  check_boom   raises, which must be reported as ERROR while the run continues
  check_after  runs after the raise and passes, proving the run continued
"""
import cadquery as cq

from agentcad import Part, Pose


def cube(size: float) -> cq.Workplane:
    return cq.Workplane("XY").box(size, size, size, centered=(True, True, False))


def tee() -> cq.Workplane:
    stem = cq.Workplane("XY").box(10, 10, 25, centered=(True, True, False))
    head = cq.Workplane("XY").workplane(offset=25).box(20, 20, 4, centered=(True, True, False))
    return stem.union(head)


def thin_cup() -> cq.Workplane:
    return cq.Workplane("XY").box(30, 30, 20, centered=(True, True, False)).faces(">Z").shell(-0.6)


def parts():
    return [
        Part("left", cube(20)),
        Part("right", cube(20), use=Pose(x=10)),
        Part("tee", tee(), use=Pose(y=40)),
        Part("long_bar", cq.Workplane("XY").box(300, 10, 5, centered=(True, True, False)), use=Pose(y=-40)),
        Part("thin_cup", thin_cup(), use=Pose(y=80)),
    ]


def checks(ctx):
    ctx.expect("left_right_gap", ctx.distance("left", "right"), min=2.0, part="left+right")


def check_boom(ctx):
    raise RuntimeError("deliberate failure to prove that a raising check becomes ERROR")


def check_after(ctx):
    cut = ctx.section("tee", "z", 27.0)
    ctx.expect("tee_head_area", cut["area_mm2"], min=399, max=401, part="tee", unit="mm2")
