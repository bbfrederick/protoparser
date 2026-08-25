"""Recognize third-party ("customer") sequences in a parsed protocol.

Siemens' own conversion tools move stock sequences between releases reliably.
What breaks a protocol migration is a third-party sequence -- CMRR's multiband
EPI, MGH's navigated MPRAGE, a site's own spectroscopy binary -- because the
new release either has no equivalent installed or has one whose parameters do
not line up. Those are the scans that have to be rebuilt and checked by hand,
so the first thing a person needs is a list of which scans they are.

Two independent detectors, because neither signal is always present:

**The sequence binary.** ``header.sequence`` names the kernel. On VB17A this
is the sequence *file* name -- ``cmrr_mbep2d_bold``, ``mjd_mclean_flipback``,
``tfl_mgh_multiecho`` -- and identifies the sequence outright. On Numaris/X it
is only the kernel, so CMRR's multiband EPI and Siemens' stock ``ep2d_bold``
both report ``epfid`` and the name decides nothing.

**The Special card.** ``Sequence - Special`` holds the parameters the sequence
author added, so its labels are chosen by that author rather than by Siemens
and are the same across every release the sequence was ported to. This is the
detector that separates the two ``epfid`` cases, and the only one that works
when the header's sequence field is unreadable. VB17A prints no Special card
at all, which is why the binary-name detector cannot be dropped in its favour.

**The stated owner.** VB17A, alone among the releases, introduces the binary
with a label naming who owns it -- ``SIEMENS:`` or ``USER:`` -- which the
profile records as ``sequence_owner``. That is not an inference from a
fingerprint, it is the scanner saying so, and it partitions all 110 VB17A
example scans without a single disagreement with the other two detectors.
It therefore decides the verdict where it is present, while a signature still
supplies the *identity*, which an owner label cannot. The later releases stop
printing it, so it cannot replace the other two.

The catalog is curated, for the same reason the vocabularies are: a wrong
entry here reports a protocol as converting cleanly when it does not, which
costs a rebuild. A sequence the catalog cannot account for is therefore
reported as *unrecognized* rather than guessed at in either direction -- see
:data:`UNRECOGNIZED`.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Mapping, Sequence

#: Where the shipped catalog lives.
CATALOG_DIR = Path(__file__).parent

#: The scan runs a sequence the catalog names, and that sequence is not
#: Siemens'. These are the scans a migration has to rebuild by hand.
THIRD_PARTY = "third-party"

#: The scan runs a Siemens kernel and prints no sequence-specific parameters.
#: Siemens' own conversion handles these.
STOCK = "stock"

#: Neither detector could account for the scan. Deliberately not a verdict of
#: "third-party": a stock sequence whose Special card these examples happen
#: never to exercise would land here too. It means "check this by hand",
#: which is the honest thing to say and the useful one.
UNRECOGNIZED = "unrecognized"

#: Verdicts in decreasing order of concern.
VERDICTS = (THIRD_PARTY, UNRECOGNIZED, STOCK)

#: Selector naming both verdicts that need a person to look at the scan. It
#: is the one a migration actually asks for, and spelling it as a single word
#: keeps ``--only`` from needing to accept a list.
FLAGGED = "flagged"

#: What each ``--only`` selector expands to.
SELECTORS = {
    THIRD_PARTY: (THIRD_PARTY,),
    UNRECOGNIZED: (UNRECOGNIZED,),
    STOCK: (STOCK,),
    FLAGGED: (THIRD_PARTY, UNRECOGNIZED),
}

#: Trailing ``#2``/``#3`` that :func:`~..model.build_sections` adds to a key
#: repeated within one section. The catalog stores unsuffixed labels.
_REPEAT_SUFFIX = re.compile(r"\s*#\d+$")


def special_keys(scan: Mapping) -> set[str]:
    """Every parameter label printed on a scan's Special card.

    The card is matched by the last component of the section title rather
    than by the full title, so a release that names it something other than
    ``Sequence - Special`` still contributes. Matching on the substring
    ``Special`` anywhere would be wrong: VB17A prints a Geometry parameter
    called ``Special sat.``, which is a saturation setting and says nothing
    about who wrote the sequence.

    Parameters
    ----------
    scan : mapping
        A serialized scan, carrying ``sections``.

    Returns
    -------
    set of str
        The labels, with any repeat suffix stripped. Empty when the scan
        prints no Special card, which is the normal case for a stock
        sequence and the invariable case on VB17A.
    """
    found: set[str] = set()
    for title, params in (scan.get("sections") or {}).items():
        if title.split(" - ")[-1].strip() != "Special":
            continue
        for key in params or ():
            found.add(_REPEAT_SUFFIX.sub("", key).strip())
    return found


@dataclass(frozen=True)
class Signature:
    """One catalog entry: how to recognize a sequence, and what it is.

    Every populated clause must hold for the signature to match, so clauses
    combine as AND. ``special_any`` is the one OR, over its own entries.

    Attributes
    ----------
    id : str
        Stable identifier, unique within the catalog.
    vendor : str
        Who supplies the sequence, as a person would name them.
    family : str
        What the sequence is, in the words a protocol librarian would use.
    binaries : tuple of str, optional
        Sequence binary names that identify this sequence on their own.
        Matched exactly: ``VA\\d\\d``-style loose matching is what turns a
        near miss into a confident wrong answer.
    base_binaries : tuple of str, optional
        Kernels the Special-card route is allowed to apply to, used to split
        one parameter fingerprint across the kernels it rides on -- the CMRR
        multiband card appears on ``epfid`` for BOLD and ``epse`` for
        diffusion, and only the kernel says which. Empty means any kernel.
        It gates the Special-card route only, never ``binaries``, which is a
        statement about the sequence rather than about the kernel under it.
    special_all : tuple of str, optional
        Special-card labels that must all be present.
    special_any : tuple of str, optional
        Special-card labels of which at least one must be present.
    priority : int, optional
        Breaks ties when a scan matches more than one signature, higher
        winning. Needed because the number of conditions does not say which
        of two matches is the narrower one: the entry for CMRR's EPI package
        names six labels, the entry for its multiband variant names two plus
        a kernel, and the *variant* is the more specific answer. Default 0.
    note : str, optional
        Why this entry is here and how sure it is. Documentation only, but
        printed by ``sequences --explain`` because a reader deciding whether
        to trust an identification needs the evidence, not just the verdict.
    """

    id: str
    vendor: str
    family: str
    binaries: tuple[str, ...] = ()
    base_binaries: tuple[str, ...] = ()
    special_all: tuple[str, ...] = ()
    special_any: tuple[str, ...] = ()
    priority: int = 0
    note: str = ""

    def match(self, binary: str, special: set[str]) -> list[str] | None:
        """Test one scan against this signature.

        The two routes are independent and either is sufficient. A binary
        this signature names is a statement about the sequence; a Special
        card carrying its labels is the same statement made by the sequence
        author's own parameter names. Requiring both would drop every scan
        whose header the layout could not read -- one XA30 export prints a
        navigated MPRAGE with no sequence field at all -- and every VB17A
        scan, since VB17A prints no Special card.

        Parameters
        ----------
        binary : str
            The scan's sequence binary, empty when the export printed none.
        special : set of str
            The scan's Special-card labels, as :func:`special_keys` returns.

        Returns
        -------
        list of str or None
            Human-readable evidence for the match, or ``None`` when the
            signature does not apply. The list is never empty on a match:
            a signature with no clauses at all cannot match anything, which
            is what stops an incomplete entry from claiming every scan.
        """
        evidence: list[str] = []
        if self.binaries and binary and binary in self.binaries:
            evidence.append(f"sequence binary {binary!r}")
        evidence.extend(self._special_evidence(binary, special))
        return evidence or None

    def _special_evidence(self, binary: str, special: set[str]) -> list[str]:
        """Evidence from the Special card, if that route applies and holds.

        Parameters
        ----------
        binary : str
            The scan's sequence binary, used only for the ``base_binaries``
            gate. An empty binary fails a gate that is set, which is what
            keeps a kernel-less scan from being handed the BOLD variant of a
            fingerprint that also has a diffusion variant.
        special : set of str
            The scan's Special-card labels.

        Returns
        -------
        list of str
            One entry per satisfied clause, empty when the route does not
            apply or does not hold.
        """
        if not (self.special_all or self.special_any):
            return []
        if self.base_binaries and binary not in self.base_binaries:
            return []
        evidence: list[str] = []
        if self.special_all:
            if any(k not in special for k in self.special_all):
                return []
            evidence.append("Special card prints " + ", ".join(self.special_all))
        if self.special_any:
            present = [k for k in self.special_any if k in special]
            if not present:
                return []
            evidence.append("Special card prints " + ", ".join(present))
        return evidence

    def weight(self) -> int:
        """How specific this signature is, for ranking competing matches.

        Counting the individual conditions rather than the clauses means the
        CMRR multiband entry, which names two Special labels on top of a base
        kernel, outranks the CMRR core entry that names six labels and no
        kernel only when it genuinely tests more.

        Returns
        -------
        int
            The number of individual conditions the signature imposes.
        """
        return (
            len(self.special_all)
            + (1 if self.special_any else 0)
            + (1 if self.binaries else 0)
            + (1 if self.base_binaries else 0)
        )

    def rank(self) -> tuple[int, int]:
        """How this signature sorts against a competing match.

        Returns
        -------
        tuple of int
            ``(priority, weight)``, compared left to right. The first
            signature in file order wins a full tie, because the comparison
            is strictly greater-than.
        """
        return (self.priority, self.weight())


@dataclass(frozen=True)
class Identification:
    """What one scan was found to be running.

    Attributes
    ----------
    index : int
        The scan's zero-based position in the protocol.
    name : str
        The protocol name from the scan's header box.
    binary : str
        The sequence binary as printed, empty when the export printed none.
    verdict : str
        One of :data:`VERDICTS`.
    vendor : str
        Who supplies the sequence, empty unless a signature matched.
    family : str
        What the sequence is, empty unless a signature matched.
    signature : str
        The matching signature's ``id``, empty when none matched.
    evidence : tuple of str
        Why the verdict was reached, in the order the detectors ran.
    special_count : int
        How many labels the scan's Special card printed. Reported even for a
        confident match, because it is the number that says how much of the
        sequence is unlike anything Siemens ships.
    note : str
        The matching signature's note, empty when none matched.
    """

    index: int
    name: str
    binary: str
    verdict: str
    vendor: str = ""
    family: str = ""
    signature: str = ""
    evidence: tuple[str, ...] = ()
    special_count: int = 0
    note: str = ""

    def to_dict(self, include_note: bool = False) -> dict:
        """Serialize the identification.

        Parameters
        ----------
        include_note : bool, optional
            Whether to carry the signature's note. Off by default: the note
            explains the catalog entry rather than this scan, so repeating it
            once per scan would bury the findings. Default ``False``.

        Returns
        -------
        dict
            The fields above, with the ones a stock scan leaves empty omitted.
        """
        out: dict[str, object] = {"verdict": self.verdict}
        if self.vendor:
            out["vendor"] = self.vendor
        if self.family:
            out["family"] = self.family
        if self.signature:
            out["signature"] = self.signature
        if self.evidence:
            out["evidence"] = list(self.evidence)
        out["special_parameters"] = self.special_count
        if include_note and self.note:
            out["note"] = self.note
        return out


@dataclass
class Catalog:
    """The shipped signatures, plus what counts as a Siemens kernel.

    Attributes
    ----------
    signatures : list of Signature
        Every known third-party sequence, in file order. Order breaks ties
        between signatures of equal specificity.
    stock_binaries : dict of str to str
        Sequence binaries Siemens ships, mapped to what they are. A scan is
        only called ``stock`` when its binary is listed here *and* it prints
        no Special card, so this list being incomplete produces an
        ``unrecognized`` verdict rather than a wrong one.
    path_markers : dict of str to str
        Substrings in a scan's protocol path that mark it as site-installed,
        mapped to what the marker means. Matched case-insensitively.
    third_party_owners : dict of str to str
        Values of the header's ``sequence_owner`` field that mean the scan
        runs a sequence Siemens did not supply, mapped to what each means.
    stock_owners : dict of str to str
        Values of ``sequence_owner`` that mean Siemens did supply it.
    description : str
        What the catalog covers.
    """

    signatures: list[Signature] = field(default_factory=list)
    stock_binaries: dict[str, str] = field(default_factory=dict)
    path_markers: dict[str, str] = field(default_factory=dict)
    third_party_owners: dict[str, str] = field(default_factory=dict)
    stock_owners: dict[str, str] = field(default_factory=dict)
    description: str = ""

    def by_id(self) -> dict[str, Signature]:
        """Index the signatures by identifier.

        Returns
        -------
        dict of str to Signature
            Every signature, keyed by its ``id``.
        """
        return {s.id: s for s in self.signatures}


def _signature_from(payload: Mapping, source: Path) -> Signature:
    """Build one signature from its JSON form.

    Parameters
    ----------
    payload : mapping
        One entry of a catalog file's ``signatures`` list.
    source : Path
        The file it came from, named in any error.

    Returns
    -------
    Signature
        The parsed entry.

    Raises
    ------
    ValueError
        If a required field is missing or a field has the wrong type.
    """

    def text(name: str, required: bool = False) -> str:
        value = payload.get(name, "")
        if not isinstance(value, str) or (required and not value):
            raise ValueError(f"{source}: signature field {name!r} must be a non-empty string")
        return value

    def words(name: str) -> tuple[str, ...]:
        value = payload.get(name, [])
        if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
            raise ValueError(f"{source}: signature field {name!r} must be a list of strings")
        return tuple(value)

    def number(name: str) -> int:
        value = payload.get(name, 0)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{source}: signature field {name!r} must be a whole number")
        return value

    match = payload.get("match", {})
    if not isinstance(match, dict):
        raise ValueError(f"{source}: signature {payload.get('id')!r} has a non-object 'match'")
    merged = {**payload, **match}
    payload = merged
    return Signature(
        id=text("id", required=True),
        vendor=text("vendor", required=True),
        family=text("family", required=True),
        binaries=words("binaries"),
        base_binaries=words("base_binaries"),
        special_all=words("special_all"),
        special_any=words("special_any"),
        priority=number("priority"),
        note=text("note"),
    )


def load_catalog(extra_dir: str | os.PathLike | None = None) -> Catalog:
    """Load the sequence catalog, optionally overlaid with a site's own.

    An overlay lets a site name the sequences only it runs without editing
    the installed package. A signature whose ``id`` already exists replaces
    the shipped one; every other entry is appended, so an overlay can both
    correct and extend.

    Parameters
    ----------
    extra_dir : path-like or None, optional
        A directory of additional ``*.json`` catalogs. Read in sorted order
        after the shipped one.

    Returns
    -------
    Catalog
        The merged catalog.

    Raises
    ------
    ValueError
        If a catalog file is present but malformed.
    """
    catalog = Catalog()
    files = [CATALOG_DIR / "catalog.json"]
    if extra_dir:
        files.extend(sorted(Path(extra_dir).glob("*.json")))
    for path in files:
        if not path.is_file():
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"could not read the sequence catalog at {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"{path}: a catalog file must hold a JSON object")
        for name in ("stock_binaries", "path_markers", "third_party_owners", "stock_owners"):
            entries = payload.get(name, {})
            if not isinstance(entries, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in entries.items()
            ):
                raise ValueError(f"{path}: {name!r} must map strings to description strings")
            getattr(catalog, name).update(entries)
        listed = payload.get("signatures", [])
        if not isinstance(listed, list):
            raise ValueError(f"{path}: 'signatures' must be a list")
        index = {s.id: position for position, s in enumerate(catalog.signatures)}
        for entry in listed:
            if not isinstance(entry, dict):
                raise ValueError(f"{path}: every signature must be a JSON object")
            signature = _signature_from(entry, path)
            if signature.id in index:
                catalog.signatures[index[signature.id]] = signature
            else:
                index[signature.id] = len(catalog.signatures)
                catalog.signatures.append(signature)
        catalog.description = payload.get("description", catalog.description)
    return catalog


def _path_evidence(path: str, markers: Mapping[str, str]) -> list[tuple[str, str]]:
    """Site-install markers found in a scan's protocol path.

    Parameters
    ----------
    path : str
        The scan's full path from the header box.
    markers : mapping
        Marker substring to what it means, as the catalog declares them.

    Returns
    -------
    list of tuple of str
        ``(evidence, meaning)`` for each marker present, in catalog order.
    """
    lowered = (path or "").lower()
    return [
        (f"protocol path contains {marker!r}", meaning)
        for marker, meaning in markers.items()
        if marker.lower() in lowered
    ]


def identify(scan: Mapping, catalog: Catalog) -> Identification:
    """Decide what one scan is running.

    Recomputed from the scan's own parameters rather than read back from a
    stored verdict, so a catalog correction takes effect on JSON that was
    parsed before the correction was made.

    Where the export states the sequence's owner, that statement decides the
    verdict, because it is the scanner saying who supplied the sequence
    rather than this catalog inferring it. A signature still supplies the
    identity: ``USER`` says the sequence is not Siemens', not which one it is.
    A stated owner of Siemens that contradicts a matched third-party
    signature yields :data:`UNRECOGNIZED` rather than a silent choice between
    them -- two disagreeing signals is precisely a scan a person should look
    at, which is what that verdict means.

    Parameters
    ----------
    scan : mapping
        A serialized scan, carrying ``header``, ``path`` and ``sections``.
    catalog : Catalog
        The signatures to test against.

    Returns
    -------
    Identification
        The verdict and the evidence for it.
    """
    header = scan.get("header") or {}
    binary = str(header.get("sequence", "")).strip()
    owner = str(header.get("sequence_owner", "")).strip()
    special = special_keys(scan)
    markers = _path_evidence(str(scan.get("path", "")), catalog.path_markers)
    common = dict(
        index=int(scan.get("index", 0)),
        name=str(scan.get("name", "")),
        binary=binary,
        special_count=len(special),
    )

    best: Signature | None = None
    best_evidence: list[str] = []
    for signature in catalog.signatures:
        found = signature.match(binary, special)
        if found is None:
            continue
        if best is None or signature.rank() > best.rank():
            best, best_evidence = signature, found

    stated_third_party = catalog.third_party_owners.get(owner)
    stated_stock = catalog.stock_owners.get(owner)
    owner_evidence = f"the export states sequence owner {owner!r}"

    if stated_stock is not None and (best is not None or markers):
        claim = describe_signature(best) if best else markers[0][1]
        return Identification(
            verdict=UNRECOGNIZED,
            evidence=(
                f"identified as {claim}",
                *best_evidence,
                *(entry for entry, _ in markers),
                f"but {owner_evidence} -- {stated_stock}",
            ),
            **common,
        )

    if best is not None:
        return Identification(
            verdict=THIRD_PARTY,
            vendor=best.vendor,
            family=best.family,
            signature=best.id,
            evidence=tuple(
                best_evidence
                + [entry for entry, _ in markers]
                + ([owner_evidence] if stated_third_party is not None else [])
            ),
            note=best.note,
            **common,
        )

    if markers or stated_third_party is not None:
        # Siemens writes both of these itself, so they are statements about
        # the sequence rather than inferences about it -- third-party for
        # certain, just not named.
        meaning = markers[0][1] if markers else stated_third_party
        return Identification(
            verdict=THIRD_PARTY,
            family=meaning,
            evidence=tuple(
                [entry for entry, _ in markers]
                + ([owner_evidence] if stated_third_party is not None else [])
            ),
            **common,
        )

    if stated_stock is not None and not special:
        return Identification(
            verdict=STOCK,
            vendor="Siemens",
            family=catalog.stock_binaries.get(binary, stated_stock),
            evidence=(owner_evidence, "no sequence-specific parameters printed"),
            **common,
        )

    if binary and binary in catalog.stock_binaries and not special:
        return Identification(
            verdict=STOCK,
            vendor="Siemens",
            family=catalog.stock_binaries[binary],
            evidence=(
                f"sequence binary {binary!r} is a Siemens kernel",
                "no sequence-specific parameters printed",
            ),
            **common,
        )

    return Identification(
        verdict=UNRECOGNIZED, evidence=_why_unknown(binary, special, catalog), **common
    )


def describe_signature(signature: Signature) -> str:
    """A signature's identity as one phrase.

    Parameters
    ----------
    signature : Signature
        The signature to describe.

    Returns
    -------
    str
        ``vendor -- family``.
    """
    return f"{signature.vendor} -- {signature.family}"


def _why_unknown(binary: str, special: set[str], catalog: Catalog) -> tuple[str, ...]:
    """Say what stopped a scan from being identified either way.

    The wording matters more than it looks: this text is a person's to-do
    list, and "check it by hand" is only actionable if it also says what
    about the scan could not be accounted for.

    Parameters
    ----------
    binary : str
        The scan's sequence binary, possibly empty.
    special : set of str
        The scan's Special-card labels.
    catalog : Catalog
        The catalog that failed to match, read for its stock kernel list.

    Returns
    -------
    tuple of str
        One line per reason, at least one.
    """
    reasons: list[str] = []
    if not binary:
        reasons.append("the export printed no sequence binary for this scan")
    elif binary not in catalog.stock_binaries:
        reasons.append(f"sequence binary {binary!r} is not a listed Siemens kernel")
    else:
        reasons.append(f"sequence binary {binary!r} is a Siemens kernel")
    if special:
        reasons.append(
            f"prints {len(special)} sequence-specific parameter(s) no signature accounts for, "
            "including " + ", ".join(sorted(special)[:3])
        )
    return tuple(reasons)


def identify_protocol(protocol: Mapping, catalog: Catalog) -> list[Identification]:
    """Identify every scan of a protocol, in acquisition order.

    Parameters
    ----------
    protocol : mapping
        A serialized protocol, carrying ``scans``.
    catalog : Catalog
        The signatures to test against.

    Returns
    -------
    list of Identification
        One entry per scan, ordered as the scans appear in the document.
    """
    return [identify(scan, catalog) for scan in protocol.get("scans", [])]


def summarize(found: Sequence[Identification]) -> dict[str, int]:
    """Count the scans falling under each verdict.

    Parameters
    ----------
    found : sequence of Identification
        The identifications to count.

    Returns
    -------
    dict of str to int
        Every verdict in :data:`VERDICTS`, including the ones at zero, so a
        caller rendering the summary need not fill in the gaps.
    """
    counts = {verdict: 0 for verdict in VERDICTS}
    for item in found:
        counts[item.verdict] = counts.get(item.verdict, 0) + 1
    return counts


def check(catalog: Catalog) -> list[str]:
    """Look for problems in a catalog.

    Parameters
    ----------
    catalog : Catalog
        The catalog to validate.

    Returns
    -------
    list of str
        Human-readable problems, empty when everything lines up.
    """
    problems: list[str] = []
    seen: set[str] = set()
    for signature in catalog.signatures:
        if signature.id in seen:
            problems.append(f"signature id {signature.id!r} is defined more than once")
        seen.add(signature.id)
        if not signature.weight():
            problems.append(
                f"signature {signature.id!r} imposes no conditions, so it would claim every scan"
            )
        for binary in signature.binaries:
            if binary in catalog.stock_binaries:
                problems.append(
                    f"signature {signature.id!r} claims sequence binary {binary!r}, which is "
                    "also listed as a Siemens kernel"
                )
        overlap = set(signature.special_all) & set(signature.special_any)
        if overlap:
            problems.append(
                f"signature {signature.id!r} lists {', '.join(sorted(overlap))} as both "
                "required and optional"
            )
    return problems


#: Marker printed against each verdict, so a long listing can be skimmed and
#: grepped. ``*`` is the one that means "rebuild this by hand".
_MARK = {THIRD_PARTY: "*", UNRECOGNIZED: "?", STOCK: " "}


def describe(item: Identification) -> str:
    """One scan's identity as a single phrase.

    Not a plain join of the two fields: a scan flagged only by its owner
    label has a family and no vendor, since the export can say a sequence is
    not Siemens' without saying whose it is, and joining would leave a
    dangling separator in front of it.

    Parameters
    ----------
    item : Identification
        The identification to describe.

    Returns
    -------
    str
        ``vendor -- family`` where both are known, whichever is known when
        only one is, and a short reason when neither is.
    """
    if item.vendor and item.family:
        return f"{item.vendor} -- {item.family}"
    if item.vendor or item.family:
        return item.vendor or item.family
    return "not recognized"


def _notes_section(shown: Sequence[Identification]) -> list[str]:
    """The catalog notes for the signatures a report actually matched.

    Parameters
    ----------
    shown : sequence of Identification
        The identifications listed in the table.

    Returns
    -------
    list of str
        A headed block of one note per distinct signature, in the order the
        signatures first appear. Empty when nothing matched carries a note.
    """
    notes: dict[str, tuple[str, str]] = {}
    for item in shown:
        if item.signature and item.note and item.signature not in notes:
            notes[item.signature] = (describe(item), item.note)
    if not notes:
        return []
    lines = ["", "why these were identified as they were:"]
    for signature, (described, note) in notes.items():
        lines.append(f"  {signature} -- {described}")
        lines.append(f"    {note}")
    return lines


def render(
    protocol: Mapping,
    found: Sequence[Identification],
    explain: bool = False,
    only: str | None = None,
) -> str:
    """Render the per-scan findings as an aligned table with a summary.

    Third-party and unrecognized scans are summarized *above* the table as
    well as marked in it, because the reason to run this command is to learn
    how much hand work a migration needs, and that answer should not require
    reading every row of a fifty-scan protocol.

    Parameters
    ----------
    protocol : mapping
        The serialized protocol, read for the heading.
    found : sequence of Identification
        The identifications, as :func:`identify_protocol` returns them.
    explain : bool, optional
        Whether to print the evidence and the catalog note under each
        non-stock row shown. Default ``False``.
    only : str or None, optional
        A key of :data:`SELECTORS`, restricting which rows the table lists.
        The counts above the table are always over every scan, so a filtered
        listing still says what it left out. Default ``None``, meaning all.

    Returns
    -------
    str
        The complete report.

    Raises
    ------
    ValueError
        If ``only`` is not a key of :data:`SELECTORS`.
    """
    heading = f"{protocol.get('source_file', '')} ({protocol.get('software_version', '')})"
    if not found:
        return f"{heading}\n\nno scans found"

    if only is not None and only not in SELECTORS:
        raise ValueError(f"unknown selector {only!r}")
    wanted = SELECTORS[only] if only else VERDICTS
    shown = [item for item in found if item.verdict in wanted]

    counts = summarize(found)
    lines = [
        heading,
        "",
        f"{counts[THIRD_PARTY]} third-party, {counts[UNRECOGNIZED]} unrecognized, "
        f"{counts[STOCK]} stock, of {len(found)} scans",
    ]

    # Built through describe() rather than by joining the two fields, because
    # a scan flagged only by its owner label has a family and no vendor: the
    # export says the sequence is not Siemens' without saying whose it is.
    families = sorted({describe(i) for i in found if i.verdict == THIRD_PARTY})
    if families:
        lines.append("")
        lines.append("third-party sequences present:")
        lines.extend(f"  - {family}" for family in families)

    if not shown:
        lines.append("")
        lines.append(f"no scans to list under --only {only}")
        return "\n".join(lines)

    w_index = max([len("#")] + [len(str(i.index)) for i in shown])
    w_name = max([len("scan")] + [len(i.name) for i in shown])
    w_binary = max([len("sequence")] + [len(i.binary) for i in shown])
    lines.extend(
        [
            "",
            f"  {'#':>{w_index}}  {'scan':<{w_name}}  {'sequence':<{w_binary}}  identified as",
            f"  {'-' * w_index}  {'-' * w_name}  {'-' * w_binary}  {'-' * 13}",
        ]
    )
    for item in shown:
        lines.append(
            f"{_MARK[item.verdict]} {item.index:>{w_index}}  {item.name:<{w_name}}  "
            f"{item.binary:<{w_binary}}  {describe(item)}"
        )
        if explain and item.verdict != STOCK:
            lines.extend(f"      {reason}" for reason in item.evidence)

    lines.append("")
    lines.append("* rebuild and check by hand   ? not accounted for, check by hand")
    if explain:
        # Once per signature rather than once per row. A protocol that runs
        # sixteen CMRR scans would otherwise print the same paragraph
        # sixteen times and bury the rows it is annotating.
        lines.extend(_notes_section(shown))
    if counts[UNRECOGNIZED]:
        # Said out loud because the bucket is easy to misread as a clean
        # bill: it means the catalog had nothing to say, not that the scan
        # is a stock sequence.
        lines.append(
            f"\n{counts[UNRECOGNIZED]} scan(s) matched no signature and no listed Siemens "
            "kernel. That is not a finding of 'stock' -- run with --explain to see what "
            "could not be accounted for."
        )
    return "\n".join(lines)


@lru_cache(maxsize=1)
def default_catalog() -> Catalog:
    """The shipped catalog, loaded once.

    Serialization identifies every scan, so the catalog would otherwise be
    read from disk once per scan of every protocol parsed.

    Returns
    -------
    Catalog
        The shipped catalog, with no overlay. Callers that accept an overlay
        directory must call :func:`load_catalog` instead.
    """
    return load_catalog()
