"""Recover embedded free-diffusion tables without changing the archive.

The XA60 corpus stores these in the standard sDiffusion ASCCONV structure;
populated examples currently run CMRR/HCP customer sequences. Arrays are
sparse: an absent component is zero, including every
component of a b0 vector. These are prescribed vectors, not image-space
bvecs or a reconstruction of the acquired volume order.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

from .archive import Archive, Protocol
from .inspect import ascconv_table

_ROOT = "sDiffusion.sFreeDiffusionData."
_VECTOR = re.compile(re.escape(_ROOT) + r"asDiffDirVector\[(\d+)\]\.d(Sag|Cor|Tra)$")
_COMMENT = re.compile(re.escape(_ROOT) + r"sComment(?:\.(\d+)|\[(\d+)\])$")

# Established against the original ABCD 102 and Lifespan 107 DVS files and
# their exact component-for-component matches in XA60 archives. See docs/diffusion.md.
COORDINATE_SYSTEMS = {1: "xyz", 2: "prs"}
NORMALIZATIONS = {1: "none"}


@dataclass(frozen=True)
class DiffusionTable:
    """Stored components in dSag, dCor, dTra order, with original enum codes.

    Characterized conventions are exposed as properties; unknown codes give
    None rather than a guessed convention. ``comment`` sometimes contains the source filename,
    but is user text, not a guaranteed filename or an output path.
    """

    vectors: tuple[tuple[float, float, float], ...]
    coordinate_system_code: int | None
    normalization_code: int | None
    comment: str
    diffusion_mode_code: int | None

    @property
    def coordinate_system(self) -> str | None:
        """Return the characterized DVS coordinate convention, if known."""
        return COORDINATE_SYSTEMS.get(self.coordinate_system_code)

    @property
    def normalization(self) -> str | None:
        """Return the characterized DVS normalization convention, if known."""
        return NORMALIZATIONS.get(self.normalization_code)

    def to_dvs(
        self, *, coordinate_system: str | None = None, normalization: str | None = None
    ) -> str:
        """Serialize using characterized stored conventions or explicit unknown-code mappings.

        No rotation, scaling, normalization, sorting or deduplication is
        performed. Unknown codes require the original file's conventions.
        Explicit values conflicting with characterized codes are refused.
        """
        for label, supplied, known in (
            ("coordinate_system", coordinate_system, self.coordinate_system),
            ("normalization", normalization, self.normalization),
        ):
            if supplied is not None and known is not None and supplied != known:
                raise ValueError(
                    f"{label}={supplied!r} conflicts with stored convention {known!r}"
                )
        coordinate_system = coordinate_system or self.coordinate_system
        normalization = normalization or self.normalization
        if coordinate_system is None:
            raise ValueError(
                f"unknown coordinate system code {self.coordinate_system_code}; "
                "supply coordinate_system from the original DVS"
            )
        if normalization is None:
            raise ValueError(
                f"unknown normalization code {self.normalization_code}; "
                "supply normalization from the original DVS"
            )
        if coordinate_system not in {"xyz", "prs"}:
            raise ValueError("coordinate_system must be 'xyz' or 'prs'")
        if normalization not in {"none", "unity", "maximum"}:
            raise ValueError("normalization must be 'none', 'unity' or 'maximum'")
        lines = [
            "# Extracted from an exar1 embedded free-diffusion table",
            f"# Stored coordinate code: {self.coordinate_system_code}; "
            f"normalization code: {self.normalization_code}",
            f"[directions={len(self.vectors)}]",
            f"CoordinateSystem = {coordinate_system}",
            f"Normalisation = {normalization}",
        ]
        # Keep user text in comments so delimiters/newlines cannot inject DVS directives.
        lines.extend(f"# Stored comment: {line}" for line in self.comment.splitlines())
        lines.extend(
            f"Vector[{i}] = ({x!r}, {y!r}, {z!r})" for i, (x, y, z) in enumerate(self.vectors)
        )
        return "\n".join(lines) + "\n"

    def write_dvs(
        self,
        path: str | Path,
        *,
        coordinate_system: str | None = None,
        normalization: str | None = None,
        overwrite: bool = False,
    ) -> None:
        """Write a DVS file, refusing replacement unless explicitly requested."""
        text = self.to_dvs(coordinate_system=coordinate_system, normalization=normalization)
        with open(path, "w" if overwrite else "x", encoding="utf-8", newline="\r\n") as stream:
            stream.write(text)


@dataclass(frozen=True)
class DiffusionScan:
    """One populated table at the archive head, with an address and identity."""

    path: tuple[str, ...]
    protocol_id: str
    table: DiffusionTable


def extract_diffusion(protocol: Protocol) -> DiffusionTable | None:
    """Read a scan's embedded table, or return None when it has none.

    Reject inconsistent counts, out-of-range indices and invalid numbers.
    Missing vector components within the declared array are stored zeros.
    A populated table may remain in a scan using another diffusion mode;
    ``diffusion_mode_code`` records that setting without asserting use.
    """
    values = ascconv_table(protocol.xprotocol)

    def integer(key: str) -> int | None:
        value = values.get(key)
        if value is None:
            return None
        try:
            literal = value.split("#", 1)[0].strip()
            return int(literal, 16 if literal.lower().startswith("0x") else 10)
        except ValueError as exc:
            raise ValueError(f"invalid integer for {key}: {value}") from exc

    size = integer(_ROOT + "asDiffDirVector.__attribute__.size")
    count = integer(_ROOT + "lDiffDirections")
    components = [(m, v) for k, v in values.items() if (m := _VECTOR.fullmatch(k))]
    if size in (None, 0) and count in (None, 0) and not components:
        return None
    if size is None or size <= 0 or count != size:
        raise ValueError(
            f"inconsistent free-diffusion counts: array size={size}, directions={count}"
        )
    # Populate only after checking all indices; never infer length from nonzero entries.
    sparse: dict[tuple[int, int], float] = {}
    for match, literal in components:
        index = int(match[1])
        if index >= size:
            raise ValueError(f"free-diffusion vector index {index} is outside array size {size}")
        try:
            value = float(literal.split("#", 1)[0].strip())
        except ValueError as exc:
            raise ValueError(f"invalid free-diffusion component: {literal}") from exc
        if not math.isfinite(value):
            raise ValueError(f"nonfinite free-diffusion component: {literal}")
        sparse[index, ("Sag", "Cor", "Tra").index(match[2])] = value
    comment_bytes = {}
    for key in values:
        if match := _COMMENT.fullmatch(key):
            value = integer(key)
            if value is None or not 0 <= value <= 255:
                raise ValueError(f"invalid free-diffusion comment byte: {values[key]}")
            comment_bytes[int(match[1] or match[2])] = value
    comment = bytes(comment_bytes.get(i, 0) for i in range(max(comment_bytes, default=-1) + 1))
    return DiffusionTable(
        vectors=tuple(tuple(sparse.get((i, axis), 0.0) for axis in range(3)) for i in range(size)),
        coordinate_system_code=integer(_ROOT + "ulCoordinateSystem"),
        normalization_code=integer(_ROOT + "ulNormalization"),
        comment=comment.split(b"\0", 1)[0].decode("utf-8", errors="replace"),
        diffusion_mode_code=integer("sDiffusion.ulMode"),
    )


def find_diffusion(archive: Archive) -> list[DiffusionScan]:
    """Find populated tables in all live programs, steps and protocol children."""
    found = []
    for program in archive.programs:
        for step in program.steps:
            for protocol in step.protocols:
                path = (*archive.path_of(program.instance), step.name)
                try:
                    table = extract_diffusion(protocol)
                except ValueError as exc:
                    raise ValueError(f"{' / '.join(path)}: {exc}") from exc
                if table is not None:
                    found.append(DiffusionScan(path, protocol.instance.element_id, table))
    return found
