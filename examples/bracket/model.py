"""An L-bracket for two M4 screws, printed as-is with the base on the bed."""
import cadquery as cq

from agentcad import Part

BASE_X, BASE_Y, BASE_T = 60.0, 40.0, 5.0   # base plate, mm
UPRIGHT_H, UPRIGHT_T = 40.0, 5.0           # wall that rises from the back edge
GUSSET_T = 4.0
HOLE_D = 4.4                               # M4 clearance hole
SCREW_D = 4.0                              # M4 shank, the requirement the holes are checked against
HOLES = [(-18.0, -8.0), (18.0, -8.0)]      # hole axes (x, y) on the base


def bracket() -> cq.Workplane:
    base = cq.Workplane("XY").box(BASE_X, BASE_Y, BASE_T, centered=(True, True, False))
    back = BASE_Y / 2
    upright = cq.Workplane("XY").center(0, back - UPRIGHT_T / 2).rect(BASE_X, UPRIGHT_T).extrude(UPRIGHT_H)
    # Triangular gusset on the front face of the upright, resting on the base plate.
    gusset = (cq.Workplane("YZ").polyline([(back - UPRIGHT_T, BASE_T), (back - UPRIGHT_T - 20, BASE_T),
                                           (back - UPRIGHT_T, BASE_T + 25)]).close()
              .extrude(GUSSET_T / 2, both=True))
    part = base.union(upright).union(gusset)
    holes = cq.Workplane("XY").pushPoints(HOLES).circle(HOLE_D / 2).extrude(BASE_T)
    return part.cut(holes)


def parts():
    return [Part("bracket", bracket(), material="PETG", color="#4f8cff")]


def checks(ctx):
    # A screw stand-in built from the requirement (4.0 mm shank on each hole axis), not from the hole diameter.
    for index, (x, y) in enumerate(HOLES, start=1):
        screw = cq.Workplane("XY").workplane(offset=-3).center(x, y).circle(SCREW_D / 2).extrude(12)
        ctx.expect(f"m4_clearance_{index}", ctx.distance("bracket", screw), min=0.1, max=0.4, part="bracket")
        ctx.expect(f"m4_no_overlap_{index}", ctx.interference("bracket", screw), max=0.001, part="bracket",
                   unit="mm3")
