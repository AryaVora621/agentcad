"""An open box and a lid whose lip drops inside the box with a measured side clearance."""
import cadquery as cq

from agentcad import Part, Pose

BOX_X, BOX_Y, BOX_Z = 60.0, 40.0, 30.0   # outer size, mm
WALL = 2.0                               # box walls and floor
PLATE_T = 2.0                            # lid plate
LIP_H = 6.0                              # how far the lip drops into the box
CLEARANCE = 0.25                         # gap per side between lip and box wall


def box() -> cq.Workplane:
    return (cq.Workplane("XY").box(BOX_X, BOX_Y, BOX_Z, centered=(True, True, False))
            .faces(">Z").shell(-WALL))


def lid() -> cq.Workplane:
    """Plate on top (z 0..PLATE_T) with a hollow lip below it (z -LIP_H..0)."""
    plate = cq.Workplane("XY").box(BOX_X, BOX_Y, PLATE_T, centered=(True, True, False))
    lip_x, lip_y = BOX_X - 2 * WALL - 2 * CLEARANCE, BOX_Y - 2 * WALL - 2 * CLEARANCE
    lip = (cq.Workplane("XY").workplane(offset=-LIP_H).rect(lip_x, lip_y).rect(lip_x - 2 * WALL, lip_y - 2 * WALL)
           .extrude(LIP_H))
    return plate.union(lip)


def parts():
    return [
        Part("box", box(), material="PETG", color="#4f8cff"),
        # Resting on the rim in use; flipped for printing so the plate lies on the bed and the lip points up.
        Part("lid", lid(), use=Pose(z=BOX_Z), print=Pose(rx=180), material="PETG", color="#f5a623"),
    ]


def checks(ctx):
    lift = ctx.slide("lid", "box", (0, 0, 1), 20, steps=20)
    ctx.expect("lid_lifts_free", max(step["overlap_mm3"] for step in lift), max=0.001, part="lid", unit="mm3")
    for axis, direction in (("x", (1, 0, 0)), ("y", (0, 1, 0))):
        play = ctx.play("lid", "box", direction, max_mm=2.0)
        # A lid that never touches within 2 mm is far too loose: report the cap so the limit fails.
        ctx.expect(f"lid_play_{axis}", 2.0 if play is None else play, min=0.15, max=0.40, part="lid")
