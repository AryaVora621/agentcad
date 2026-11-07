"""Parts and the explicit coordinate frames they are measured in."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

import cadquery as cq

_NAME = re.compile(r"[a-z0-9_-]+")


@dataclass(frozen=True)
class Pose:
    """A rigid placement: rotation in degrees about the part origin, then translation in mm."""

    x: float = 0
    y: float = 0
    z: float = 0
    rx: float = 0
    ry: float = 0
    rz: float = 0

    def location(self) -> cq.Location:
        """Rotate about X, then Y, then Z (extrinsic, about the origin), then translate."""
        # CadQuery composes locations right to left, so the rightmost factor acts first.
        rotate_x = cq.Location(cq.Vector(0, 0, 0), cq.Vector(1, 0, 0), self.rx)
        rotate_y = cq.Location(cq.Vector(0, 0, 0), cq.Vector(0, 1, 0), self.ry)
        rotate_z = cq.Location(cq.Vector(0, 0, 0), cq.Vector(0, 0, 1), self.rz)
        return cq.Location(cq.Vector(self.x, self.y, self.z)) * rotate_z * rotate_y * rotate_x

    def apply(self, shape: cq.Shape) -> cq.Shape:
        """Return a moved copy; the input shape is never mutated."""
        return shape.moved(self.location())


@dataclass
class Part:
    """A named component: its model, where it sits in the product and how it is printed."""

    name: str
    shape: cq.Workplane | cq.Shape
    use: Pose = Pose()
    print: Pose | Literal["as-is"] = "as-is"
    qty: int = 1
    material: str | None = None
    color: str | None = None
    printed: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _NAME.fullmatch(self.name):
            raise ValueError(f"Invalid part name {self.name!r}; use lowercase letters, digits, '_' and '-'")
        if isinstance(self.qty, bool) or not isinstance(self.qty, int) or self.qty < 1:
            raise ValueError(f"Part {self.name}: qty must be a positive integer")
        if not isinstance(self.use, Pose):
            raise TypeError(f"Part {self.name}: use must be a Pose")
        if self.print != "as-is" and not isinstance(self.print, Pose):
            raise ValueError(f"Part {self.name}: print must be a Pose or 'as-is'")
        if self.color is not None and not re.fullmatch(r"#[0-9a-fA-F]{6}", self.color):
            raise ValueError(f"Part {self.name}: color must look like '#rrggbb'")

    def solid(self, frame: Literal["model", "use", "print"] = "model") -> cq.Shape:
        """Return the part in a frame: its own ("model"), the assembly ("use") or on the bed ("print")."""
        from .geometry import bbox, to_shape

        shape = to_shape(self.shape)
        if frame == "model":
            return shape
        if frame == "use":
            return self.use.apply(shape)
        if frame != "print":
            raise ValueError(f"Unknown frame {frame!r}; use 'model', 'use' or 'print'")
        if isinstance(self.print, Pose):
            # Translation is ignored on purpose: the bed placement below is always derived.
            shape = Pose(rx=self.print.rx, ry=self.print.ry, rz=self.print.rz).apply(shape)
        low, high = bbox(shape)["min_mm"], bbox(shape)["max_mm"]
        return shape.moved(cq.Location(cq.Vector(-(low[0] + high[0]) / 2, -(low[1] + high[1]) / 2, -low[2])))
