# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A reviewed script never leaves a mesh whose arrays name elements the mesh does not have: ``after``.

Blender's low-level mesh API (``from_pydata``, ``vertices.add``/``loops.add``/``polygons.add``, ``foreach_set``)
writes the arrays it is given and checks nothing: a face corner or an edge naming a vertex the mesh does not have, a
face whose corners lie outside the corner array. Blender then reads past the arrays at the next evaluation, modifier
or redraw, which is a crash or wrong memory, never an error a script can catch. A person at the Python console
learns to call ``Mesh.validate()``; a model's script is run as written, and the executor evaluates the scene
straight after it (competitor triage row B4: every such script "succeeded" and left the mesh as written).

``after`` runs between the script's last statement and anything that evaluates the scene, whether the script
returned or raised, and asks Blender itself: ``Mesh.validate()`` is the check and the repair in one (it walks the
arrays in C++, changes nothing in a sound mesh and says whether it had to correct this one: it removes the faces
and edges that name missing elements and rebuilds edges that are only missing).

Which meshes: every one the script could have written, whatever its size, and no other. Blender says which
(``Mesh.is_topology_sealed``, ``BKE_mesh_types.hh``: ``MeshTopologySeal``). ``Mesh.topology_seal()`` records the
identity and write version of the mesh's edge, face-corner and face-offset arrays and its element counts; any
access that could write one of them copies it or raises its version, and replacing or resizing one changes its
identity, so the seal holds exactly while nothing could have written the topology. ``after`` validates each mesh
whose seal is broken or was never set (a new mesh, a copy, one just read from a file, one a script wrote or only
read through RNA, which hands out writable arrays) and seals it once ``validate`` finds it sound. A mesh whose
seal holds is as Blender last found it, and costs nothing: the seals of the whole file are read in one call.
Angelo's own readers of mesh arrays leave the seals as they found them (``reading``): the scene signature reads
every scene mesh's coordinates around each script and the revision read-back reads the arrays of the objects a
script changed, right after this check; without it every such mesh was scanned or validated a second time by
the next script.

Two earlier shapes, and why not. The first compared element counts, validated the meshes whose counts had changed
and gave the others five milliseconds in all per script, in rotation: a mesh whose own check cost more than that
was passed over on every call, so an in-place rewrite of it stayed, unsaid (external audit, round 2, F1). The
second validated every mesh after every script: sound, and 36 to 175 ms on every call in large scenes, paid by
scripts that touched no mesh. In a Blender without the seal (an older build under these scripts) that second
shape is what still runs: never fewer checks than can be proven enough. A mesh that could not be checked at all
is never silent (``unchecked``, in the tool's result) and is not sealed; nor is one ``validate`` corrected twice.

Positions have a seal of their own (``Mesh.is_geometry_sealed``: the topology seal and the position array
together). ``validate`` does one thing for positions, it sets a coordinate that is not a finite number to zero,
so a mesh whose positions alone were written (its topology seal holds, its geometry seal does not: a script that
moves vertices) is not validated: its coordinates are read once and scanned, about a tenth of ``validate``'s
cost, and only a mesh that has such a value is validated, repaired and reported. A scan that cannot be made is a
validation.

Linked meshes and meshes in Edit Mode are validated too: a script can write their arrays as well, and ``validate``
leaves a sound one as it is. The result says what was repaired (``attach``: ``meshes_repaired``, one row per mesh
with its counts before and after, and a sentence for the model) and what could not be checked
(``meshes_unchecked``), unless the failed script's restore put the file back as it was. Only meshes: curves, point
clouds and Grease Pencil have no ``validate`` to ask.

No ``bpy`` import; the executor passes it.
"""

import contextlib

MAX_ROWS = 16
SEALED = "is_geometry_sealed"        # Topology and positions as Blender last found them.
TOPOLOGY = "is_topology_sealed"      # Topology alone: with SEALED false, only positions were written.
FOUND = "Mesh.validate() had to correct it"
NOTE = ("The script left mesh arrays Blender cannot read. Angelo repaired each mesh listed with Mesh.validate(), "
        "which removes the faces and edges that name missing elements and sets a coordinate that is not a "
        "finite number to zero: compare its counts before and after, and write valid indices and coordinates if "
        "geometry is missing.")
UNCHECKED = ("Angelo could not check these meshes with Mesh.validate() after the script. If the script wrote their "
             "vertices, edges, corners or faces, call validate() on each before anything else reads them.")
EVERY = "(every mesh)"


def counts(mesh):
    return len(mesh.vertices), len(mesh.edges), len(mesh.loops), len(mesh.polygons)


def _meshes(bpy):
    """The file's meshes; none where there is no such collection (a host test's stand-in ``bpy``)."""
    return getattr(getattr(bpy, "data", None), "meshes", None) or ()


def _labelled(values):
    return dict(zip(("vertices", "edges", "corners", "faces"), values))


def _holds(mesh, seal=SEALED):
    """Whether this mesh's seal holds. False where it cannot be read, and in a Blender that has none."""
    try:
        return bool(getattr(mesh, seal, False))
    except Exception:   # noqa: BLE001 - an unreadable seal costs a check, never a miss.
        return False


def _finite(mesh):
    """Whether every coordinate of the mesh is a finite number, from one read of them all; False where that
    cannot be told (no numpy, no bulk read): then the mesh is validated."""
    try:
        import numpy
        values = numpy.empty(len(mesh.vertices) * 3, dtype=numpy.float32)
        try:   # The position attribute's own array: one copy. A vertex at a time is ten times slower.
            mesh.attributes["position"].data.foreach_get("vector", values)
        except Exception:   # noqa: BLE001 - no such attribute to read here (a stand-in, an older Blender).
            mesh.vertices.foreach_get("co", values)
        return bool(numpy.isfinite(values).all())
    except Exception:   # noqa: BLE001 - a scan that cannot be made is a validation, never a pass.
        return False


def _unsealed(collection, count):
    """The positions of the meshes whose seal does not hold, from one read of the whole collection; None where
    that read is not there to use (then each mesh is asked)."""
    try:
        try:
            import numpy
            holds = numpy.zeros(count, dtype=bool)
            collection.foreach_get(SEALED, holds)
            return numpy.flatnonzero(~holds).tolist()
        except ImportError:
            holds = [False] * count
            collection.foreach_get(SEALED, holds)
            return [index for index, held in enumerate(holds) if not held]
    except Exception:   # noqa: BLE001 - no such property in this Blender, or no bulk read of it.
        return None


def _due(collection):
    """The meshes to look at: those whose geometry seal is broken or was never set. The file's meshes are only
    listed when there is one: a script that touched none costs one read of the seals."""
    positions = None
    if getattr(collection, "foreach_get", None) is not None:
        count = len(collection)
        if not count:
            return []
        positions = _unsealed(collection, count)
        if positions == []:
            return []
    meshes = list(collection)
    if positions is None or (positions and positions[-1] >= len(meshes)):
        return [mesh for mesh in meshes if not _holds(mesh)]
    return [meshes[position] for position in positions]


def _checked(mesh, repaired, unchecked):
    """Validate one mesh: a row in ``repaired`` when Blender had to correct it, one in ``unchecked`` when it could
    not be asked. True when Blender found it sound, at once or after one repair. Never raises: a check that fails
    is not the script's failure, and the script's own outcome is what its result reports; but it is said."""
    try:
        was = counts(mesh)
        # The geometry only (clean_customdata off): a sound mesh's cached layers are not this check's to strip.
        if not mesh.validate(verbose=False, clean_customdata=False):
            return True
        mesh.update()
        row = {"mesh": mesh.name[:128], "found": FOUND, "before": _labelled(was), "after": _labelled(counts(mesh))}
        repaired.append(row)
        if mesh.validate(verbose=False, clean_customdata=False):   # Once more, only when repaired: sound now.
            row["still"] = "Mesh.validate() corrected it a second time"
            return False
        return True
    except Exception as error:   # noqa: BLE001 - see the docstring.
        print("Angelo: a mesh could not be checked after a script:", type(error).__name__)
        unchecked.append({"mesh": str(getattr(mesh, "name", "?"))[:128],
                          "why": "Mesh.validate() raised %s" % type(error).__name__})
        return False


def _seal(mesh, positions=True):
    """Record the mesh as Blender just found it (its topology alone when not ``positions``: they stay marked as
    written). Where that fails, or there is no seal, it is checked again."""
    try:
        seal = getattr(mesh, "topology_seal", None)
        if seal is not None:
            seal() if positions else seal(positions=False)
    except Exception:   # noqa: BLE001 - an unset seal costs a check, never a miss.
        pass


@contextlib.contextmanager
def reading(mesh):
    """For Angelo's own code that only READS a mesh's arrays through RNA: the seals are after the read as they
    were before it. RNA hands a reader writable arrays, so the read itself breaks them; nothing was written, so
    what Blender last found is still true. Both seals when both held; the topology seal alone when only it held
    (positions a script wrote stay marked as written, for the check to scan); nothing when neither did."""
    topology = mesh is not None and _holds(mesh, TOPOLOGY)
    geometry = topology and _holds(mesh)
    try:
        yield
    finally:
        if topology:
            _seal(mesh, positions=geometry)


def after(bpy, prior=None):
    """Validate every mesh whose topology the script could have written, scan the coordinates of every mesh whose
    positions alone it could have written, and seal each one found sound; ``{"repaired": rows, "unchecked":
    rows, "validated": how many were validated, "scanned": how many had their coordinates read}``, also kept on
    ``prior`` (``script_after.Before``) as ``mesh_check`` for ``attach``."""
    repaired, unchecked, validated, scanned = [], [], 0, 0
    try:
        due = _due(_meshes(bpy))
    except Exception as error:   # noqa: BLE001 - as _checked: never the script's failure, and said.
        print("Angelo: the meshes could not be listed after a script:", type(error).__name__)
        due = []
        unchecked.append({"mesh": EVERY, "why": "the file's meshes could not be listed (%s)" % type(error).__name__})
    for mesh in due:
        if _holds(mesh, TOPOLOGY):   # Only its positions could have been written.
            scanned += 1
            if _finite(mesh):
                _seal(mesh)
                continue
        validated += 1
        if _checked(mesh, repaired, unchecked):
            _seal(mesh)
    outcome = {"repaired": repaired, "unchecked": unchecked, "validated": validated, "scanned": scanned}
    if prior is not None:
        prior.mesh_check = outcome
    return outcome


def attach(result, outcome):
    """Say in a script's ``result`` what was repaired and what could not be checked; nothing when the failed
    script's file was put back, or when there is nothing to say."""
    if not outcome or not isinstance(result, dict) or result.get("main_data_restored"):
        return
    for rows, key, note_key, note in ((outcome["repaired"], "meshes_repaired", "mesh_repair", NOTE),
                                      (outcome["unchecked"], "meshes_unchecked", "mesh_unchecked", UNCHECKED)):
        if rows:
            result[key], result[note_key] = rows[:MAX_ROWS], note
            if len(rows) > MAX_ROWS:
                result[key + "_omitted"] = len(rows) - MAX_ROWS
