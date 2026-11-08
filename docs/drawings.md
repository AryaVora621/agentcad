# Dimensioned drawings

`agentcad drawing` writes one dimensioned sheet per part as SVG, PDF and PNG. Every dimension is read from the built
solid (its exact bounding box and its circular edges), never from a model parameter, so a drawing cannot disagree with
the geometry it shows.

```
agentcad drawing [PROJECT|FILE ...] [-o DIR] [--parts NAME,NAME] [--format svg,pdf,png]
                 [--projection third|first] [--sheet auto|A4|A3|letter|tabloid] [--json]
```

- Input is one project folder (default `.`) or STEP files. Meshes (STL, OBJ, PLY, 3MF, GLB) are refused with exit 2:
  they have no edges to draw.
- Output goes to `PROJECT/build/drawings`, `./drawings` for STEP files, or `-o`. Files are `<part>.svg`, `.pdf`, `.png`.
- Text output is one path per file written. Unknown `--parts` names exit 2 and list the valid names; model errors exit 3.
- Python: `agentcad.drawing.make_drawings(inputs, out=None, parts=None, formats=("svg","pdf","png"),
  projection="third", sheet="auto")` returns the JSON document below and raises `ConfigError` / `BuildError`.

## Layout

Each sheet has a front, top and right view plus an isometric view without dimensions in the free corner.

| Projection | Arrangement |
|---|---|
| `third` (default, US) | top above front, right view to the right of front, isometric top right |
| `first` (ISO/EU) | top below front, right view to the left of front, isometric bottom left |

Lines come from OCCT hidden-line removal: visible sharp and outline edges are solid, visible tangent edges thin,
hidden edges thin and dashed. The isometric view omits hidden edges and is drawn at the sheet scale when it fits the
free corner, otherwise smaller and labelled "NOT TO SCALE".

**Scale.** The largest standard scale of 10:1, 5:1, 2:1, 1:1, 1:2, 1:5, 1:10, 1:20, 1:50 at which the views, their
dimension space and the title block fit inside the frame. **Sheet.** `--sheet auto` uses A4 landscape, and A3
instead only when A3 allows a strictly larger standard scale than A4. `letter` and `tabloid` are landscape too. A PNG
is the sheet at 150 dpi (A4: 1754 x 1240 px).

## What is dimensioned

- Overall width and height on the front view, depth on the top view.
- Holes: circular edges seen end-on in a view (a closed circle, or arcs that close) become diameter callouts grouped
  by size ("4x Ø3.4"), with a leader to one hole of the group and centre marks on every hole.
- Hole centres get ordinate positions from the view's lower-left datum: x values above the view, y values to its
  right, each starting from a "0" at the datum.
- With more than 8 hole centres in a view the ordinates are replaced by ids next to the holes and a table (id, X, Y,
  Ø) on the sheet.
- Arrows are closed and filled, dimension lines sit 9 mm off the view, extension lines leave a 1 mm gap, text is a
  3.5 mm font, numbers use up to 2 decimals with trailing zeros dropped. Placement is checked so text never overlaps
  a view, another text, a dimension line or the title block.

The title block (bottom right) holds part name, project, material, quantity, scale, projection, units, sheet size,
`agentcad <version>` and the first 12 hex digits of a sha256 of the part's STEP data. There are no dates, and the
same input gives byte-identical SVG, PDF and PNG files.

## JSON document

```json
{"schema": "agentcad.drawing/1", "project": "bracket", "out": "/abs/build/drawings",
 "sheets": [{"part": "bracket", "files": {"svg": "...", "pdf": "...", "png": "..."},
             "sheet": "A3", "scale": "2:1", "projection": "third",
             "views": [{"name": "front", "direction": [0, 1, 0], "scale": "2:1",
                        "bbox_mm": {"min_mm": [x, y], "max_mm": [x, y], "size_mm": [w, h]}}],
             "dimensions": [{"id": "D1", "view": "front", "kind": "linear", "role": "width",
                             "value_mm": 60.0, "count": 1, "text": "60"}]}]}
```

- `direction` is the line of sight in model axes; `bbox_mm` is the view's box on the sheet in millimetres from the
  upper-left corner, y down, like the SVG.
- `kind` is `linear` or `diameter`; `role` is `width`, `height`, `depth`, `position_x`, `position_y` or `diameter`.
  For a diameter, `count` is the number of holes of that size and `value_mm` their mean diameter.
- `hole_table` (only when a table is drawn) lists `{id, x_mm, y_mm, diameter_mm}`.
- Failures print `agentcad.error/1` with `--json`, as for every command.

## Limits

No GD&T, no tolerances, no section views, no threads or counterbore notes, and no dimensions for slots, arcs or
angles yet. Holes are found only as closed circles whose axis is parallel to a view direction. SVG text stays text
(font family DejaVu Sans), so other renderers may substitute a font.
