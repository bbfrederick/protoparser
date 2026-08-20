"""Version profiles: the only place software-version differences live.

A profile declares what differs between Siemens releases -- how to recognize
the version, the grammar of the header summary line, and any layout
thresholds that need nudging. Extraction, layout reconstruction, scan
splitting and output are shared code and stay untouched when a release is
added.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Sequence

#: Header fields holding a spatial extent, whose trailing unit is dropped.
#: The field name already carries the unit, and keeping it would make ``mm3``
#: and ``mm³`` compare unequal in a diff across releases.
SIZE_FIELDS: tuple[str, ...] = ("voxel_size_mm", "voi_mm")

#: Trailing volume unit, superscript or not.
_SIZE_UNIT_RE = re.compile(r"\s*mm[³²]?\s*$")


def strip_size_units(fields: dict[str, str]) -> dict[str, str]:
    """Drop the trailing ``mm`` unit from every spatial-extent field.

    Parameters
    ----------
    fields : dict of str to str
        Parsed header fields, modified in place.

    Returns
    -------
    dict of str to str
        The same mapping, with :data:`SIZE_FIELDS` unit-stripped.
    """
    for key in SIZE_FIELDS:
        value = fields.get(key)
        if value:
            fields[key] = _SIZE_UNIT_RE.sub("", value).strip()
    return fields


@dataclass
class LayoutConfig:
    """Geometry thresholds, expressed as ratios wherever possible.

    Ratios travel better than absolute points: they survive a page-size
    change and they hold up on the OCR path, where coordinates come back from
    a rasterization round trip rather than from the PDF itself.

    Attributes
    ----------
    page_header_max_y : float
        Spans above this y are the running "SIEMENS MAGNETOM ..." page header.
    page_footer_min_y : float
        Spans below this y are the page number.
    header_box_max_y : float
        How far down the page a scan header box may start.
    column_split_ratio : float
        Fraction of page width separating the left and right table columns.
    value_x_ratio : float
        Fraction of a column's content width separating labels from values.
    row_tolerance : float
        Spans whose ``y0`` differ by less than this belong to the same row.
    row_overlap_ratio : float
        Spans overlapping vertically by this fraction of the shorter one also
        belong to the same row; this is what keeps an OCR'd hyphen on its line.
    continuation_gap_ratio : float
        A row closer to its predecessor than this fraction of the normal row
        pitch is a wrapped continuation, not a new entry.
    title_gap_ratio : float
        A label-only row further from its predecessor than this is a title.
        Only consulted for a column with no parameter rows to measure against.
    title_size_ratio : float
        A label-only row at least this much larger than the body text is a
        title. Native pages only, where the size is a real font metric.
    title_outdent : float
        How far, in points, a section title hangs left of parameter labels.
    title_outdent_ratio : float
        Fraction of that offset a row must clear to count as outdented, which
        sets the tolerance for OCR jitter in the left edge.
    min_printable_ratio : float
        Below this printable-character ratio a page is sent to OCR.
    indent_threshold : float
        Indentation past the column label origin that marks a sub-row.
    """

    page_header_max_y: float = 45.0
    page_footer_min_y: float = 790.0
    header_box_max_y: float = 130.0
    column_split_ratio: float = 0.5
    value_x_ratio: float = 0.55
    row_tolerance: float = 3.5
    row_overlap_ratio: float = 0.5
    continuation_gap_ratio: float = 0.9
    title_gap_ratio: float = 1.25
    title_size_ratio: float = 1.1
    title_outdent: float = 2.4
    title_outdent_ratio: float = 0.4
    min_printable_ratio: float = 0.90
    indent_threshold: float = 5.0


@dataclass
class VersionProfile:
    """Everything version-specific about one Siemens software release.

    Attributes
    ----------
    name : str
        The release name, which is also its registry key.
    require : sequence of str
        Regex patterns that must all appear for the profile to match.
    reject : sequence of str
        Patterns that must not appear. This is what separates VE11C from
        XA60, since both name a MAGNETOM scanner.
    header_labels : sequence of tuple of str
        Ordered ``(output key, label regex)`` pairs for the header summary.
    param_fallbacks : dict of str to str
        Header keys filled in from a scan parameter when the box omits them.
    native_text_expected : bool
        Whether pages are expected to carry usable native text.
    layout : LayoutConfig
        Geometry thresholds for this release.
    """

    name: str
    require: Sequence[str] = ()
    reject: Sequence[str] = ()
    header_labels: Sequence[tuple[str, str]] = ()
    param_fallbacks: dict[str, str] = field(default_factory=dict)
    native_text_expected: bool = True
    layout: LayoutConfig = field(default_factory=LayoutConfig)

    def match_score(self, sample_text: str) -> float:
        """How strongly a document sample looks like this release.

        Scoring is deliberately blunt: the required patterns are distinctive
        strings printed on every page.

        Parameters
        ----------
        sample_text : str
            Text from the document's first pages.

        Returns
        -------
        float
            The number of required patterns matched, or ``0.0`` for a
            non-match or when any rejected pattern is present.
        """
        for pattern in self.reject:
            if re.search(pattern, sample_text, re.IGNORECASE):
                return 0.0
        if not self.require:
            return 0.0
        hits = sum(1 for pattern in self.require if re.search(pattern, sample_text, re.IGNORECASE))
        if hits < len(self.require):
            return 0.0
        return float(hits)

    def parse_header_summary(self, line: str) -> dict[str, str]:
        """Split the ``TA: ...`` line into its declared fields.

        Each declared label is located in the line, and the text running to
        the next label becomes its value. That tolerates the run-together
        spacing (``mmPAT:``) and doubled colons (``Acc::``) that the two
        current releases print, without a bespoke regex per release.

        Parameters
        ----------
        line : str
            The header box's summary line.

        Returns
        -------
        dict of str to str
            Field name to value, omitting fields that are absent or empty.
        """
        if not line:
            return {}
        found: list[tuple[int, int, str]] = []
        for key, pattern in self.header_labels:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                found.append((match.start(), match.end(), key))
        found.sort()
        out: dict[str, str] = {}
        for i, (_start, end, key) in enumerate(found):
            stop = found[i + 1][0] if i + 1 < len(found) else len(line)
            value = line[end:stop].strip()
            if value:
                out[key] = value
        return self.postprocess_header(out, line)

    def postprocess_header(self, fields: dict[str, str], line: str) -> dict[str, str]:
        """Hook for release-specific cleanup of the parsed header fields.

        Parameters
        ----------
        fields : dict of str to str
            Fields as split out by :meth:`parse_header_summary`.
        line : str
            The original summary line, for anything the split cannot express.

        Returns
        -------
        dict of str to str
            The cleaned fields. The base implementation returns them unchanged.
        """
        return fields


class ProfileRegistry:
    """Name to profile, with best-match auto-detection."""

    def __init__(self) -> None:
        """Create an empty registry.

        Returns
        -------
        None
        """
        self._profiles: dict[str, VersionProfile] = {}

    def register(self, profile: VersionProfile) -> VersionProfile:
        """Add a profile to the registry.

        Parameters
        ----------
        profile : VersionProfile
            The profile to register, keyed by its ``name``.

        Returns
        -------
        VersionProfile
            The same profile, so registration can wrap a definition.
        """
        self._profiles[profile.name] = profile
        return profile

    def names(self) -> list[str]:
        """Every registered profile name.

        Returns
        -------
        list of str
            Names in sorted order.
        """
        return sorted(self._profiles)

    def get(self, name: str) -> VersionProfile:
        """Look a profile up by name.

        Parameters
        ----------
        name : str
            A registered profile name.

        Returns
        -------
        VersionProfile
            The matching profile.

        Raises
        ------
        KeyError
            If no profile has that name. The message lists the known names.
        """
        try:
            return self._profiles[name]
        except KeyError:
            raise KeyError(
                f"unknown version profile {name!r}; known: {', '.join(self.names())}"
            ) from None

    def detect(self, sample_text: str) -> tuple[VersionProfile | None, dict]:
        """Pick the best-matching profile for a document sample.

        Detection is best effort by design; ``--version`` always overrides.

        Parameters
        ----------
        sample_text : str
            Text from the document's first pages.

        Returns
        -------
        tuple
            ``(profile, info)``. ``profile`` is ``None`` when nothing matches.
            ``info`` carries ``method`` and ``confidence`` for the output.
        """
        scored = [
            (profile.match_score(sample_text), profile) for profile in self._profiles.values()
        ]
        scored = [item for item in scored if item[0] > 0]
        scored.sort(key=lambda item: (-item[0], item[1].name))
        if not scored:
            return None, {"method": "none", "confidence": "none"}
        confidence = "low" if len(scored) > 1 and scored[0][0] == scored[1][0] else "high"
        return scored[0][1], {"method": "header-string", "confidence": confidence}


#: The registry every profile module registers itself with.
REGISTRY = ProfileRegistry()
