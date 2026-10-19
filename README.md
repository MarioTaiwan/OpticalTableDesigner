# Optical Table Designer v0.3 — FreeCAD 1.1 / Windows 11

FreeCAD extension for arranging optomechanical STEP parts on an optical table or breadboard, storing optical references, and tracing geometric beams with the optional Optics Workbench add-on.

**v0.3 updates:** moving with arrows now preserves all existing component rotations; the local mounting anchor can be moved independently of the CAD body; one Designer laser ray is created/updated instead of accumulating copies; and a new **Components** tab duplicates models with their mounting/optical metadata.

## Upgrade your existing installation

Your system-wide FreeCAD application is installed at `C:\Program Files\FreeCAD 1.1`.

1. **Save your `.FCStd` files**, and close FreeCAD completely.
2. Extract the entire `OpticalTableDesigner_v0.3_Windows.zip` archive.
3. Right-click **`INSTALL_WINDOWS.bat` → Run as administrator**.
4. Select **`2` — All users**. The workbench is installed to:
   `C:\Program Files\FreeCAD 1.1\Mod\OpticalTableDesigner`.
5. Restart FreeCAD 1.1; select **Optical Table Designer** from the workbench list. Check that its panel says **v0.3**.
6. Open your existing optical-table `.FCStd` project. The installer changes **only the workbench files**, not your projects. There is no need to reimport STEP components.

You can alternatively select option **1 — Current user** (`%APPDATA%\FreeCAD\v1-1\Mod\OpticalTableDesigner`) without administrator privileges. Do not keep separate old installations of this same workbench in both locations; they can conflict.

## 1. Update an existing laser, without duplicate rays

1. Open **Optics**.
2. Enter the beam origin **X/Y/Z** and direction **dx/dy/dz**. Direction is a vector and need not be normalized.
3. Click **Create / update laser ray**. The first click creates one managed ray; subsequent clicks move and rotate that *same* ray, updating the wavelength and recalculating optical paths.
4. Click **Load saved laser coordinates** to read the laser's current placement back into the fields.

The upgrade also recognizes earlier v0.2 Designer rays whose names start with **`Laser `**. If multiple such legacy rays were previously created, it adopts one and removes the extra copies *on the next Create / update action*. **Other Optics Workbench rays with different names are untouched.** Save your `.FCStd` after confirming the beam.

## 2. Move a component without resetting its rotation

1. Select the main table or breadboard in **Mount / Move**, then select the component from the dropdown or FreeCAD model tree.
2. If desired, rotate or flip the object in FreeCAD's native **Transform / Placement** dialog, including full 3D rotations such as a 180° flip.
3. Choose hole indices, **Height above face**, and step size. Use the **arrow keys shown as buttons** to move the component relative to the selected mounting surface.
4. Its **existing 3D rotation is preserved** when moving or raising/lowering it. The **Rotate +/−** controls now apply only an **incremental rotation about the surface normal**, rather than reconstructing the entire orientation from a single yaw.

Component placement is calculated from the **local mounting anchor**. The specified height uses the optical reference center if marked, otherwise the anchor. On flipped/tilted parts, this reference-height calculation uses the actual current orientation.

If you move or rotate a breadboard using native FreeCAD tools, click **Update mounted positions**: attached objects follow the breadboard rigidly, keeping their manually chosen rotations.

## 3. Change the local mounting anchor (which point sits over the hole)

1. Select the component in **Mount / Move** (or click the object in the 3D view and then **Use component selected in 3D view**).
2. Switch to **Setup** and click **Show / hide local anchor axes** to display the orange axis marker at its mounting reference.
3. Set **Anchor X / Y / Z**, or use the adjacent **X± / Y± / Z±** buttons and set the **Anchor nudge step**. These are **component-local coordinates**, not world coordinates.
4. Click **Apply local anchor XYZ**. If the component is already attached to a mounting grid, the STEP body moves so the edited anchor stays exactly at the same hole and anchor elevation. The body's orientation is retained. This is how you choose a different mounting foot or contact point after rotating a mount.
5. Alternatively, click a planar mounting face or circular hole edge on the CAD component and choose **Set selected component's mount anchor**. This defines both the local anchor position and its normal.

Only the annotation frame changes; the original STEP geometry isn't remodeled. The axis marker is visual construction geometry, not part of the laser simulation.

## 4. Duplicate an already imported smart component

1. Open **Components** to view/search all annotated/imported components in the **currently open `.FCStd` document**.
2. Highlight any mirror, lens, collimator or mount.
3. Click **Duplicate highlighted component**. A new FreeCAD object is created with copied STEP geometry, its own mechanical anchor, optical center/normal, aperture and mounting metadata. Mirrors get a **separate** optical proxy; if the source is already registered in Optics Workbench, the copy is registered too.
4. If attached to a table or breadboard, the copy is placed at the **next hole in local X** (when one exists), preserving its orientation and optical height. Otherwise it starts in the source location; move it with the placement arrows.
5. Click **Use highlighted component for placement**, or select the new copy in Mount / Move.

The Setup tab also has **Duplicate current component**, which duplicates the component chosen in Mount / Move. The Components tab lists objects **inside the current FreeCAD document**, not a cross-document catalog yet. To copy components to another optical-table document, use normal FreeCAD file/document tools for now.

## 5. Existing tools

- **Setup:** Create separate demo, add breadboard without resetting model, import/replace STEP, edit local anchors.
- **Mount / Move:** Choose the active mounting surface, snap to nominal holes, move with arrows, set optical height, rotate about selected surface normal.
- **Components:** Search, duplicate, and select parts with saved annotations.
- **Surfaces:** Annotate optical tables/breadboards as mountable surfaces; define their grid origin, pitch and hole counts.
- **Optics:** Annotate mirror/lens/fiber references, register mirrors, edit the laser and retrace rays.
- **Views / Save:** Isometric, top, front, right and Save As.
- **Help:** Step-by-step guide built into the FreeCAD interface.

**Important:** Nominal grids do not check real screw compatibility or collisions. Imported STEP geometry is not automatically interpreted as an optical solid; lenses and fiber axes are positioning references unless explicitly supplied with optical models. The ray tracer does not automatically make every mechanical housing block light.

## Troubleshooting and tests

If FreeCAD shows an error, enable **View → Panels → Report view** and send the complete traceback and the action taken. If a button says that an optics module is missing, install Optics Workbench using FreeCAD **Tools → Addon Manager**, then restart FreeCAD.

The Python code lives at `OpticalTableDesigner/optical_table_designer.py`. Standalone regression checks (no FreeCAD required):

```bat
py -3 -m pytest tests
```

If `pytest` is not installed, run `py -3 tests\test_core.py`. These checks validate the placement math and document-management logic using simulated FreeCAD objects; **v0.3 has not been run in the real FreeCAD 1.1 GUI in this environment**.
