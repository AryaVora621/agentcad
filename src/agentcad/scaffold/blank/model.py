"""A starting point: one 20 mm cube and a check that measures it. Replace both with your part."""
import cadquery as cq

from agentcad import Part

SIZE = 20.0  # mm


def parts():
    cube = cq.Workplane("XY").box(SIZE, SIZE, SIZE, centered=(True, True, False))
    return [Part("cube", cube, material="PLA")]


def checks(ctx):
    # Write each requirement as a measurement of the built solid, never as a comparison of parameters.
    height = ctx.solid("cube", "print").BoundingBox().zlen
    ctx.expect("cube_height", height, min=19.9, max=20.1, part="cube")
