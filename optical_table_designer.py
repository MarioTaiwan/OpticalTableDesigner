"""Optical Table Designer v0.3, native FreeCAD UI.

Target: FreeCAD 1.0/1.1, with optional chbergmann/OpticsWorkbench.
All coordinates are in millimetres. User metadata is stored as document properties.
This is intentionally a lightweight placement tool, not a joint/assembly solver.
"""

import math
import os
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

V = App.Vector
_USER_ROLE = getattr(QtCore.Qt, "UserRole", None)
if _USER_ROLE is None:
    _USER_ROLE = QtCore.Qt.ItemDataRole.UserRole
_PANEL = None


def say(message):
    App.Console.PrintMessage("[Optical Table] " + str(message) + "\n")


def _property(obj, ptype, name, group="Optical Table", tooltip=""):
    if name not in obj.PropertiesList:
        obj.addProperty(ptype, name, group, tooltip)


def _getdoc():
    if not App.ActiveDocument:
        App.newDocument("OpticalTable")
    return App.ActiveDocument


def _is_shape(obj):
    return hasattr(obj, "Shape") and hasattr(obj, "Placement")


def _tag_component(obj):
    _property(obj, "App::PropertyBool", "OTD_Smart", tooltip="Annotated optical/mechanical part")
    _property(obj, "App::PropertyString", "OTD_Type", tooltip="Mirror, lens, mount, etc")
    _property(obj, "App::PropertyPlacement", "OTD_Anchor", tooltip="Mount reference relative to part")
    _property(obj, "App::PropertyBool", "OTD_HasOptics", tooltip="Has optical reference geometry")
    _property(obj, "App::PropertyVector", "OTD_OpticCenter", tooltip="Optical center in part coordinates")
    _property(obj, "App::PropertyVector", "OTD_OpticNormal", tooltip="Optical direction in part coordinates")
    _property(obj, "App::PropertyLength", "OTD_OpticDiameter", tooltip="Nominal optical aperture")
    _property(obj, "App::PropertyLink", "OTD_OpticProxy", tooltip="Independent optical geometry")
    _property(obj, "App::PropertyLink", "OTD_AttachedTo", tooltip="Mounting surface used for placement")
    _property(obj, "App::PropertyInteger", "OTD_HoleI", tooltip="Mounting grid column")
    _property(obj, "App::PropertyInteger", "OTD_HoleJ", tooltip="Mounting grid row")
    _property(obj, "App::PropertyDistance", "OTD_Lift", tooltip="Mount anchor elevation along surface normal")
    _property(obj, "App::PropertyDistance", "OTD_OffsetX", tooltip="Fine X offset from nominal grid hole")
    _property(obj, "App::PropertyDistance", "OTD_OffsetY", tooltip="Fine Y offset from nominal grid hole")
    _property(obj, "App::PropertyAngle", "OTD_Angle", tooltip="UI yaw adjustment; full FreeCAD rotation is retained")
    _property(obj, "App::PropertyPlacement", "OTD_LastGridWorld", tooltip="Last grid frame for relative board movement")
    _property(obj, "App::PropertyBool", "OTD_PoseValid", tooltip="A mounting-grid frame has been recorded")
    _property(obj, "App::PropertyLink", "OTD_AnchorMarker", tooltip="Optional local mounting-anchor axes")
    obj.OTD_Smart = True


def _tag_surface(obj):
    _property(obj, "App::PropertyBool", "OTD_IsMountSurface", tooltip="Offers selectable mounting grid")
    _property(obj, "App::PropertyPlacement", "OTD_GridFrame", tooltip="First hole frame, local to part")
    _property(obj, "App::PropertyLength", "OTD_PitchX", tooltip="Hole pitch along local X")
    _property(obj, "App::PropertyLength", "OTD_PitchY", tooltip="Hole pitch along local Y")
    _property(obj, "App::PropertyInteger", "OTD_Columns", tooltip="Number of hole columns")
    _property(obj, "App::PropertyInteger", "OTD_Rows", tooltip="Number of hole rows")
    _property(obj, "App::PropertyLink", "OTD_Markers", tooltip="Optional visual hole-center markers")
    obj.OTD_IsMountSurface = True


def surfaces(doc):
    return [o for o in doc.Objects if getattr(o, "OTD_IsMountSurface", False) and _is_shape(o)]


def components(doc):
    return [o for o in doc.Objects if getattr(o, "OTD_Smart", False) and _is_shape(o)]


def active_pick():
    """Return (object, subshape, picked position) using current scene selection."""
    sels = Gui.Selection.getSelectionEx()
    for sel in sels:
        if sel.SubObjects:
            p = sel.PickedPoints[0] if sel.PickedPoints else None
            return sel.Object, sel.SubObjects[0], p
    raise ValueError("Click a face or circular edge in the 3D model first.")


def plane_normal(face):
    if face.ShapeType != "Face" or not isinstance(face.Surface, Part.Plane):
        raise ValueError("Select a planar face, not a curved surface or edge.")
    u0, u1, v0, v1 = face.ParameterRange
    n = face.normalAt((u0 + u1) / 2.0, (v0 + v1) / 2.0)
    if n.Length < 1e-8:
        raise ValueError("Face has no usable normal.")
    n.normalize()
    return n


def _optical_frame_from_face(face, owner):
    center = face.CenterOfMass
    normal = plane_normal(face)
    local_inverse = owner.Placement.inverse()
    local_center = local_inverse.multVec(center)
    local_normal = local_inverse.Rotation.multVec(normal)
    local_normal.normalize()
    return local_center, local_normal


def _grid_from_face(face, owner, pitchx, pitchy):
    """Find rectangular face-local bounds; first grid point at half pitch from edges.

    For nonrectangular surfaces this is only a starting suggestion; the user
    must verify that the chosen hole locations actually exist in the STEP model.
    """
    normal = plane_normal(face)
    if normal.dot(V(0, 0, 1)) < 0:
        normal = -normal
    orientation = App.Rotation(V(0, 0, 1), normal)
    center = face.CenterOfMass
    inv_rot = orientation.inverted()
    rel = [inv_rot.multVec(v.Point - center) for v in face.Vertexes]
    if not rel:
        raise ValueError("Face has no vertices to define a grid extent.")
    xmin = min(p.x for p in rel)
    xmax = max(p.x for p in rel)
    ymin = min(p.y for p in rel)
    ymax = max(p.y for p in rel)
    width = xmax - xmin
    depth = ymax - ymin
    if width < pitchx / 2 or depth < pitchy / 2:
        raise ValueError("The selected face is too small for this hole pitch.")
    cols = max(1, int(math.floor(width / pitchx + 1e-6)))
    rows = max(1, int(math.floor(depth / pitchy + 1e-6)))
    world_first = center + orientation.multVec(
        V(xmin + pitchx / 2.0, ymin + pitchy / 2.0, 0)
    )
    world_frame = App.Placement(world_first, orientation)
    local_frame = owner.Placement.inverse().multiply(world_frame)
    return local_frame, cols, rows


def register_surface(obj, face, pitchx, pitchy):
    frame, cols, rows = _grid_from_face(face, obj, pitchx, pitchy)
    _tag_surface(obj)
    obj.OTD_GridFrame = frame
    obj.OTD_PitchX = pitchx
    obj.OTD_PitchY = pitchy
    obj.OTD_Columns = cols
    obj.OTD_Rows = rows
    return cols, rows



def set_grid_first_hole_from_edge(surface, edge):
    """Align the grid origin to the actual center of a selected circular hole.

    Keep the grid plane's height and orientation. Only its in-plane origin moves.
    """
    if edge.ShapeType != "Edge" or not isinstance(edge.Curve, Part.Circle):
        raise ValueError("Select a circular edge around the first mounting hole.")
    if not getattr(surface, "OTD_IsMountSurface", False):
        raise ValueError("Register this part as a mounting surface first.")
    global_frame = surface.Placement.multiply(surface.OTD_GridFrame)
    hole_local = global_frame.inverse().multVec(edge.Curve.Center)
    new_base = global_frame.multVec(V(hole_local.x, hole_local.y, 0))
    new_world = App.Placement(new_base, global_frame.Rotation)
    surface.OTD_GridFrame = surface.Placement.inverse().multiply(new_world)
    return new_base


def edit_grid(surface, pitchx, pitchy, cols, rows):
    if min(pitchx, pitchy) <= 0 or min(cols, rows) < 1:
        raise ValueError("Hole pitch and grid counts must be positive.")
    surface.OTD_PitchX = pitchx
    surface.OTD_PitchY = pitchy
    surface.OTD_Columns = cols
    surface.OTD_Rows = rows


def choose_top_face(obj):
    candidates = []
    for face in obj.Shape.Faces:
        if not isinstance(face.Surface, Part.Plane):
            continue
        normal = plane_normal(face)
        if normal.dot(V(0, 0, 1)) > 0.8:
            candidates.append((face.CenterOfMass.z, face.Area, face))
    if not candidates:
        raise ValueError("No upward-facing planar face found. Select one manually.")
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates[0][2]


def make_grid_markers(surface):
    doc = surface.Document
    prev = getattr(surface, "OTD_Markers", None)
    if prev and prev in doc.Objects:
        doc.removeObject(prev.Name)
    px, py = surface.OTD_PitchX.Value, surface.OTD_PitchY.Value
    cols, rows = int(surface.OTD_Columns), int(surface.OTD_Rows)
    if cols * rows > 2500:
        raise ValueError("Grid too large (>2500 markers). Reduce the hole counts.")
    circles = []
    for i in range(cols):
        for j in range(rows):
            circles.append(Part.makeCircle(min(px, py) * 0.09,
                                          V(i * px, j * py, 0.18), V(0, 0, 1)))
    markers = doc.addObject("Part::Feature", "HoleCenters")
    markers.Label = surface.Label + " | hole centers (display only)"
    markers.Shape = Part.makeCompound(circles)
    markers.Placement = surface.Placement.multiply(surface.OTD_GridFrame)
    markers.ViewObject.LineColor = (0.08, 0.16, 0.24)
    markers.ViewObject.LineWidth = 1.2
    if hasattr(markers.ViewObject, "Selectable"):
        markers.ViewObject.Selectable = False
    surface.OTD_Markers = markers
    doc.recompute()
    return markers


def _refresh_children(doc):
    for surface in surfaces(doc):
        marker = getattr(surface, "OTD_Markers", None)
        if marker:
            marker.Placement = surface.Placement.multiply(surface.OTD_GridFrame)
    for item in components(doc):
        proxy = getattr(item, "OTD_OpticProxy", None)
        if proxy:
            proxy.Placement = item.Placement
        marker = getattr(item, "OTD_AnchorMarker", None)
        if marker:
            marker.Placement = item.Placement.multiply(item.OTD_Anchor)
    doc.recompute()


def _surface_frame(surface):
    return surface.Placement.multiply(surface.OTD_GridFrame)


def _place_at(obj, surface, i, j, lift, angle, offset_x=None, offset_y=None):
    """Place the local anchor at a grid position WITHOUT overwriting 3D rotation.

    The previous implementation assigned an orientation built from a single yaw,
    destroying manual FreeCAD tip/tilt and 180-degree rotations. Translation
    now uses the current rotation, while the GUI angle applies only its DELTA.
    """
    if obj is surface:
        raise ValueError("Cannot mount a surface onto itself.")
    if not getattr(surface, "OTD_IsMountSurface", False):
        raise ValueError("Select a registered mounting surface.")
    if i < 0 or i >= surface.OTD_Columns or j < 0 or j >= surface.OTD_Rows:
        raise ValueError("Hole index is outside the selected surface grid.")
    _tag_component(obj)
    if offset_x is None:
        offset_x = obj.OTD_OffsetX.Value
    if offset_y is None:
        offset_y = obj.OTD_OffsetY.Value
    frame = _surface_frame(surface)
    target = frame.multVec(V(i * surface.OTD_PitchX.Value + offset_x,
                                 j * surface.OTD_PitchY.Value + offset_y, lift))

    old_angle = obj.OTD_Angle.Value
    delta = angle - old_angle
    old_rotation = obj.Placement.Rotation
    if abs(delta) > 1e-9:
        normal = frame.Rotation.multVec(V(0, 0, 1))
        new_rotation = App.Rotation(normal, delta).multiply(old_rotation)
    else:
        new_rotation = old_rotation
    # The chosen point on the part, not the STEP origin, sits on the hole.
    origin = target - new_rotation.multVec(obj.OTD_Anchor.Base)
    obj.Placement = App.Placement(origin, new_rotation)
    obj.OTD_AttachedTo = surface
    obj.OTD_HoleI = i
    obj.OTD_HoleJ = j
    obj.OTD_Lift = lift
    obj.OTD_OffsetX = offset_x
    obj.OTD_OffsetY = offset_y
    obj.OTD_Angle = angle
    obj.OTD_LastGridWorld = frame
    obj.OTD_PoseValid = True
    return target


def _optical_offset_from_anchor(obj, surface=None):
    """Project optical-center separation along the selected surface normal.

    Works even after a STEP component has been freely rotated/tilted in FreeCAD.
    """
    if not getattr(obj, "OTD_HasOptics", False):
        return 0.0
    if surface is None:
        surface = getattr(obj, "OTD_AttachedTo", None)
    if surface is None:
        return obj.OTD_Anchor.inverse().multVec(obj.OTD_OpticCenter).z
    normal = _surface_frame(surface).Rotation.multVec(V(0, 0, 1))
    local_delta = obj.OTD_OpticCenter - obj.OTD_Anchor.Base
    return obj.Placement.Rotation.multVec(local_delta).dot(normal)


def reference_height(obj):
    return obj.OTD_Lift.Value + _optical_offset_from_anchor(obj)


def lift_for_reference_height(obj, desired_height, surface=None):
    return desired_height - _optical_offset_from_anchor(obj, surface)


def set_anchor_local_position(obj, xyz):
    """Move the LOCAL mounting frame; for attached parts keep its hole fixed."""
    _tag_component(obj)
    anchor = obj.OTD_Anchor
    obj.OTD_Anchor = App.Placement(V(*xyz), anchor.Rotation)
    surface = getattr(obj, "OTD_AttachedTo", None)
    if surface is not None:
        _place_at(obj, surface, obj.OTD_HoleI, obj.OTD_HoleJ,
                  obj.OTD_Lift.Value, obj.OTD_Angle.Value)
    _refresh_children(obj.Document)
    return obj.OTD_Anchor.Base


def toggle_anchor_marker(obj):
    """Show the component's local anchor with three short construction axes."""
    _tag_component(obj)
    marker = getattr(obj, "OTD_AnchorMarker", None)
    if marker is not None:
        marker.ViewObject.Visibility = not marker.ViewObject.Visibility
        return marker.ViewObject.Visibility
    size = 12.0
    shapes = [Part.makeLine(V(0, 0, 0), V(size, 0, 0)),
              Part.makeLine(V(0, 0, 0), V(0, size, 0)),
              Part.makeLine(V(0, 0, 0), V(0, 0, size))]
    marker = obj.Document.addObject("Part::Feature", "MountAnchorAxes")
    marker.Label = obj.Label + " | local mount axes (X/Y/Z)"
    marker.Shape = Part.makeCompound(shapes)
    marker.ViewObject.LineColor = (1.0, 0.55, 0.0)
    marker.ViewObject.LineWidth = 3
    if hasattr(marker.ViewObject, "Selectable"):
        marker.ViewObject.Selectable = False
    obj.OTD_AnchorMarker = marker
    marker.Placement = obj.Placement.multiply(obj.OTD_Anchor)
    obj.Document.recompute()
    return True


def _normalize_axis_position(coordinate, pitch, count):
    """Encode a continuous coordinate as nearest hole + residual offset.

    Allow fine offsets within the half-pitch margins of a breadboard while
    keeping hole indices in range. This does NOT mean an off-hole mounting
    position is mechanically fastened to an actual hole.
    """
    low = -pitch / 2.0
    high = (count - 0.5) * pitch
    if coordinate < low - 1e-6 or coordinate > high + 1e-6:
        raise ValueError("Movement is beyond the current mounting grid area.")
    nominal = int(math.floor(coordinate / pitch + 0.5))
    nominal = min(max(nominal, 0), count - 1)
    return nominal, coordinate - nominal * pitch


def add_breadboard(doc, name, width, depth, thickness, x, y, top_z,
                   pitch=25.0, add_markers=False):
    """Add one independent breadboard to the active document, without reset.

    X,Y are world coordinates of the lower-left corner. top_z is the top
    mounting face's world height; no supports or drilled bores are assumed.
    """
    if min(width, depth, thickness, pitch) <= 0:
        raise ValueError("Breadboard dimensions, thickness and hole pitch must be positive.")
    if width < pitch or depth < pitch:
        raise ValueError("Breadboard must be at least one hole pitch in each direction.")
    cols = int(math.floor(width / pitch + 1e-9))
    rows = int(math.floor(depth / pitch + 1e-9))
    if cols * rows > 100000:
        raise ValueError("The requested breadboard grid is too large.")
    board = doc.addObject("Part::Feature", "OTDBreadboard")
    board.Label = name.strip() or "Optical breadboard"
    board.Shape = Part.makeBox(width, depth, thickness)
    board.Placement.Base = V(x, y, top_z - thickness)
    _tag_surface(board)
    board.OTD_GridFrame = App.Placement(V(pitch / 2.0, pitch / 2.0, thickness), App.Rotation())
    board.OTD_PitchX = pitch
    board.OTD_PitchY = pitch
    board.OTD_Columns = cols
    board.OTD_Rows = rows
    board.ViewObject.ShapeColor = (0.53, 0.63, 0.72)
    _property(board, "App::PropertyString", "OTD_BoardNote",
              tooltip="Breadboard is a geometric plate; holes are reference positions")
    board.OTD_BoardNote = "Conceptual plate: grid markers are not machined holes"
    doc.recompute()
    if add_markers and cols * rows <= 2500:
        make_grid_markers(board)
    return board


def update_mounted(doc):
    """Apply breadboard motion as a relative 3D transform, without reset.

    The old implementation rebuilt the object's pose from yaw and hole index,
    losing any manual adjustments. A stored grid-frame snapshot supplies the
    rigid transform since the last placement or board update.
    """
    count = 0
    for item in components(doc):
        target = getattr(item, "OTD_AttachedTo", None)
        if target is None or target not in doc.Objects:
            continue
        _tag_component(item)
        frame = _surface_frame(target)
        if item.OTD_PoseValid:
            delta = frame.multiply(item.OTD_LastGridWorld.inverse())
            item.Placement = delta.multiply(item.Placement)
            item.OTD_LastGridWorld = frame
        else:
            # Legacy documents lack a previous grid transform. Adopt their
            # current pose rather than guessing and destroying the rotation.
            item.OTD_LastGridWorld = frame
            item.OTD_PoseValid = True
        count += 1
    _refresh_children(doc)
    return count


def _get_or_tag_anchor(obj):
    if not getattr(obj, "OTD_Smart", False):
        _tag_component(obj)
        # A helpful initial mounting anchor at the bounding-box bottom center.
        bb = obj.Shape.BoundBox
        world_bottom = V((bb.XMin + bb.XMax) / 2,
                         (bb.YMin + bb.YMax) / 2, bb.ZMin)
        obj.OTD_Anchor = App.Placement(obj.Placement.inverse().multVec(world_bottom),
                                      App.Rotation())


def make_imported_step(doc, filepath):
    # One compound object makes selection and replacement simpler than hundreds
    # of imported sub-parts. STEP display colors are not preserved by Part.read.
    shape = Part.read(filepath)
    if shape.isNull():
        raise ValueError("STEP file contained no solid or surface geometry.")
    name = os.path.splitext(os.path.basename(filepath))[0]
    obj = doc.addObject("Part::Feature", "ThorlabsPart")
    obj.Label = name
    obj.Shape = shape
    _tag_component(obj)
    obj.OTD_Type = "Mechanical"
    _get_or_tag_anchor_initial(obj)
    obj.ViewObject.ShapeColor = (0.74, 0.78, 0.83)
    doc.recompute()
    return obj


def _get_or_tag_anchor_initial(obj):
    bb = obj.Shape.BoundBox
    world_pt = V((bb.XMin + bb.XMax) / 2,
                 (bb.YMin + bb.YMax) / 2, bb.ZMin)
    obj.OTD_Anchor = App.Placement(obj.Placement.inverse().multVec(world_pt), App.Rotation())


def mark_anchor(obj, shape, reverse=False):
    _get_or_tag_anchor(obj)
    if shape.ShapeType == "Face":
        center = shape.CenterOfMass
        normal = plane_normal(shape)
    elif shape.ShapeType == "Edge" and isinstance(shape.Curve, Part.Circle):
        center = shape.Curve.Center
        normal = shape.Curve.Axis
    else:
        raise ValueError("Select a planar face or circular edge as the anchor.")
    if normal.dot(V(0, 0, 1)) < 0:
        normal = -normal
    if reverse:
        normal = -normal
    rotation = App.Rotation(V(0, 0, 1), normal)
    world = App.Placement(center, rotation)
    obj.OTD_Anchor = obj.Placement.inverse().multiply(world)
    return center


def mark_optical_reference(obj, shape, kind, diameter):
    _get_or_tag_anchor(obj)
    if kind == "Mirror":
        if shape.ShapeType != "Face":
            raise ValueError("For a mirror, select a flat reflective face.")
        center, normal = _optical_frame_from_face(shape, obj)
    else:
        # An axis marker for lenses/fiber collimators; no optical simulation yet.
        if shape.ShapeType == "Edge" and isinstance(shape.Curve, Part.Circle):
            center = obj.Placement.inverse().multVec(shape.Curve.Center)
            normal = obj.Placement.inverse().Rotation.multVec(shape.Curve.Axis)
        elif shape.ShapeType == "Face":
            center, normal = _optical_frame_from_face(shape, obj)
        else:
            raise ValueError("Select a circular edge or a planar reference face.")
    obj.OTD_Type = kind
    obj.OTD_HasOptics = True
    obj.OTD_OpticCenter = center
    obj.OTD_OpticNormal = normal
    obj.OTD_OpticDiameter = diameter
    if kind == "Mirror":
        _make_mirror_proxy(obj)
    return center, normal


def _make_mirror_proxy(obj):
    doc = obj.Document
    proxy = getattr(obj, "OTD_OpticProxy", None)
    if not proxy:
        proxy = doc.addObject("Part::Feature", "ReflectiveDisk")
        proxy.Label = obj.Label + " | ray-tracing disk"
        obj.OTD_OpticProxy = proxy
    center = obj.OTD_OpticCenter
    axis = obj.OTD_OpticNormal
    edge = Part.makeCircle(obj.OTD_OpticDiameter.Value / 2.0, center, axis)
    proxy.Shape = Part.Face(Part.Wire([edge]))
    proxy.Placement = obj.Placement
    proxy.ViewObject.Visibility = False
    doc.recompute()
    return proxy


def optics_module():
    try:
        import OpticsWorkbench as OW
        return OW
    except ImportError:
        return None


def register_mirror_with_optics(owner):
    OW = optics_module()
    if OW is None:
        raise RuntimeError("Install Optics Workbench through Tools > Addon Manager, then restart FreeCAD.")
    if getattr(owner, "OTD_Type", "") != "Mirror" or not owner.OTD_OpticProxy:
        raise ValueError("First mark a planar face as this component's mirror surface.")
    proxy = owner.OTD_OpticProxy
    for obj in owner.Document.Objects:
        if getattr(obj, "OTD_RegisteredMirrorOwner", None) is owner:
            return obj
    optical = OW.makeMirror(base=[proxy])
    _property(optical, "App::PropertyLink", "OTD_RegisteredMirrorOwner")
    optical.OTD_RegisteredMirrorOwner = owner
    optical.Label = owner.Label + " | mirror optics"
    return optical



def duplicate_component(source):
    """Duplicate an annotated CAD part, all local optical references and proxy.

    Existing STEP geometry is copied into the current FreeCAD document: the
    source STEP file does not have to be available. Mirror workers are never
    shared between the original and duplicate.
    """
    if not getattr(source, "OTD_Smart", False) or not _is_shape(source):
        raise ValueError("Choose a smart component from the current document.")
    doc = source.Document
    _tag_component(source)
    new = doc.addObject("Part::Feature", "OpticalComponentCopy")
    new.Label = source.Label + " (copy)"
    new.Shape = source.Shape.copy()
    new.Placement = source.Placement
    new.ViewObject.ShapeColor = source.ViewObject.ShapeColor
    _tag_component(new)
    new.OTD_Type = source.OTD_Type
    new.OTD_Anchor = source.OTD_Anchor
    new.OTD_HasOptics = source.OTD_HasOptics
    new.OTD_OpticCenter = source.OTD_OpticCenter
    new.OTD_OpticNormal = source.OTD_OpticNormal
    new.OTD_OpticDiameter = source.OTD_OpticDiameter
    new.OTD_OffsetX = source.OTD_OffsetX.Value
    new.OTD_OffsetY = source.OTD_OffsetY.Value
    new.OTD_Angle = source.OTD_Angle.Value

    surface = getattr(source, "OTD_AttachedTo", None)
    if surface is not None and surface in doc.Objects:
        i = int(source.OTD_HoleI)
        j = int(source.OTD_HoleJ)
        # Offer the adjacent hole in X if it exists; otherwise the same one.
        if i + 1 < surface.OTD_Columns:
            i += 1
        _place_at(new, surface, i, j, source.OTD_Lift.Value,
                  source.OTD_Angle.Value, source.OTD_OffsetX.Value,
                  source.OTD_OffsetY.Value)

    # Never duplicate the pointer to the original's optical surface.
    if (source.OTD_HasOptics and source.OTD_Type == "Mirror"
            and getattr(source, "OTD_OpticProxy", None) is not None):
        _make_mirror_proxy(new)
        was_registered = any(
            getattr(worker, "OTD_RegisteredMirrorOwner", None) is source
            for worker in doc.Objects)
        if was_registered and optics_module() is not None:
            register_mirror_with_optics(new)

    _refresh_children(doc)
    return new


def replace_component(old, new_step_file):
    """Swap one CAD component; remove stale optical bodies to avoid ghost rays.

    Mechanical hole/placement is preserved when possible. The replacement's
    optical references must be re-annotated since CAD geometry has changed.
    """
    if not getattr(old, "OTD_Smart", False):
        raise ValueError("Select an annotated smart component for replacement.")
    doc = old.Document
    _tag_component(old)  # migrate properties on old .FCStd files
    attached = getattr(old, "OTD_AttachedTo", None)
    i, j = old.OTD_HoleI, old.OTD_HoleJ
    lift, angle = old.OTD_Lift.Value, old.OTD_Angle.Value
    offset_x, offset_y = old.OTD_OffsetX.Value, old.OTD_OffsetY.Value
    previous_placement = old.Placement
    old_name = old.Label
    proxy = getattr(old, "OTD_OpticProxy", None)
    new = make_imported_step(doc, new_step_file)
    if attached and attached in doc.Objects:
        _place_at(new, attached, i, j, lift, angle, offset_x, offset_y)
    else:
        new.Placement = previous_placement
    # Remove mirror workers before the proxy/owner to avoid dangling link lists.
    for ob in list(doc.Objects):
        if getattr(ob, "OTD_RegisteredMirrorOwner", None) is old:
            doc.removeObject(ob.Name)
    doc.removeObject(old.Name)
    if proxy and proxy in doc.Objects:
        doc.removeObject(proxy.Name)
    doc.recompute()
    return new, old_name


def create_or_update_laser(doc, pos, direction, wavelength):
    """Keep exactly one Designer-managed beam per document, including v0.2 rays.

    In-place edits preserve the ray's FreeCAD object identity. Manual rays
    created elsewhere in Optics Workbench are not affected.
    """
    OW = optics_module()
    if OW is None:
        raise RuntimeError("Install Optics Workbench from Addon Manager first.")
    if direction.Length < 1e-9:
        raise ValueError("Laser direction vector cannot be zero.")
    direction = V(direction.x, direction.y, direction.z)
    direction.normalize()
    managed = [obj for obj in list(doc.Objects)
               if getattr(obj, "OTD_ManagedLaser", False)
               or (obj.Label.startswith("Laser ") and OW.isRay(obj))]
    if managed:
        ray = managed[0]
        for previous in managed[1:]:
            doc.removeObject(previous.Name)
        ray.Placement = App.Placement(pos, App.Rotation(V(1, 0, 0), direction))
        ray.Wavelength = wavelength
        ray.Power = True
        ray.touch()
        updated = True
    else:
        ray = OW.makeRay(position=pos, direction=direction,
                         wavelength=wavelength, maxRayLength=2000,
                         maxNrReflections=20)
        updated = False
    _property(ray, "App::PropertyBool", "OTD_ManagedLaser",
              tooltip="Beam controlled by Optical Table Designer")
    ray.OTD_ManagedLaser = True
    ray.Label = "Laser %.0f nm" % wavelength
    OW.restartAll()
    return ray, updated


def demo_table():
    doc = App.newDocument("OpticalTableDemo")
    table = doc.addObject("Part::Feature", "MainTable")
    table.Label = "Main Optical Table (600 x 450 mm)"
    table.Shape = Part.makeBox(600, 450, 25, V(0, 0, -25))
    _tag_surface(table)
    table.OTD_GridFrame = App.Placement(V(12.5, 12.5, 0), App.Rotation())
    table.OTD_PitchX = table.OTD_PitchY = 25
    table.OTD_Columns = 24
    table.OTD_Rows = 18
    table.ViewObject.ShapeColor = (0.40, 0.47, 0.55)

    board = doc.addObject("Part::Feature", "ElevatedBreadboard")
    board.Label = "Breadboard A (200 x 150 mm, z = 100 mm)"
    board.Shape = Part.makeBox(200, 150, 12)
    board.Placement.Base = V(270, 160, 88)
    _tag_surface(board)
    board.OTD_GridFrame = App.Placement(V(12.5, 12.5, 12), App.Rotation())
    board.OTD_PitchX = board.OTD_PitchY = 25
    board.OTD_Columns = 8
    board.OTD_Rows = 6
    board.ViewObject.ShapeColor = (0.53, 0.63, 0.72)

    supports = doc.addObject("Part::Feature", "BreadboardSupports")
    supports.Label = "Breadboard supports (illustrative only)"
    rods = [Part.makeCylinder(7, 88, V(x, y, 0))
            for x in (290, 450) for y in (180, 290)]
    supports.Shape = Part.makeCompound(rods)
    supports.ViewObject.ShapeColor = (0.65, 0.66, 0.68)

    # An example part with a default anchor, to make placement testable even
    # before any Thorlabs STEP geometry has been downloaded.
    sample = doc.addObject("Part::Feature", "ExampleMirrorMount")
    sample.Label = "Example mirror plate (not Thorlabs)"
    sample.Shape = Part.makeBox(26, 3, 26, V(-13, -1.5, 35)).fuse(
        Part.makeCylinder(4, 35, V(0, 0, 0)))
    sample.ViewObject.ShapeColor = (0.69, 0.76, 0.83)
    _tag_component(sample)
    sample.OTD_Type = "Mechanical"
    sample.OTD_Anchor = App.Placement()
    _place_at(sample, table, 4, 7, 0, 0)

    doc.recompute()
    make_grid_markers(table)
    make_grid_markers(board)
    Gui.activeDocument().activeView().viewAxonometric()
    Gui.activeDocument().activeView().fitAll()
    return doc


HELP_HTML = """<html><body style="font-size: 11pt;">
<h2>Optical Table Designer — workflow</h2>
<h3>1. Start with a table</h3>
<p>Open an existing <b>.FCStd</b> optical-table file, or use
<b>Setup → Create demo in NEW document</b> to test the tools.
The demo makes a separate document.</p>
<h3>2. Add an optical breadboard</h3>
<p>Click <b>Setup → Add breadboard to current document</b>.
Enter width, depth, thickness, world X/Y corner and top Z height. The
breadboard will appear in the current model with its own selectable grid.
It does not replace the optical table or remove existing parts.</p>
<h3>3. Import Thorlabs STEP components</h3>
<p>Click <b>Setup → Import STEP component</b>. For precise positioning,
select a mounting face or circular edge of the imported part in the 3D
viewer, then click <b>Set selected component's mount anchor</b>.
The import defaults to a bottom-center mounting anchor.</p>
<h3>4. Annotate the optical center (recommended)</h3>
<p>For a mirror: select the reflective planar face, then use
<b>Optics → Mirror → Annotate selected optical reference</b>.
For a lens/fiber axis: select its circular edge. Once annotated,
<b>Height above face</b> places the <em>optical center</em> at the
chosen height, not the CAD object's bottom. Without an optical annotation,
height instead refers to the mounting anchor.</p>
<h3>5. Mount and reposition parts</h3>
<p>Open <b>Mount / Move</b>. Choose the <b>mounting surface</b> (main
optical table or breadboard), choose the component, and set <b>Height
above face</b>. Choose a hole column/row or click a face in the 3D model
and select the nearest hole. Click <b>Apply hole, height and rotation</b>.</p>
<p>If the mounting surface comes from an imported STEP file, define its
mounting grid once in the <b>Surfaces</b> tab. Then use the large <b>arrow buttons</b> to move along the selected
surface's local X/Y directions while keeping the reference height fixed.
<b>1 mounting hole</b> steps between hole centers; mm steps permit small
XY offsets from the grid (not necessarily physically mountable).
Use <b>Raise / Lower</b> for Z and <b>Rotate</b> to adjust yaw.
Manual FreeCAD rotations (including 180-degree flips and tilt) are preserved
when using the arrows. All movement is undoable using FreeCAD Undo.</p>
<h3>6. Duplicate optical components</h3>
<p>In <b>Components</b>, select a part already in the current document
and click <b>Duplicate highlighted component</b>. Its geometry, mounting
anchor, optical center, and an independent optical mirror proxy are copied.
A mounted copy is placed at the next X-grid hole when available.</p>
<h3>7. Edit local mounting anchor</h3>
<p>In <b>Setup</b>, use the <b>Local mount anchor XYZ</b> spin boxes to
move the reference point on the CAD component that should sit on top of
a mounting hole. <b>Show/hide local anchor axes</b> reveals the location.
Changing the anchor does not rotate the CAD body, and an attached object
is re-positioned so its new anchor sits on the same selected hole.</p>
<h3>8. Switch views / trace light / save</h3>
<p>Use <b>Views / Save</b> for 3D, top, front and right cameras. A
periscope works in real 3D with appropriate mirror orientations. Optionally
install <b>Optics Workbench</b>, register mirror surfaces and create rays
in the <b>Optics</b> tab. <b>Create / update laser ray</b> edits the existing
Designer laser in place, rather than accumulating multiple laser rays. The auto-retrace checkbox can be disabled for
large models. Save another cube configuration using <b>Save As</b>.
</p>
<h3>Notes and limitations</h3>
<p>Mounting grids are idealized reference locations (the program does not
machine holes). All dimensions are in mm. A breadboard created here is
axis-aligned initially; FreeCAD's Placement property can subsequently
rotate it, after which the arrows operate in its own local grid coordinates.
If you move a breadboard using ordinary FreeCAD tools, click
<b>Update mounted positions</b> so attached parts follow it.
The project is a prototype and mechanical collisions/fastener compatibility
are not automatically validated.</p>
</body></html>
"""


class OpticalTablePanel(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()
        self.refresh_lists()

    def _spin(self, value, low=-10000, high=10000, decimals=2, suffix=""):
        w = QtWidgets.QDoubleSpinBox()
        w.setRange(low, high)
        w.setDecimals(decimals)
        w.setValue(value)
        w.setSingleStep(1)
        if suffix:
            w.setSuffix(suffix)
        return w

    def _row(self, box, label, widget):
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel(label))
        row.addWidget(widget, 1)
        box.addLayout(row)

    def _button(self, layout, label, callback, tooltip=None):
        b = QtWidgets.QPushButton(label)
        if tooltip:
            b.setToolTip(tooltip)
        b.clicked.connect(lambda checked=False: self._safe(callback))
        layout.addWidget(b)
        return b

    def _safe(self, callback):
        try:
            result = callback()
            if result is not None:
                self.status.setText(str(result))
                say(result)
        except Exception as exc:
            traceback.print_exc()
            App.Console.PrintError(traceback.format_exc())
            self.status.setText("Error: " + str(exc))
            QtWidgets.QMessageBox.warning(self, "Optical Table Designer", str(exc))

    def _scroll_tab(self, tabs, content, name):
        """Keep the tools accessible in a narrow FreeCAD dock."""
        scroller = QtWidgets.QScrollArea()
        scroller.setWidgetResizable(True)
        scroller.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroller.setWidget(content)
        tabs.addTab(scroller, name)
        return scroller

    def _build(self):
        main = QtWidgets.QVBoxLayout(self)
        main.setContentsMargins(8, 8, 8, 8)
        intro = QtWidgets.QLabel("Optical Table Designer · v0.3")
        intro.setStyleSheet("font-weight:bold; font-size:14px;")
        main.addWidget(intro)
        tabs = QtWidgets.QTabWidget()
        main.addWidget(tabs, 1)

        # Setup -------------------------------------------------------------
        setup = QtWidgets.QWidget()
        sl = QtWidgets.QVBoxLayout(setup)
        self._button(sl, "Create demo in NEW document", self.create_demo,
                     "Creates a separate demonstration document; does not add to your existing setup")
        self._button(sl, "Add breadboard to current document…", self.insert_breadboard,
                     "Specify dimensions and XYZ location; your existing geometry stays unchanged")
        self._button(sl, "Import STEP component…", self.import_step)
        self._button(sl, "Replace selected component with STEP…", self.replace_step,
                     "Uses the component selected in the Mount tab; old optical proxies are deleted")
        self._button(sl, "Refresh object lists", self.refresh_lists)
        self._button(sl, "Duplicate current component", self.copy_current_component,
                     "Copy STEP geometry, mounting anchor, optical center and independent mirror optics")
        sl.addSpacing(8)
        sl.addWidget(QtWidgets.QLabel("SMART COMPONENT REFERENCE"))
        sl.addWidget(QtWidgets.QLabel("Select the component's mounting face or circular edge in 3D."))
        self.reverse_normal = QtWidgets.QCheckBox("Flip anchor normal")
        sl.addWidget(self.reverse_normal)
        self._button(sl, "Set selected component's mount anchor", self.set_anchor)
        sl.addWidget(QtWidgets.QLabel("LOCAL MOUNT ANCHOR (component coordinates, mm)"))
        sl.addWidget(QtWidgets.QLabel("Edit the contact point that should sit over the hole. CAD rotation is retained."))
        self.anchor_xyz = {}
        for axis in ("X", "Y", "Z"):
            spin = self._spin(0, -100000, 100000, 2, " mm")
            self.anchor_xyz[axis] = spin
            self._row(sl, "Anchor " + axis, spin)
        self._button(sl, "Apply local anchor XYZ", self.apply_anchor_xyz)
        self.anchor_step = self._spin(1, 0.01, 1000, 2, " mm")
        self._row(sl, "Anchor nudge step", self.anchor_step)
        for axis in ("X", "Y", "Z"):
            pair = QtWidgets.QHBoxLayout()
            self._button(pair, axis + " −", lambda a=axis: self.nudge_anchor(a, -1))
            self._button(pair, axis + " +", lambda a=axis: self.nudge_anchor(a, +1))
            sl.addLayout(pair)
        self._button(sl, "Show / hide local anchor axes", self.show_anchor)
        sl.addWidget(QtWidgets.QLabel("An anchor moves relative to the STEP body; the geometry is not remodeled."))
        sl.addStretch(1)
        self._scroll_tab(tabs, setup, "Setup")

        # Mountable-surface editor; kept separate from everyday positioning.
        su = QtWidgets.QWidget()
        sul = QtWidgets.QVBoxLayout(su)
        sul.addWidget(QtWidgets.QLabel("SURFACE DEFINITION"))
        sul.addWidget(QtWidgets.QLabel("Select a top planar face in 3D, then register it."))
        self.surface_pitch = self._spin(25, 0.1, 1000, 2, " mm")
        self._row(sul, "Suggested hole pitch", self.surface_pitch)
        self._button(sul, "Register selected face as mounting surface", self.register_selected_surface)
        self._button(sul, "Suggest top face of selected object", self.register_auto_surface)
        sul.addSpacing(7)
        sul.addWidget(QtWidgets.QLabel("ACTIVE SURFACE GRID (EDITABLE)"))
        self.pitchx = self._spin(25, 0.1, 1000, 2, " mm")
        self.pitchy = self._spin(25, 0.1, 1000, 2, " mm")
        self._row(sul, "Pitch X", self.pitchx)
        self._row(sul, "Pitch Y", self.pitchy)
        self.columns = QtWidgets.QSpinBox()
        self.columns.setRange(1, 1000)
        self.rows = QtWidgets.QSpinBox()
        self.rows.setRange(1, 1000)
        self._row(sul, "Columns", self.columns)
        self._row(sul, "Rows", self.rows)
        self._button(sul, "Apply active surface grid parameters", self.apply_surface_grid)
        self._button(sul, "Set first hole from selected circular edge", self.set_grid_origin)
        sul.addStretch(1)
        self._scroll_tab(tabs, su, "Surfaces")

        # Everyday placement tools stay in their own uncluttered tab.
        mt = QtWidgets.QWidget()
        ml = QtWidgets.QVBoxLayout(mt)
        ml.addWidget(QtWidgets.QLabel("PLACE / MOVE COMPONENT"))
        ml.addWidget(QtWidgets.QLabel("Surface-local X/Y directions; +Z points away from the face."))
        self.surface_combo = QtWidgets.QComboBox()
        self._row(ml, "Mounting surface", self.surface_combo)
        self.surface_combo.currentIndexChanged.connect(self._surface_changed)
        self.component_combo = QtWidgets.QComboBox()
        self._row(ml, "Component", self.component_combo)
        self.component_combo.currentIndexChanged.connect(self._component_changed)
        self._button(ml, "Use component selected in 3D view", self.use_selected_component)
        self.col_spin = QtWidgets.QSpinBox()
        self.col_spin.setRange(0, 10000)
        self._row(ml, "Hole column", self.col_spin)
        self.row_spin = QtWidgets.QSpinBox()
        self.row_spin.setRange(0, 10000)
        self._row(ml, "Hole row", self.row_spin)
        self.height = self._spin(0, -1000, 5000, 2, " mm")
        self._row(ml, "Height above face", self.height)
        self.height_help = QtWidgets.QLabel("Annotate a lens/mirror axis to position its optical center at this height.")
        self.height_help.setWordWrap(True)
        ml.addWidget(self.height_help)
        self.rotation = self._spin(0, -3600, 3600, 2, "°")
        self._row(ml, "Rotation around surface normal", self.rotation)
        self._button(ml, "Choose nearest hole from 3D face click", self.select_clicked_hole)
        self._button(ml, "Apply hole, height and rotation", self.place_component)

        ml.addSpacing(8)
        ml.addWidget(QtWidgets.QLabel("ARROW POSITIONING — KEEPS HEIGHT CONSTANT"))
        self.step_combo = QtWidgets.QComboBox()
        self.step_combo.addItems(["1 mounting hole", "1 mm", "5 mm", "10 mm", "Custom mm"])
        self._row(ml, "XY arrow step", self.step_combo)
        self.custom_step = self._spin(2.5, 0.01, 1000, 2, " mm")
        self._row(ml, "Custom step", self.custom_step)
        self.step_combo.currentIndexChanged.connect(
            lambda index: self.custom_step.setEnabled(index == 4))
        self.custom_step.setEnabled(False)
        pad = QtWidgets.QGridLayout()
        pad.setHorizontalSpacing(5)
        pad.setVerticalSpacing(5)
        pad.addWidget(QtWidgets.QLabel("Local +Y"), 0, 0)
        up = QtWidgets.QPushButton("↑  Y+")
        down = QtWidgets.QPushButton("↓  Y−")
        left = QtWidgets.QPushButton("←  X−")
        right = QtWidgets.QPushButton("→  X+")
        for button, di, dj in ((up,0,1), (down,0,-1),(left,-1,0),(right,1,0)):
            button.clicked.connect(lambda checked=False, a=di, b=dj: self._safe(
                lambda: self.nudge_component(a, b)))
        pad.addWidget(up, 0, 1)
        pad.addWidget(left, 1, 0)
        pad.addWidget(QtWidgets.QLabel("XY"), 1, 1, alignment=QtCore.Qt.AlignCenter)
        pad.addWidget(right, 1, 2)
        pad.addWidget(down, 2, 1)
        ml.addLayout(pad)
        hp = QtWidgets.QHBoxLayout()
        self.height_step = self._spin(5, 0.01, 1000, 2, " mm")
        hp.addWidget(QtWidgets.QLabel("Z step"))
        hp.addWidget(self.height_step)
        ml.addLayout(hp)
        actions = QtWidgets.QHBoxLayout()
        self._button(actions, "Z −  Lower", lambda: self.nudge_height(-1))
        self._button(actions, "Z +  Raise", lambda: self.nudge_height(1))
        ml.addLayout(actions)
        angles = QtWidgets.QHBoxLayout()
        self.angle_step = self._spin(5, 0.01, 180, 2, "°")
        angles.addWidget(QtWidgets.QLabel("Angle step"))
        angles.addWidget(self.angle_step)
        ml.addLayout(angles)
        angle_actions = QtWidgets.QHBoxLayout()
        self._button(angle_actions, "↶  Rotate −", lambda: self.nudge_angle(-1))
        self._button(angle_actions, "↷  Rotate +", lambda: self.nudge_angle(1))
        ml.addLayout(angle_actions)
        self.snap_warning = QtWidgets.QLabel(
            "1-hole moves snap to mounting holes. Millimetre moves add fine XY offsets; "
            "an off-grid position may require another mounting method.")
        self.snap_warning.setWordWrap(True)
        ml.addWidget(self.snap_warning)
        self.auto_retrace = QtWidgets.QCheckBox("Recalculate laser rays after moving (if available)")
        self.auto_retrace.setChecked(True)
        ml.addWidget(self.auto_retrace)
        self._button(ml, "Show / rebuild hole-center markers", self.build_markers)
        self._button(ml, "Update mounted positions", self.update_placements)
        ml.addStretch(1)
        move_tab = self._scroll_tab(tabs, mt, "Mount / Move")
        # Keep the most commonly used controls immediately after Setup.
        tabs.removeTab(tabs.indexOf(move_tab))
        tabs.insertTab(1, move_tab, "Mount / Move")

        # Component copies; source list is the current FreeCAD document.
        cp = QtWidgets.QWidget()
        cl = QtWidgets.QVBoxLayout(cp)
        cl.addWidget(QtWidgets.QLabel("REUSABLE COMPONENTS IN CURRENT DOCUMENT"))
        cl.addWidget(QtWidgets.QLabel("Copies include local optical references and mounting anchors."))
        self.component_filter = QtWidgets.QLineEdit()
        self.component_filter.setPlaceholderText("Filter by name or optical type…")
        cl.addWidget(self.component_filter)
        self.component_list = QtWidgets.QListWidget()
        self.component_list.setMinimumHeight(150)
        cl.addWidget(self.component_list, 1)
        self.component_filter.textChanged.connect(self.update_component_library)
        self._button(cl, "Duplicate highlighted component", self.copy_component)
        self._button(cl, "Use highlighted component for placement", self.use_library_component)
        self._button(cl, "Refresh list", self.refresh_lists)
        cl.addWidget(QtWidgets.QLabel("Copies are made in the current .FCStd file. "
                                    "Save As for another optical-table configuration."))
        self._scroll_tab(tabs, cp, "Components")

        # Optical refs and rays ----------------------------------------------
        op = QtWidgets.QWidget()
        ol = QtWidgets.QVBoxLayout(op)
        ol.addWidget(QtWidgets.QLabel("OPTICAL REFERENCE"))
        ol.addWidget(QtWidgets.QLabel("Select a mirror face or a lens circular edge in 3D."))
        self.optic_type = QtWidgets.QComboBox()
        self.optic_type.addItems(["Mirror", "Lens axis", "Fiber axis"])
        self._row(ol, "Reference type", self.optic_type)
        self.aperture = self._spin(25.4, 0.1, 1000, 2, " mm")
        self._row(ol, "Aperture diameter", self.aperture)
        self._button(ol, "Annotate selected optical reference", self.add_optics_ref)
        self._button(ol, "Register selected mirror with Optics Workbench", self.add_mirror)
        ol.addSpacing(10)
        ol.addWidget(QtWidgets.QLabel("LASER SOURCE (GLOBAL COORDINATES)"))
        self.beam_fields = {}
        for name, initial in [("X", 30), ("Y", 190), ("Z", 75),
                              ("dx", 1), ("dy", 0), ("dz", 0)]:
            w = self._spin(initial, -100000, 100000, 2)
            self.beam_fields[name] = w
            self._row(ol, name + (" [mm]" if name in ("X", "Y", "Z") else ""), w)
        self.wavelength = self._spin(532, 100, 3000, 0, " nm")
        self._row(ol, "Wavelength", self.wavelength)
        self._button(ol, "Create / update laser ray", self.add_ray)
        self._button(ol, "Load saved laser coordinates", self.load_laser)
        self._button(ol, "Synchronize optics + retrace beams", self.retrace)
        ol.addWidget(QtWidgets.QLabel("Requires Optics Workbench for reflected/refracted rays.\nLens/Fiber axis annotations are positioning references (not refractive solids)."))
        ol.addStretch(1)
        self._scroll_tab(tabs, op, "Optics")

        # Views --------------------------------------------------------------
        views = QtWidgets.QWidget()
        vl = QtWidgets.QVBoxLayout(views)
        vl.addWidget(QtWidgets.QLabel("CAMERA VIEWS — same 3D document"))
        for title, method in [("3D isometric", "viewAxonometric"),
                              ("Top (XY)", "viewTop"),
                              ("Front (XZ)", "viewFront"),
                              ("Right (YZ)", "viewRight")]:
            self._button(vl, title, lambda m=method: self.change_view(m))
        self._button(vl, "Fit all components", self.fit_view)
        self._button(vl, "Save configuration as…", self.save_as)
        vl.addWidget(QtWidgets.QLabel("Use Save As to store alternative cubes and beam paths\nas separate .FCStd documents."))
        vl.addStretch(1)
        self._scroll_tab(tabs, views, "Views / Save")

        help_panel = QtWidgets.QWidget()
        help_layout = QtWidgets.QVBoxLayout(help_panel)
        help_browser = QtWidgets.QTextBrowser()
        help_browser.setOpenExternalLinks(True)
        help_browser.setHtml(HELP_HTML)
        help_layout.addWidget(help_browser)
        tabs.addTab(help_panel, "Help")

        self.status = QtWidgets.QLabel("Ready. Start with a sample table or import a STEP file.")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("padding:8px; border-radius:4px; background:palette(alternate-base);")
        main.addWidget(self.status)

    def refresh_lists(self):
        doc = App.ActiveDocument
        for combo, items in ((self.surface_combo, surfaces(doc) if doc else []),
                             (self.component_combo, components(doc) if doc else [])):
            old_name = combo.currentData()  # Combo itemData is a string, not a FreeCAD object
            combo.blockSignals(True)
            combo.clear()
            for obj in items:
                combo.addItem(obj.Label, obj.Name)
            for idx in range(combo.count()):
                if combo.itemData(idx) == old_name:
                    combo.setCurrentIndex(idx)
                    break
            combo.blockSignals(False)
        self._surface_changed()
        self._component_changed()
        self.update_component_library()
        return "Found %d surfaces and %d components." % (
            self.surface_combo.count(), self.component_combo.count())

    def _surface_changed(self, *args):
        doc = App.ActiveDocument
        name = self.surface_combo.currentData()
        surface = doc.getObject(name) if doc and name else None
        if surface is not None and getattr(surface, "OTD_IsMountSurface", False):
            self.pitchx.setValue(surface.OTD_PitchX.Value)
            self.pitchy.setValue(surface.OTD_PitchY.Value)
            self.columns.setValue(int(surface.OTD_Columns))
            self.rows.setValue(int(surface.OTD_Rows))
            self.col_spin.setMaximum(max(0, int(surface.OTD_Columns)-1))
            self.row_spin.setMaximum(max(0, int(surface.OTD_Rows)-1))
        self._component_changed()

    def _component_changed(self, *args):
        doc = App.ActiveDocument
        name = self.component_combo.currentData()
        obj = doc.getObject(name) if doc and name else None
        if obj is None:
            return
        _tag_component(obj)  # upgrade v0.1.x metadata when first selected
        if getattr(obj, "OTD_HasOptics", False):
            self.height_help.setText("Height controls the annotated optical center above the selected surface.")
        else:
            self.height_help.setText("No optical center annotated: height controls the mount anchor. "
                                     "Use Setup / Optics to define its reference first.")
        active_surface = self.surface_combo.currentData()
        attached = getattr(obj, "OTD_AttachedTo", None)
        if attached and attached.Name == active_surface:
            self.col_spin.setValue(int(obj.OTD_HoleI))
            self.row_spin.setValue(int(obj.OTD_HoleJ))
            self.rotation.setValue(obj.OTD_Angle.Value)
            self.height.setValue(reference_height(obj))
        elif getattr(obj, "OTD_HasOptics", False) and abs(self.height.value()) < 1e-9:
            # Typical optical axes sit above the table; suggest 75 mm only
            # when the part has a known optical reference.
            self.height.setValue(75.0)
        # Otherwise retain the current target height; applying will attach
        # the component to the surface newly selected in the dropdown.
        for axis, v in zip(("X", "Y", "Z"),
                           (obj.OTD_Anchor.Base.x, obj.OTD_Anchor.Base.y,
                            obj.OTD_Anchor.Base.z)):
            self.anchor_xyz[axis].setValue(v)

    def _select_combo_object(self, combo, obj):
        for idx in range(combo.count()):
            if combo.itemData(idx) == obj.Name:
                combo.setCurrentIndex(idx)
                return True
        return False

    def update_component_library(self, *unused):
        if not hasattr(self, "component_list"):
            return
        doc = App.ActiveDocument
        previous = self.component_list.currentItem()
        old_name = previous.data(_USER_ROLE) if previous else None
        search = self.component_filter.text().strip().lower()
        self.component_list.blockSignals(True)
        self.component_list.clear()
        if doc:
            for obj in components(doc):
                desc = "%s   [%s]" % (obj.Label, getattr(obj, "OTD_Type", "Mechanical"))
                if search and search not in desc.lower():
                    continue
                entry = QtWidgets.QListWidgetItem(desc)
                entry.setData(_USER_ROLE, obj.Name)
                self.component_list.addItem(entry)
                if obj.Name == old_name:
                    self.component_list.setCurrentItem(entry)
        self.component_list.blockSignals(False)

    def _library_choice(self):
        item = self.component_list.currentItem()
        doc = App.ActiveDocument
        if item and doc:
            obj = doc.getObject(item.data(_USER_ROLE))
            if obj is not None:
                return obj
        return self._resolve(self.component_combo)

    def copy_component(self):
        return self._copy_source(self._library_choice())

    def copy_current_component(self):
        return self._copy_source(self._resolve(self.component_combo))

    def _copy_source(self, src):
        doc = src.Document
        doc.openTransaction("Duplicate optical component")
        try:
            clone = duplicate_component(src)
            doc.commitTransaction()
        except Exception:
            doc.abortTransaction()
            raise
        self.refresh_lists()
        self._select_combo_object(self.component_combo, clone)
        for row in range(self.component_list.count()):
            entry = self.component_list.item(row)
            if entry.data(_USER_ROLE) == clone.Name:
                self.component_list.setCurrentItem(entry)
                break
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(doc.Name, clone.Name)
        return "Duplicated %s, including its local optical/mounting references." % src.Label

    def use_library_component(self):
        obj = self._library_choice()
        self._select_combo_object(self.component_combo, obj)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(obj.Document.Name, obj.Name)
        return "Ready to place %s." % obj.Label

    def apply_anchor_xyz(self):
        obj = self._resolve(self.component_combo)
        xyz = [self.anchor_xyz[axis].value() for axis in ("X", "Y", "Z")]
        doc = obj.Document
        doc.openTransaction("Edit local mounting anchor")
        try:
            new_position = set_anchor_local_position(obj, xyz)
            doc.commitTransaction()
        except Exception:
            doc.abortTransaction()
            raise
        self._component_changed()
        return "%s local anchor: (%.2f, %.2f, %.2f) mm." % (
            obj.Label, new_position.x, new_position.y, new_position.z)

    def nudge_anchor(self, axis, sign):
        spin = self.anchor_xyz[axis]
        spin.setValue(spin.value() + sign * self.anchor_step.value())
        return self.apply_anchor_xyz()

    def show_anchor(self):
        obj = self._resolve(self.component_combo)
        is_visible = toggle_anchor_marker(obj)
        return "Anchor axes are now %s for %s." % (
            "visible" if is_visible else "hidden", obj.Label)

    def use_selected_component(self):
        selected = Gui.Selection.getSelection()
        if not selected:
            raise ValueError("Select a component in the 3D view or model tree.")
        obj = selected[0]
        if not getattr(obj, "OTD_Smart", False):
            raise ValueError("The selected object is not an imported/annotated smart component.")
        self.refresh_lists()
        self._select_combo_object(self.component_combo, obj)
        surface = getattr(obj, "OTD_AttachedTo", None)
        if surface:
            self._select_combo_object(self.surface_combo, surface)
        return "Selected component: %s" % obj.Label

    def _sync_after_position(self, doc):
        _refresh_children(doc)
        if self.auto_retrace.isChecked():
            ow = optics_module()
            if ow is not None:
                try:
                    ow.restartAll()
                except Exception as exc:
                    say("Automatic ray retracing could not complete: %s" % exc)

    def _apply_current_position(self, operation="Place / move optical component",
                                offset_x=None, offset_y=None):
        comp = self._resolve(self.component_combo)
        surf = self._resolve(self.surface_combo)
        doc = comp.Document
        if surf.Document is not doc:
            raise ValueError("Component and mounting surface must be in the same document.")
        doc.openTransaction(operation)
        try:
            lift = lift_for_reference_height(comp, self.height.value(), surf)
            pos = _place_at(comp, surf, self.col_spin.value(), self.row_spin.value(),
                            lift, self.rotation.value(), offset_x, offset_y)
            self._sync_after_position(doc)
            doc.commitTransaction()
        except Exception:
            doc.abortTransaction()
            raise
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(doc.Name, comp.Name)
        return "%s: reference height %.2f mm above %s; placed at (%.2f, %.2f, %.2f)." % (
            comp.Label, self.height.value(), surf.Label, pos.x, pos.y, pos.z)

    def nudge_component(self, dx, dy):
        comp = self._resolve(self.component_combo)
        surf = self._resolve(self.surface_combo)
        # If changing the selected surface, start with the UI-selected hole
        # and discard fine offsets inherited from the old surface.
        same = getattr(comp, "OTD_AttachedTo", None) is surf
        ox = comp.OTD_OffsetX.Value if same else 0.0
        oy = comp.OTD_OffsetY.Value if same else 0.0
        if self.step_combo.currentIndex() == 0:
            i = self.col_spin.value() + dx
            j = self.row_spin.value() + dy
            if not (0 <= i < surf.OTD_Columns and 0 <= j < surf.OTD_Rows):
                raise ValueError("Next hole is outside the selected mounting grid.")
        else:
            step = {1: 1.0, 2: 5.0, 3: 10.0}.get(
                self.step_combo.currentIndex(), self.custom_step.value())
            x = self.col_spin.value() * surf.OTD_PitchX.Value + ox + dx * step
            y = self.row_spin.value() * surf.OTD_PitchY.Value + oy + dy * step
            i, ox = _normalize_axis_position(x, surf.OTD_PitchX.Value, surf.OTD_Columns)
            j, oy = _normalize_axis_position(y, surf.OTD_PitchY.Value, surf.OTD_Rows)
        self.col_spin.setValue(i)
        self.row_spin.setValue(j)
        return self._apply_current_position("Nudge optical component", ox, oy)

    def nudge_height(self, sign):
        self.height.setValue(self.height.value() + sign * self.height_step.value())
        return self._apply_current_position("Adjust optical height")

    def nudge_angle(self, sign):
        self.rotation.setValue(self.rotation.value() + sign * self.angle_step.value())
        return self._apply_current_position("Rotate optical component")

    def apply_surface_grid(self):
        surf = self._resolve(self.surface_combo)
        edit_grid(surf, self.pitchx.value(), self.pitchy.value(),
                  self.columns.value(), self.rows.value())
        self.col_spin.setMaximum(surf.OTD_Columns-1)
        self.row_spin.setMaximum(surf.OTD_Rows-1)
        return "Updated %s grid. Rebuild markers to see changes." % surf.Label

    def set_grid_origin(self):
        surf = self._resolve(self.surface_combo)
        obj, edge, _ = active_pick()
        if obj is not surf:
            raise ValueError("Select the first-hole circular edge on the active surface.")
        pos = set_grid_first_hole_from_edge(surf, edge)
        _refresh_children(surf.Document)
        return "Grid origin moved to actual hole (%.1f, %.1f, %.1f) mm." % (pos.x, pos.y, pos.z)

    def _resolve(self, combo):
        doc = App.ActiveDocument
        name = combo.currentData()
        if not doc or not name:
            raise ValueError("Create/import parts and choose an entry from the menu first.")
        obj = doc.getObject(name)
        if obj is None:
            raise ValueError("Object no longer exists. Click Refresh object lists.")
        return obj

    def create_demo(self):
        doc = demo_table()
        self.refresh_lists()
        return "Created %s. Use the Mount tab to reposition the sample mirror." % doc.Label

    def insert_breadboard(self):
        # This is an independent command: never creates or replaces a document.
        doc = _getdoc()
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Add breadboard to current optical table")
        form = QtWidgets.QFormLayout(dialog)
        label = QtWidgets.QLineEdit("Breadboard")
        form.addRow("Name", label)
        inputs = {}
        values = [
            ("Width X", "width", 200.0, 1.0, 10000.0),
            ("Depth Y", "depth", 150.0, 1.0, 10000.0),
            ("Thickness", "thickness", 12.0, 0.1, 1000.0),
            ("Corner X (world)", "x", 270.0, -100000, 100000),
            ("Corner Y (world)", "y", 160.0, -100000, 100000),
            ("Top Z (world)", "top_z", 100.0, -100000, 100000),
            ("Grid hole pitch", "pitch", 25.0, 0.1, 1000.0),
        ]
        for display, key, value, minimum, maximum in values:
            w = self._spin(value, minimum, maximum, 2, " mm")
            form.addRow(display, w)
            inputs[key] = w
        note = QtWidgets.QLabel(
            "The plate is placed with its lower-left XY corner at X/Y and its TOP face at Z. "
            "This adds a new breadboard; it does not clear other parts. "
            "The grid is a placement guide, not drilled holes.")
        note.setWordWrap(True)
        form.addRow(note)
        standard = getattr(QtWidgets.QDialogButtonBox, "StandardButton", QtWidgets.QDialogButtonBox)
        controls = QtWidgets.QDialogButtonBox(standard.Ok | standard.Cancel)
        controls.accepted.connect(dialog.accept)
        controls.rejected.connect(dialog.reject)
        form.addRow(controls)
        run = dialog.exec_ if hasattr(dialog, "exec_") else dialog.exec
        dialog_code = getattr(QtWidgets.QDialog, "DialogCode", QtWidgets.QDialog)
        if run() != dialog_code.Accepted:
            return None
        opts = {key: widget.value() for key, widget in inputs.items()}
        doc.openTransaction("Add optical breadboard")
        try:
            board = add_breadboard(doc, label.text(), **opts)
            doc.commitTransaction()
        except Exception:
            doc.abortTransaction()
            raise
        self.refresh_lists()
        self._select_combo_object(self.surface_combo, board)
        Gui.activeDocument().activeView().fitAll()
        return "Added %s (%.0f x %.0f mm, top z %.1f mm). Existing parts were unchanged." % (
            board.Label, opts["width"], opts["depth"], opts["top_z"])

    def import_step(self):
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Import Thorlabs or other STEP model", "",
            "STEP CAD files (*.step *.stp *.STEP *.STP)")
        if not filename:
            return None
        obj = make_imported_step(_getdoc(), filename)
        self.refresh_lists()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(obj.Document.Name, obj.Name)
        Gui.activeDocument().activeView().fitAll()
        return "Imported %s. Its mount anchor defaults to the bottom center; you can redefine it." % obj.Label

    def replace_step(self):
        old = self._resolve(self.component_combo)
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Choose replacement STEP model for " + old.Label, "",
            "STEP CAD files (*.step *.stp *.STEP *.STP)")
        if not filename:
            return None
        doc = old.Document
        doc.openTransaction("Replace optical component")
        try:
            new, old_label = replace_component(old, filename)
            _refresh_children(doc)
            doc.commitTransaction()
        except Exception:
            doc.abortTransaction()
            raise
        self.refresh_lists()
        for idx in range(self.component_combo.count()):
            if self.component_combo.itemData(idx) == new.Name:
                self.component_combo.setCurrentIndex(idx)
                break
        return "Replaced %s with %s. Re-annotate its optical faces if needed." % (old_label, new.Label)

    def set_anchor(self):
        obj, shape, _ = active_pick()
        center = mark_anchor(obj, shape, self.reverse_normal.isChecked())
        attached = getattr(obj, "OTD_AttachedTo", None)
        if attached is not None:
            _place_at(obj, attached, obj.OTD_HoleI, obj.OTD_HoleJ,
                      obj.OTD_Lift.Value, obj.OTD_Angle.Value)
            _refresh_children(obj.Document)
        self.refresh_lists()
        self._select_combo_object(self.component_combo, obj)
        return "%s mount anchor set at (%.1f, %.1f, %.1f) mm." % (
            obj.Label, center.x, center.y, center.z)

    def register_selected_surface(self):
        obj, face, _ = active_pick()
        cols, rows = register_surface(obj, face, self.surface_pitch.value(),
                                      self.surface_pitch.value())
        self.refresh_lists()
        return "%s registered with suggested %d × %d hole grid; verify grid origin and counts." % (
            obj.Label, cols, rows)

    def register_auto_surface(self):
        selection = Gui.Selection.getSelection()
        if not selection:
            raise ValueError("Select an object in the 3D view or model tree first.")
        obj = selection[0]
        face = choose_top_face(obj)
        cols, rows = register_surface(obj, face, self.surface_pitch.value(),
                                      self.surface_pitch.value())
        self.refresh_lists()
        return "%s: suggested top face with %d × %d grid. Verify it before mounting." % (
            obj.Label, cols, rows)

    def select_clicked_hole(self):
        surface = self._resolve(self.surface_combo)
        picked_obj, shape, point = active_pick()
        if picked_obj is not surface or shape.ShapeType != "Face":
            raise ValueError("Click a face of the active mounting surface in the 3D viewer.")
        if point is None:
            raise ValueError("No picked 3D point. Enter hole indices manually.")
        frame = surface.Placement.multiply(surface.OTD_GridFrame)
        local = frame.inverse().multVec(point)
        i = int(round(local.x / surface.OTD_PitchX.Value))
        j = int(round(local.y / surface.OTD_PitchY.Value))
        i = max(0, min(surface.OTD_Columns - 1, i))
        j = max(0, min(surface.OTD_Rows - 1, j))
        self.col_spin.setValue(i)
        self.row_spin.setValue(j)
        return "Nearest hole selected: column %d, row %d. Click Apply to place the part." % (i, j)

    def place_component(self):
        comp = self._resolve(self.component_combo)
        surf = self._resolve(self.surface_combo)
        same = getattr(comp, "OTD_AttachedTo", None) is surf
        x = comp.OTD_OffsetX.Value if same else 0.0
        y = comp.OTD_OffsetY.Value if same else 0.0
        return self._apply_current_position("Place optical component", x, y)

    def build_markers(self):
        surface = self._resolve(self.surface_combo)
        make_grid_markers(surface)
        return "Showing hole centers for %s (visual overlay, not drilled bores)." % surface.Label

    def update_placements(self):
        doc = _getdoc()
        count = update_mounted(doc)
        if self.auto_retrace.isChecked() and optics_module() is not None:
            try:
                optics_module().restartAll()
            except Exception as exc:
                say("Beam retracing failed: %s" % exc)
        return "Updated %d mounted components and synchronized optical geometry." % count

    def add_optics_ref(self):
        obj, sub, _ = active_pick()
        kind = self.optic_type.currentText()
        center, normal = mark_optical_reference(obj, sub, kind, self.aperture.value())
        self.refresh_lists()
        return "%s: %s reference marked at local position (%.1f, %.1f, %.1f)." % (
            obj.Label, kind, center.x, center.y, center.z)

    def add_mirror(self):
        owner = None
        sels = Gui.Selection.getSelection()
        for obj in sels:
            if getattr(obj, "OTD_Type", "") == "Mirror":
                owner = obj
                break
        if owner is None:
            owner = self._resolve(self.component_combo)
        optical = register_mirror_with_optics(owner)
        return "Mirror active for %s (%s)." % (owner.Label, optical.Label)

    def add_ray(self):
        doc = _getdoc()
        pos = V(*(self.beam_fields[k].value() for k in ("X", "Y", "Z")))
        direction = V(*(self.beam_fields[k].value() for k in ("dx", "dy", "dz")))
        doc.openTransaction("Create / update laser")
        try:
            _refresh_children(doc)
            ray, updated = create_or_update_laser(doc, pos, direction,
                                                   self.wavelength.value())
            doc.commitTransaction()
        except Exception:
            doc.abortTransaction()
            raise
        return ("Updated" if updated else "Created") + " laser beam %s; previous beam not duplicated." % ray.Label

    def load_laser(self):
        doc = _getdoc()
        ray = next((obj for obj in doc.Objects
                    if getattr(obj, "OTD_ManagedLaser", False)
                    or (obj.Label.startswith("Laser ") and hasattr(obj, "Power"))), None)
        if ray is None:
            raise ValueError("No saved laser ray found in this document.")
        base = ray.Placement.Base
        direction = ray.Placement.Rotation.multVec(V(1, 0, 0))
        for key, value in zip(("X", "Y", "Z", "dx", "dy", "dz"),
                              (base.x, base.y, base.z,
                               direction.x, direction.y, direction.z)):
            self.beam_fields[key].setValue(value)
        self.wavelength.setValue(ray.Wavelength)
        return "Loaded laser position, direction and wavelength from %s." % ray.Label

    def retrace(self):
        doc = _getdoc()
        _refresh_children(doc)
        OW = optics_module()
        if OW is None:
            return "Placement refreshed. Install Optics Workbench to trace optical rays."
        OW.restartAll()
        return "Updated mirrors and retraced optical beams."

    def change_view(self, method):
        if not Gui.activeDocument():
            raise ValueError("Open a document first.")
        view = Gui.activeDocument().activeView()
        getattr(view, method)()
        view.fitAll()
        return "Changed view to %s." % method

    def fit_view(self):
        Gui.activeDocument().activeView().fitAll()
        return "View fitted to geometry."

    def save_as(self):
        doc = _getdoc()
        default = (doc.Label or "OpticalTable") + ".FCStd"
        filename, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save optical table configuration", default, "FreeCAD files (*.FCStd)")
        if not filename:
            return None
        if not filename.lower().endswith(".fcstd"):
            filename += ".FCStd"
        doc.recompute()
        doc.saveAs(filename)
        return "Saved configuration to %s" % filename


class OpenPanelCommand:
    def GetResources(self):
        return {"MenuText": "Open Optical Table Designer",
                "ToolTip": "Show optical component and mounting-surface controls"}

    def Activated(self):
        show_panel()

    def IsActive(self):
        return True


def show_panel():
    global _PANEL
    mw = Gui.getMainWindow()
    dock = mw.findChild(QtWidgets.QDockWidget, "OTD_MainDock")
    if dock is None:
        dock = QtWidgets.QDockWidget("Optical Table Designer", mw)
        dock.setObjectName("OTD_MainDock")
        dock.setMinimumWidth(335)
        panel = OpticalTablePanel(dock)
        dock.setWidget(panel)
        area = getattr(QtCore.Qt, "RightDockWidgetArea", None)
        if area is None:
            area = QtCore.Qt.DockWidgetArea.RightDockWidgetArea
        mw.addDockWidget(area, dock)
        _PANEL = panel
    else:
        _PANEL = dock.widget()
        _PANEL.refresh_lists()
    dock.show()
    dock.raise_()
    return dock
