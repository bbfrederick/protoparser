"""Recompute a protocol's slice array from the quantities it is derived from.

The geometry cards present as a cascade -- ``Slices``, ``Slice Thickness``,
``Distance Factor``, ``Position``, ``Orientation`` and ``Rotation`` each move
six to twelve ``sSliceArray.asSlice[]`` fields -- but none of those fields is
independent. Every slice is placed by::

    position[i] = centre + normal * ((i - (n - 1) / 2) * step)
    step        = thickness * (1 + distance factor)

with ``sNormal``, ``dThickness`` and ``dInPlaneRot`` identical on every
element. That is why this module exists rather than more mappings: writing
``dThickness`` on its own leaves sixty positions describing the old spacing,
which is an inconsistent parameter set, and the console answers those by
greying the scan out so it cannot even be opened.

Recomputing is also what makes the result *safe* rather than merely tidy. An
array rebuilt from the six inputs is internally consistent by construction; a
partly written one never is, and nothing offline can tell the difference --
only the scanner, with the sequence loaded, decides.

The centre is preserved rather than derived. ``paramcheck/XA60/extravals``
varies the slice count and the spacing and leaves the group centre exactly
where it was, so a rebuild has nothing to carry over; and the printed
``Position`` is not reliable enough to write through, since several
spectroscopy scans print the VoI position instead.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from . import ascconv

#: The three protocol axes, in the order the console writes them.
AXES = ("dSag", "dCor", "dTra")

#: How far a recomputed position may sit from the stored one before the
#: rebuild is treated as disagreeing with the console, in millimetres. The
#: corpus reproduces to about fourteen nanometres, so this is loose by five
#: orders of magnitude and still far inside the printed precision.
TOLERANCE = 1e-3


@dataclass(frozen=True)
class SliceGroup:
    """The quantities a slice array is computed from.

    Attributes
    ----------
    centre : tuple of float
        Group centre in ``(dSag, dCor, dTra)``. Preserved across a rebuild.
    normal : tuple of float
        Unit normal to the slices, shared by every element.
    thickness : float
        Slice thickness in millimetres, shared by every element.
    distance_factor : float
        Gap between slices as a fraction of the thickness. Zero when the
        assignment is absent, which is how the console writes no gap.
    count : int
        Number of slices.
    in_plane_rotation : float
        Rotation within the slice plane, in radians.
    """

    centre: tuple[float, float, float]
    normal: tuple[float, float, float]
    thickness: float
    distance_factor: float
    count: int
    in_plane_rotation: float

    @property
    def step(self) -> float:
        """Return the centre-to-centre distance between neighbouring slices.

        Returns
        -------
        float
            ``thickness * (1 + distance factor)``.
        """
        return self.thickness * (1.0 + self.distance_factor)

    @property
    def extent(self) -> float:
        """Return the slab thickness the printout shows, edge to edge.

        The obvious ``count * step`` is wrong in a way that hides: it agrees
        on four of the five ``extravals`` copies and misses the fifth by a
        millimetre, which reads as rounding. The printed figure runs from the
        outer face of the first slice to the outer face of the last.

        Returns
        -------
        float
            ``(count - 1) * step + thickness``, in millimetres.
        """
        return (self.count - 1) * self.step + self.thickness

    def position(self, index: int) -> tuple[float, float, float]:
        """Return the centre of one slice.

        Parameters
        ----------
        index : int
            Slice index, from zero.

        Returns
        -------
        tuple of float
            The slice centre in ``(dSag, dCor, dTra)``.
        """
        offset = (index - (self.count - 1) / 2.0) * self.step
        return tuple(self.centre[n] + self.normal[n] * offset for n in range(3))


def _number(text: str, key: str, fallback: float | None = None) -> float | None:
    """Read one ASCCONV assignment as a float.

    Parameters
    ----------
    text : str
        The XProtocol text.
    key : str
        The assignment to read.
    fallback : float or None
        Returned when the assignment is absent, which for a sparse field is
        a value rather than a gap.

    Returns
    -------
    float or None
        The number, or ``fallback``.
    """
    found = ascconv.read_ascconv(text, key)
    return float(found) if found is not None else fallback


def read_group(text: str) -> SliceGroup | None:
    """Read the quantities the slice array is computed from.

    Parameters
    ----------
    text : str
        The XProtocol text.

    Returns
    -------
    SliceGroup or None
        The group, or ``None`` when the protocol has no usable slice array --
        fewer than two slices leaves the step unobservable, and a second
        slice group is a shape the corpus has never varied.
    """
    count = _number(text, "sSliceArray.lSize")
    thickness = _number(text, "sSliceArray.asSlice[0].dThickness")
    if not count or count < 2 or not thickness:
        return None
    if ascconv.read_ascconv(text, "sGroupArray.asGroup[1].nSize") is not None:
        return None
    positions = [
        [_number(text, f"sSliceArray.asSlice[{n}].sPosition.{a}", 0.0) for a in AXES]
        for n in range(int(count))
    ]
    centre = tuple(sum(p[n] for p in positions) / len(positions) for n in range(3))
    return SliceGroup(
        centre=centre,  # type: ignore[arg-type]
        normal=tuple(  # type: ignore[arg-type]
            _number(text, f"sSliceArray.asSlice[0].sNormal.{a}", 0.0) for a in AXES
        ),
        thickness=thickness,
        distance_factor=_number(text, "sGroupArray.asGroup[0].dDistFact", 0.0) or 0.0,
        count=int(count),
        in_plane_rotation=_number(text, "sSliceArray.asSlice[0].dInPlaneRot", 0.0) or 0.0,
    )


def agrees(text: str, group: SliceGroup | None = None) -> float | None:
    """Return how far the stored array sits from the recomputed one.

    Parameters
    ----------
    text : str
        The XProtocol text.
    group : SliceGroup or None
        The group to compute from. Read from ``text`` when omitted.

    Returns
    -------
    float or None
        The worst distance in millimetres over every slice, or ``None`` when
        the protocol has no usable slice array.
    """
    group = group or read_group(text)
    if group is None:
        return None
    worst = 0.0
    for index in range(group.count):
        stored = [_number(text, f"sSliceArray.asSlice[{index}].sPosition.{a}", 0.0) for a in AXES]
        worst = max(worst, math.dist(group.position(index), stored))
    return worst


def rebuild(text: str, group: SliceGroup) -> str:
    """Write every slice position, normal and thickness from ``group``.

    Only assignments the protocol already carries are written. Changing the
    slice *count* would mean creating array elements -- and the acquisition
    order, ascending-index and position tables beside them -- so that is out
    of scope here and :func:`read_group` reports the count rather than
    accepting a new one.

    Parameters
    ----------
    text : str
        The XProtocol text.
    group : SliceGroup
        The quantities to place the slices from.

    Returns
    -------
    str
        The text with the slice array rewritten.
    """
    for index in range(group.count):
        position = group.position(index)
        for axis, value in zip(AXES, position):
            key = f"sSliceArray.asSlice[{index}].sPosition.{axis}"
            existing = ascconv.read_ascconv(text, key)
            if existing is None:
                # The console omits an axis holding zero; writing one back is
                # only right when the value is no longer zero.
                if abs(value) < TOLERANCE:
                    continue
                text = ascconv.insert_ascconv(text, key, ascconv.format_like(value, "0.0"))
                continue
            if abs(float(existing) - value) < TOLERANCE:
                # Already right. Rewriting it would replace the console's
                # literal with an equivalent one -- at the centre of an
                # array the console writes 5.55e-17 where recomputing
                # gives 8.81e-19, both of them zero -- and a rebuild that
                # edits a protocol nobody changed makes every later diff
                # useless.
                continue
            text = ascconv.store_ascconv(
                text, key, ascconv.format_like(value, existing), existing, False
            )
        for key, value in ((f"sSliceArray.asSlice[{index}].dThickness", group.thickness),):
            existing = ascconv.read_ascconv(text, key)
            if existing is not None and abs(float(existing) - value) >= TOLERANCE:
                text = ascconv.store_ascconv(
                    text, key, ascconv.format_like(value, existing), existing, False
                )
    return text


def recentre(before: str, after: str) -> str:
    """Replace the slice array when a write has invalidated it.

    ``Slice Thickness`` and ``Distance Factor`` both set the *spacing* between
    slices, and every ``sSliceArray.asSlice[]`` position is a function of it,
    so writing either one alone leaves every position describing the geometry
    that was replaced. The console recomputes; a patcher does not, and the
    result is an array that still loads -- a scanner returned one 3.15 mm out
    without complaint -- while describing no coherent slice group.

    Only an array this write broke is rebuilt. One that arrived disagreeing
    with its own inputs is left exactly as it was, because repairing it would
    be a change nothing asked for, and a multi-group array is skipped outright
    since :func:`geometry.read_group` refuses to describe one.

    Parameters
    ----------
    before : str
        The XProtocol text as the template held it.
    after : str
        The same text after this scan's values were written.

    Returns
    -------
    str
        ``after``, with the slice positions recomputed when they need to be.
    """
    if before == after:
        return after
    was = agrees(before)
    if was is None or was >= TOLERANCE:
        return after
    group = read_group(after)
    if group is None:
        return after
    now = agrees(after, group)
    if now is None or now < TOLERANCE:
        return after
    return rebuild(after, group)


def problems(text: str) -> list[str]:
    """Check slice-array invariants supported by the XA60 corpus.

    Multi-group positions are deliberately not checked against the single-group
    formula. Sparse zero coordinates are valid; missing slice thicknesses are not.

    Parameters
    ----------
    text : str
        XProtocol text to inspect without modifying it.

    Returns
    -------
    list of str
        Broken numeric, array-shape, or single-group geometry invariants.
    """
    from .inspect import ascconv_table

    table = ascconv_table(text)
    if "sSliceArray.lSize" not in table:
        return []
    numeric = {
        k: v
        for k, v in table.items()
        if k.startswith(("sSliceArray.", "sGroupArray."))
        and re.search(
            r"(?:lSize|nSize|dThickness|dDistFact|dInPlaneRot|dReadoutFOV|dPhaseFOV|dSag|dCor|dTra)$",
            k,
        )
    }
    try:
        numbers = {k: float(v) for k, v in numeric.items()}
    except ValueError:
        return ["slice geometry contains a nonnumeric assignment"]
    if any(not math.isfinite(v) for v in numbers.values()):
        return ["slice geometry contains a nonfinite assignment"]
    count = numbers["sSliceArray.lSize"]
    if count < 1 or not count.is_integer():
        return ["sSliceArray.lSize must be a positive integer"]
    indices = sorted(
        int(m.group(1))
        for key in table
        if (m := re.fullmatch(r"sSliceArray\.asSlice\[(\d+)\]\.dThickness", key))
    )
    if len(indices) != int(count) or any(i != n for n, i in enumerate(indices)):
        return ["slice thickness indices do not cover sSliceArray.lSize"]
    if any(
        int(m.group(1)) >= count
        for key in table
        if (m := re.match(r"sSliceArray\.asSlice\[(\d+)\]", key))
    ):
        return ["slice assignment index is outside sSliceArray.lSize"]
    found = []
    for key, value in numbers.items():
        if key.endswith(("dThickness", "dReadoutFOV", "dPhaseFOV")) and value <= 0:
            found.append(f"{key} must be positive")
        if key.endswith("dDistFact") and value <= -1:
            found.append(f"{key} gives nonpositive slice spacing")
    for index in indices:
        normal = tuple(numbers.get(f"sSliceArray.asSlice[{index}].sNormal.{a}", 0) for a in AXES)
        if not math.isclose(math.sqrt(sum(n * n for n in normal)), 1, abs_tol=1e-6):
            found.append(f"slice {index} normal is not a unit vector")
    if found:
        return found
    if "sGroupArray.asGroup[1].nSize" not in table and count > 1:
        stored_positions = [
            tuple(numbers.get(f"sSliceArray.asSlice[{i}].sPosition.{a}", 0) for a in AXES)
            for i in indices
        ]
        group = SliceGroup(
            centre=tuple(sum(p[n] for p in stored_positions) / count for n in range(3)),
            normal=tuple(numbers.get(f"sSliceArray.asSlice[0].sNormal.{a}", 0) for a in AXES),
            thickness=numbers["sSliceArray.asSlice[0].dThickness"],
            distance_factor=numbers.get("sGroupArray.asGroup[0].dDistFact", 0),
            count=int(count),
            in_plane_rotation=numbers.get("sSliceArray.asSlice[0].dInPlaneRot", 0),
        )
        for index in indices:
            prefix = f"sSliceArray.asSlice[{index}]"
            if not math.isclose(
                numbers[prefix + ".dThickness"], group.thickness, abs_tol=TOLERANCE
            ):
                found.append(f"slice {index} thickness differs within a single group")
            normal = tuple(numbers.get(f"{prefix}.sNormal.{a}", 0) for a in AXES)
            if math.dist(normal, group.normal) > 1e-6:
                found.append(f"slice {index} normal differs within a single group")
            rotation = numbers.get(prefix + ".dInPlaneRot", 0)
            if not math.isclose(rotation, group.in_plane_rotation, abs_tol=1e-6):
                found.append(f"slice {index} rotation differs within a single group")
        distance = max(math.dist(group.position(i), p) for i, p in enumerate(stored_positions))
        if distance >= TOLERANCE:
            found.append(f"slice positions disagree with spacing by {distance:.6g} mm")
    return found
