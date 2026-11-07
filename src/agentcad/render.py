"""Offscreen VTK rendering for agentcad.

Produces multi-view PNG sheets with labels, scale bars, and optional section cuts.
"""

import os

import numpy as np
import trimesh
import vtk
from vtk.util import numpy_support

DEFAULT_PALETTE = [
    "#38bdf8",  # sky blue
    "#fb923c",  # orange
    "#4ade80",  # green
    "#f472b6",  # pink
    "#a78bfa",  # violet
    "#facc15",  # yellow
    "#2dd4bf",  # teal
    "#fb7185",  # rose
    "#60a5fa",  # blue
    "#34d399",  # emerald
    "#f87171",  # red
    "#c084fc",  # purple
]


def _hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    """Convert #rrggbb to (r, g, b) in [0, 1]."""
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i : i + 2], 16) / 255.0 for i in (0, 2, 4))


def _darker(color: tuple[float, float, float] | str, factor: float = 0.4) -> tuple[float, float, float]:
    """Return a darker shade of the color for feature edges."""
    if isinstance(color, str):
        r, g, b = _hex_to_rgb(color)
    else:
        r, g, b = color
    return (r * factor, g * factor, b * factor)


def _trimesh_to_polydata(mesh: trimesh.Trimesh) -> vtk.vtkPolyData:
    """Convert a trimesh to vtkPolyData using numpy_support (no temp files)."""
    pd = vtk.vtkPolyData()

    # Points
    points = vtk.vtkPoints()
    points.SetData(numpy_support.numpy_to_vtk(mesh.vertices.astype(np.float64), deep=True))
    pd.SetPoints(points)

    # Triangles
    cells = vtk.vtkCellArray()
    # Prepend 3 to each triangle for VTK format
    faces_with_count = np.hstack([np.full((len(mesh.faces), 1), 3, dtype=np.int64), mesh.faces]).ravel()
    cells.SetCells(len(mesh.faces), numpy_support.numpy_to_vtkIdTypeArray(faces_with_count, deep=True))
    pd.SetPolys(cells)

    return pd


def _make_actor(polydata: vtk.vtkPolyData, color: tuple[float, float, float], opacity: float = 1.0) -> vtk.vtkActor:
    """Create a VTK actor with normals, color, and optional feature edges."""
    # Compute normals with feature angle for sharp edges
    normals = vtk.vtkPolyDataNormals()
    normals.SetInputData(polydata)
    normals.SetFeatureAngle(35.0)
    normals.SplittingOn()
    normals.Update()

    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputConnection(normals.GetOutputPort())
    mapper.ScalarVisibilityOff()

    actor = vtk.vtkActor()
    actor.SetMapper(mapper)
    prop = actor.GetProperty()
    prop.SetColor(*color)
    prop.SetOpacity(opacity)
    prop.SetAmbient(0.24)
    prop.SetDiffuse(0.72)
    prop.SetSpecular(0.22)
    prop.SetSpecularPower(18.0)

    return actor


def _make_edge_actor(polydata: vtk.vtkPolyData, color: tuple[float, float, float]) -> vtk.vtkActor:
    """Create a feature-edge actor for sharp edge lines."""
    edges = vtk.vtkFeatureEdges()
    edges.SetInputData(polydata)
    edges.BoundaryEdgesOff()
    edges.ManifoldEdgesOff()
    edges.NonManifoldEdgesOff()
    edges.FeatureEdgesOn()
    edges.SetFeatureAngle(40.0)

    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputConnection(edges.GetOutputPort())
    mapper.ScalarVisibilityOff()

    actor = vtk.vtkActor()
    actor.SetMapper(mapper)
    prop = actor.GetProperty()
    prop.SetColor(*color)
    prop.SetLineWidth(1.5)
    prop.LightingOff()

    return actor


def _apply_section(polydata: vtk.vtkPolyData, axis: str, value: float) -> vtk.vtkPolyData:
    """Cut mesh with a plane and cap the cut faces using vtkClipClosedSurface."""
    plane = vtk.vtkPlane()
    # VTK keeps the side the normal points to, so point it away from the cameras
    # (iso +X -Y +Z, right +X, front -Y, top +Z) to leave the cut face visible.
    if axis == "x":
        plane.SetOrigin(value, 0, 0)
        plane.SetNormal(-1, 0, 0)
    elif axis == "y":
        plane.SetOrigin(0, value, 0)
        plane.SetNormal(0, 1, 0)
    else:  # z
        plane.SetOrigin(0, 0, value)
        plane.SetNormal(0, 0, -1)

    planes = vtk.vtkPlaneCollection()
    planes.AddItem(plane)

    clip = vtk.vtkClipClosedSurface()
    clip.SetInputData(polydata)
    clip.SetClippingPlanes(planes)
    clip.Update()

    return clip.GetOutput()


def _build_renderer(
    actors: list[vtk.vtkActor],
    background: tuple[float, float, float] = (0.97, 0.97, 0.98),
) -> tuple[vtk.vtkRenderer, vtk.vtkRenderWindow]:
    """Create a renderer and offscreen render window with light kit."""
    ren = vtk.vtkRenderer()
    ren.SetBackground(*background)

    for a in actors:
        ren.AddActor(a)

    # Light kit for consistent lighting
    kit = vtk.vtkLightKit()
    kit.SetKeyLightIntensity(0.85)
    kit.SetKeyToFillRatio(2.2)
    kit.AddLightsToRenderer(ren)

    win = vtk.vtkRenderWindow()
    win.SetOffScreenRendering(1)
    win.SetMultiSamples(8)
    win.AddRenderer(ren)

    return ren, win


def _camera_for_view(
    ren: vtk.vtkRenderer,
    view: str,
    bounds: tuple[float, float, float, float, float, float],
) -> vtk.vtkCamera:
    """Configure camera for the given view. Returns the camera."""
    cam = ren.GetActiveCamera()
    cam.SetViewUp(0, 0, 1)

    cx = (bounds[0] + bounds[1]) * 0.5
    cy = (bounds[2] + bounds[3]) * 0.5
    cz = (bounds[4] + bounds[5]) * 0.5
    cam.SetFocalPoint(cx, cy, cz)

    max_dim = max(bounds[1] - bounds[0], bounds[3] - bounds[2], bounds[5] - bounds[4])
    if max_dim == 0:
        max_dim = 1.0

    if view == "iso":
        # Perspective from (+1, -1, +0.8) direction
        cam.SetPosition(cx + max_dim * 1.5, cy - max_dim * 1.5, cz + max_dim * 1.2)
        cam.SetViewAngle(18)
        cam.ParallelProjectionOff()
    elif view == "front":
        # Look along +Y (camera at -Y)
        cam.SetPosition(cx, cy - max_dim * 2.5, cz)
        cam.ParallelProjectionOn()
    elif view == "right":
        # Look along -X (camera at +X)
        cam.SetPosition(cx + max_dim * 2.5, cy, cz)
        cam.ParallelProjectionOn()
    elif view == "top":
        # Look down -Z
        cam.SetPosition(cx, cy, cz + max_dim * 2.5)
        cam.ParallelProjectionOn()
        cam.SetViewUp(0, 1, 0)  # Y up for top view
    else:
        raise ValueError(f"Unknown view: {view}")

    ren.ResetCamera()

    if view in ("front", "right", "top"):
        # Zoom a bit for orthographic views to add margin
        cam.Zoom(1.2)

    return cam


TOP_BAND_PX = 140  # view name, title, size and section lines
BOTTOM_BAND_PX = 90  # scale bar and its label
SIDE_MARGIN_PX = 40


def _fit_to_band(ren: vtk.vtkRenderer, win: vtk.vtkRenderWindow) -> None:
    """Zoom and pan the camera so the model stays between the label bands."""
    w, h = win.GetSize()
    b = ren.ComputeVisiblePropBounds()
    cam = ren.GetActiveCamera()
    cam.SetWindowCenter(0.0, 0.0)
    win.Render()  # the projection needs the aspect ratio of a rendered window

    def project() -> tuple[float, float, float, float]:
        xs, ys = [], []
        for x in (b[0], b[1]):
            for y in (b[2], b[3]):
                for z in (b[4], b[5]):
                    ren.SetWorldPoint(x, y, z, 1.0)
                    ren.WorldToDisplay()
                    d = ren.GetDisplayPoint()
                    xs.append(d[0])
                    ys.append(d[1])
        return min(xs), max(xs), min(ys), max(ys)

    x0, x1, y0, y1 = project()
    avail_w = w - 2 * SIDE_MARGIN_PX
    avail_h = h - TOP_BAND_PX - BOTTOM_BAND_PX
    zoom = 0.95 * min(avail_w / max(x1 - x0, 1e-6), avail_h / max(y1 - y0, 1e-6))
    cam.Zoom(zoom)
    win.Render()

    # Zoom scales about the window centre, so measure again and pan the rest.
    x0, x1, y0, y1 = project()
    dx = w / 2.0 - (x0 + x1) / 2.0
    dy = (BOTTOM_BAND_PX + (h - TOP_BAND_PX)) / 2.0 - (y0 + y1) / 2.0
    cam.SetWindowCenter(-2.0 * dx / w, -2.0 * dy / h)


def _section_line(section: tuple[str, float] | None) -> str | None:
    """Extra label line naming the cut plane, or None without a section."""
    if not section:
        return None
    axis, value = section
    return f"section {axis}={value:.1f} mm"


def _label_lines(
    view_name: str,
    title: str | None,
    overall_size: tuple[float, float, float],
    section: tuple[str, float] | None,
) -> list[tuple[str, int, bool]]:
    """Text lines (text, font size, bold) stacked top-down under the view name."""
    lines = [(view_name, 32, True)]
    if title:
        lines.append((title, 22, False))
    lines.append((f"{overall_size[0]:.1f} x {overall_size[1]:.1f} x {overall_size[2]:.1f} mm", 18, False))
    sec = _section_line(section)
    if sec:
        lines.append((sec, 18, False))
    return lines


def _add_labels(
    ren: vtk.vtkRenderer,
    view_name: str,
    title: str | None,
    overall_size: tuple[float, float, float],
    cam: vtk.vtkCamera,
    win: vtk.vtkRenderWindow,
    section: tuple[str, float] | None = None,
) -> None:
    """Add view name, title, overall size, and scale bar (for ortho views)."""
    w = win.GetSize()[0]
    h = win.GetSize()[1]

    def add_text(text: str, x: int, y: int, size: int, bold: bool, color: tuple[float, float, float] = (0.07, 0.08, 0.1)) -> None:
        t = vtk.vtkTextActor()
        t.SetInput(text)
        t.SetDisplayPosition(x, y)
        tp = t.GetTextProperty()
        tp.SetFontFamilyToArial()
        tp.SetFontSize(size)
        tp.SetBold(bold)
        tp.SetColor(*color)
        ren.AddActor2D(t)

    # Top-left stack; every line must end above TOP_BAND_PX.
    y = h - 60
    for i, (text, size, bold) in enumerate(_label_lines(view_name, title, overall_size, section)):
        add_text(text, 40, y, size, bold)
        y -= 30 if i == 0 else 24

    # Scale bar for orthographic views
    if cam.GetParallelProjection():
        ps = cam.GetParallelScale()  # half-height in world units
        # Target bar length: ~1/5 of visible width
        visible_width = 2.0 * ps * (w / h)
        target = visible_width / 5.0
        # Round to 1, 2, 5 x 10^n
        exp = int(np.floor(np.log10(target))) if target > 0 else 0
        for base in (1, 2, 5):
            length = base * (10**exp)
            if length >= target * 0.8:
                break
        # Bar in pixels
        px = length * h / (2.0 * ps)  # ps is half the HEIGHT in world units
        x0 = w - 80 - px
        y0 = 60

        # Draw bar and ticks using 2D actors
        pts = vtk.vtkPoints()
        # Main bar
        pts.InsertNextPoint(x0, y0, 0)
        pts.InsertNextPoint(x0 + px, y0, 0)
        # Left tick
        pts.InsertNextPoint(x0, y0 - 8, 0)
        pts.InsertNextPoint(x0, y0 + 8, 0)
        # Right tick
        pts.InsertNextPoint(x0 + px, y0 - 8, 0)
        pts.InsertNextPoint(x0 + px, y0 + 8, 0)

        lines = vtk.vtkCellArray()
        for a, b in ((0, 1), (2, 3), (4, 5)):
            lines.InsertNextCell(2)
            lines.InsertCellPoint(a)
            lines.InsertCellPoint(b)

        pd = vtk.vtkPolyData()
        pd.SetPoints(pts)
        pd.SetLines(lines)

        mapper = vtk.vtkPolyDataMapper2D()
        mapper.SetInputData(pd)

        actor2d = vtk.vtkActor2D()
        actor2d.SetMapper(mapper)
        actor2d.GetProperty().SetColor(0.07, 0.08, 0.1)
        actor2d.GetProperty().SetLineWidth(2.5)
        ren.AddActor2D(actor2d)

        # Label
        add_text(f"{length:.0f} mm", int(x0 + px / 2 - 30), y0 + 18, 18, False)


def _render_view(
    actors: list[vtk.vtkActor],
    view: str,
    size: tuple[int, int],
    title: str | None,
    overall_size: tuple[float, float, float],
    section: tuple[str, float] | None = None,
) -> vtk.vtkImageData:
    """Render a single view and return the image data."""
    ren, win = _build_renderer(actors)
    win.SetSize(*size)

    cam = _camera_for_view(ren, view, ren.ComputeVisiblePropBounds())
    _fit_to_band(ren, win)
    ren.ResetCameraClippingRange()
    win.Render()

    _add_labels(ren, view, title, overall_size, cam, win, section)
    win.Render()

    grab = vtk.vtkWindowToImageFilter()
    grab.SetInput(win)
    grab.ReadFrontBufferOff()
    grab.Update()

    win.Finalize()
    return grab.GetOutput()


def _write_png(image: vtk.vtkImageData, path: str) -> None:
    """Write a vtkImageData to PNG."""
    writer = vtk.vtkPNGWriter()
    writer.SetFileName(path)
    writer.SetInputData(image)
    writer.Write()


def _compose_sheet(images: list[vtk.vtkImageData], out_path: str) -> None:
    """Compose multiple view images into a 2x2 grid (or row if < 4)."""
    n = len(images)
    if n == 0:
        return

    # All images should have same dimensions
    dims = images[0].GetDimensions()
    w, h = dims[0], dims[1]

    if n <= 2:
        # Single row
        grid_w, grid_h = n, 1
    else:
        # 2x2 grid
        grid_w, grid_h = 2, 2

    append = vtk.vtkImageAppend()
    append.SetAppendAxis(1)  # Append along Y first (rows)

    # Build rows
    row_images = []
    for row in range(grid_h):
        row_append = vtk.vtkImageAppend()
        row_append.SetAppendAxis(0)  # Append along X (columns)
        for col in range(grid_w):
            idx = row * grid_w + col
            if idx < n:
                row_append.AddInputData(images[idx])
            else:
                # Blank image
                blank = vtk.vtkImageData()
                blank.SetDimensions(w, h, 1)
                blank.AllocateScalars(vtk.VTK_UNSIGNED_CHAR, 4)
                blank.GetPointData().GetScalars().Fill(255)
                row_append.AddInputData(blank)
        row_append.Update()
        row_images.append(row_append.GetOutput())

    # VTK image origin is bottom-left: append rows in reverse so row 0 reads first.
    for img in reversed(row_images):
        append.AddInputData(img)
    append.Update()

    _write_png(append.GetOutput(), out_path)


def offscreen_ok() -> tuple[bool, str]:
    """Test offscreen VTK rendering with a tiny 64x64 scene."""
    try:
        # Create a simple sphere
        sphere = vtk.vtkSphereSource()
        sphere.SetRadius(1.0)
        sphere.SetCenter(0, 0, 0)
        sphere.SetThetaResolution(16)
        sphere.SetPhiResolution(16)
        sphere.Update()

        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(sphere.GetOutputPort())

        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(1, 0, 0)

        ren = vtk.vtkRenderer()
        ren.SetBackground(0.1, 0.1, 0.1)
        ren.AddActor(actor)

        win = vtk.vtkRenderWindow()
        win.SetOffScreenRendering(1)
        win.SetSize(64, 64)
        win.AddRenderer(ren)
        win.Render()

        grab = vtk.vtkWindowToImageFilter()
        grab.SetInput(win)
        grab.ReadFrontBufferOff()
        grab.Update()

        img = grab.GetOutput()
        if img.GetDimensions() != (64, 64, 1):
            return False, "Wrong image dimensions"

        win.Finalize()

        vtk_version = vtk.vtkVersion().GetVTKVersion()
        return True, f"VTK {vtk_version} offscreen OK"
    except (OSError, RuntimeError, ValueError) as e:
        return False, f"Offscreen test failed: {e}"


def render_sheet(
    items: list[tuple[str, "trimesh.Trimesh", str | None]],
    out_dir: str,
    *,
    views: tuple[str, ...] = ("iso", "front", "right", "top"),
    size: tuple[int, int] = (1600, 1200),
    section: tuple[str, float] | None = None,
    title: str | None = None,
) -> list[str]:
    """Render multi-view sheet PNGs.

    Args:
        items: List of (name, mesh in mm, "#rrggbb" or None).
        out_dir: Output directory (created if needed).
        views: Views to render. Default: iso, front, right, top.
        size: Per-view image size (width, height).
        section: Optional (axis, value) for section cut, e.g. ("z", 10.0).
        title: Optional title shown on each view.

    Returns:
        List of output paths, with sheet.png last.
    """
    os.makedirs(out_dir, exist_ok=True)

    # Build actors for all items
    all_actors: list[vtk.vtkActor] = []
    all_bounds = [float("inf"), -float("inf"), float("inf"), -float("inf"), float("inf"), -float("inf")]

    for idx, (name, mesh, color_hex) in enumerate(items):
        # Apply section cut if requested
        polydata = _trimesh_to_polydata(mesh)
        if section:
            axis, value = section
            polydata = _apply_section(polydata, axis, value)

        # Determine color
        if color_hex:
            color = _hex_to_rgb(color_hex)
        else:
            color = _hex_to_rgb(DEFAULT_PALETTE[idx % len(DEFAULT_PALETTE)])

        # Main actor
        actor = _make_actor(polydata, color)
        all_actors.append(actor)

        # Feature edges in darker shade
        edge_actor = _make_edge_actor(polydata, _darker(color))
        all_actors.append(edge_actor)

        # Update overall bounds
        b = polydata.GetBounds()
        all_bounds[0] = min(all_bounds[0], b[0])
        all_bounds[1] = max(all_bounds[1], b[1])
        all_bounds[2] = min(all_bounds[2], b[2])
        all_bounds[3] = max(all_bounds[3], b[3])
        all_bounds[4] = min(all_bounds[4], b[4])
        all_bounds[5] = max(all_bounds[5], b[5])

    overall_size = (
        all_bounds[1] - all_bounds[0],
        all_bounds[3] - all_bounds[2],
        all_bounds[5] - all_bounds[4],
    )

    # Render each view
    view_images = []
    output_paths = []

    for view in views:
        img = _render_view(all_actors, view, size, title, overall_size, section)
        view_images.append(img)
        path = os.path.join(out_dir, f"{view}.png")
        _write_png(img, path)
        output_paths.append(path)

    # Compose sheet
    sheet_path = os.path.join(out_dir, "sheet.png")
    _compose_sheet(view_images, sheet_path)
    output_paths.append(sheet_path)

    return output_paths