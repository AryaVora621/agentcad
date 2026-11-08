"""A laser-cut 3 mm plywood panel with a small 3D-printed bracket that screws to it."""
import cadquery as cq
from agentcad import Part, Pose

PANEL_W, PANEL_H, PANEL_T = 200.0, 120.0, 3.0
CORNER_R = 6.0
HOLE_D = 3.2  # M3 clearance
HOLE_INSET = 10.0
SLOT_L, SLOT_W = 60.0, 6.0

# The bracket's foot sits on the panel with its screw hole over one of the panel holes.
HOLES = [(-PANEL_W / 2 + HOLE_INSET, -PANEL_H / 2 + HOLE_INSET), (PANEL_W / 2 - HOLE_INSET, -PANEL_H / 2 + HOLE_INSET),
         (-PANEL_W / 2 + HOLE_INSET, PANEL_H / 2 - HOLE_INSET), (PANEL_W / 2 - HOLE_INSET, PANEL_H / 2 - HOLE_INSET)]
FOOT = HOLES[1]


def panel() -> cq.Workplane:
    plate = (cq.Workplane("XY").box(PANEL_W, PANEL_H, PANEL_T, centered=(True, True, False))
             .edges("|Z").fillet(CORNER_R))
    plate = plate.faces(">Z").workplane().pushPoints(HOLES).hole(HOLE_D)
    # A through slot for a cable; a cutter cannot make anything but straight through-cuts.
    return plate.cut(cq.Workplane("XY").slot2D(SLOT_L, SLOT_W).extrude(PANEL_T * 3, both=True))


def bracket() -> cq.Workplane:
    foot = cq.Workplane("XY").box(20, 20, 4, centered=(True, True, False))
    foot = foot.faces(">Z").workplane().hole(HOLE_D)
    upright = cq.Workplane("XY").box(20, 4, 18, centered=(True, True, False)).translate((0, -8, 4))
    return foot.union(upright)


def parts():
    return [
        Part("panel", panel(), cut="laser", material="3 mm birch plywood", color="#c8a165"),
        Part("bracket", bracket(), use=Pose(x=FOOT[0], y=FOOT[1], z=PANEL_T), material="PETG", color="#2b6cb0"),
    ]
