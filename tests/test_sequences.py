"""Recognizing third-party sequences.

The catalog is data, so most of what can go wrong is a data mistake rather
than a code one: an entry that claims every scan, a fingerprint that also
matches a stock sequence, a signature that stops matching because a release
respells one label. The corpus tests below are what catch those, and they
assert against the shipped examples rather than against a frozen list, so a
new example folder tightens them instead of editing them.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from conftest import GOLDEN, ParseFixture, find_example, requires_examples
from siemens_protocol.cli import main
from siemens_protocol.sequences import (
    FLAGGED,
    SELECTORS,
    STOCK,
    THIRD_PARTY,
    UNRECOGNIZED,
    VERDICTS,
    Catalog,
    Signature,
    check,
    default_catalog,
    describe,
    identify,
    identify_protocol,
    load_catalog,
    render,
    special_keys,
    summarize,
)


def golden_protocols() -> list[tuple[str, dict]]:
    """Every stored snapshot, parsed.

    Returns
    -------
    list of tuple
        ``(snapshot name, protocol)`` in sorted order. Empty when the
        snapshots are absent, which is how a source checkout without the
        example tree behaves.
    """
    if not os.path.isdir(GOLDEN):
        return []
    found = []
    for name in sorted(os.listdir(GOLDEN)):
        if name.endswith(".json"):
            with open(os.path.join(GOLDEN, name), encoding="utf-8") as handle:
                found.append((name, json.load(handle)))
    return found


GOLDEN_PROTOCOLS = golden_protocols()
requires_snapshots = pytest.mark.skipif(
    not GOLDEN_PROTOCOLS, reason="no golden snapshots to identify against"
)


def scan(**fields: object) -> dict:
    """Build a minimal serialized scan.

    Parameters
    ----------
    **fields : object
        ``sequence`` for the header's binary, ``owner`` for its stated
        owner, ``special`` for a list of Special-card labels, ``path`` for
        the header path. Anything else is set on the scan directly.

    Returns
    -------
    dict
        A serialized scan the identification functions accept.
    """
    special = fields.pop("special", [])
    sequence = fields.pop("sequence", "")
    owner = fields.pop("owner", "")
    header = {}
    if sequence:
        header["sequence"] = sequence
    if owner:
        header["sequence_owner"] = owner
    built = {
        "index": 0,
        "name": "a scan",
        "path": fields.pop("path", "\\\\Research\\Investigators\\x\\a scan"),
        "header": header,
        "sections": {"Sequence - Special": {k: "" for k in special}},
    }
    built.update(fields)
    return built


# ---------------------------------------------------------------- the catalog


def test_the_shipped_catalog_is_internally_consistent() -> None:
    assert check(default_catalog()) == []


def test_the_shipped_catalog_actually_holds_signatures() -> None:
    catalog = default_catalog()
    assert catalog.signatures
    assert catalog.stock_binaries
    assert all(s.vendor and s.family for s in catalog.signatures)


def test_every_signature_imposes_at_least_one_condition() -> None:
    # A signature with no clauses would match every scan ever parsed, and
    # would look like a spectacularly good catalog while doing it.
    assert all(s.weight() > 0 for s in default_catalog().signatures)


def test_a_signature_with_no_conditions_matches_nothing() -> None:
    empty = Signature(id="empty", vendor="v", family="f")
    assert empty.match("epfid", {"anything"}) is None
    assert check(Catalog(signatures=[empty]))


def test_check_rejects_a_binary_that_is_also_listed_as_a_siemens_kernel() -> None:
    catalog = Catalog(
        signatures=[Signature(id="s", vendor="v", family="f", binaries=("fl",))],
        stock_binaries={"fl": "FLASH"},
    )
    problems = check(catalog)
    assert any("also listed as a Siemens kernel" in p for p in problems)


def test_check_rejects_a_duplicated_signature_id() -> None:
    twice = [
        Signature(id="same", vendor="v", family="f", binaries=("a",)),
        Signature(id="same", vendor="v", family="f", binaries=("b",)),
    ]
    assert any("more than once" in p for p in check(Catalog(signatures=twice)))


# ------------------------------------------------------------------- matching


def test_the_two_detection_routes_are_each_sufficient_alone() -> None:
    # An AND between them would find nothing on VB17A, which prints no
    # Special card, and nothing on a scan whose header the layout could not
    # read. Both cases are in the shipped examples.
    both = Signature(
        id="s", vendor="v", family="f", binaries=("custom_seq",), special_all=("A key",)
    )
    assert both.match("custom_seq", set()) is not None
    assert both.match("", {"A key"}) is not None


def test_base_binaries_gate_the_special_card_route_but_not_the_binary_route() -> None:
    signature = Signature(
        id="s",
        vendor="v",
        family="f",
        binaries=("vendor_seq",),
        base_binaries=("epfid",),
        special_all=("A key",),
    )
    assert signature.match("epse", {"A key"}) is None
    # The binary names the sequence outright, so the kernel gate is not its
    # business: a vendor binary is a statement, not an inference.
    assert signature.match("vendor_seq", set()) is not None


def test_a_kernel_less_scan_does_not_satisfy_a_base_binary_gate() -> None:
    # Otherwise a scan with no readable kernel would match both the BOLD and
    # the diffusion variant of one fingerprint, and take whichever sorted
    # first. The catalog carries an explicit lower-priority entry for that
    # case instead, which says the base sequence is unknown.
    gated = Signature(id="s", vendor="v", family="f", base_binaries=("epfid",), special_all=("K",))
    assert gated.match("", {"K"}) is None


def test_priority_beats_condition_count_when_two_signatures_match() -> None:
    generic = Signature(
        id="generic", vendor="v", family="generic", special_all=("A", "B", "C"), priority=10
    )
    specific = Signature(
        id="specific",
        vendor="v",
        family="specific",
        base_binaries=("epfid",),
        special_all=("D",),
        priority=20,
    )
    catalog = Catalog(signatures=[generic, specific])
    found = identify(scan(sequence="epfid", special=["A", "B", "C", "D"]), catalog)
    assert found.signature == "specific"


def test_special_any_needs_only_one_of_its_labels() -> None:
    signature = Signature(id="s", vendor="v", family="f", special_any=("X", "Y"))
    assert signature.match("", {"Y"}) is not None
    assert signature.match("", {"Z"}) is None


# ------------------------------------------------------- reading the card


def test_the_special_card_is_matched_by_title_not_by_substring() -> None:
    # VB17A prints a Geometry parameter called "Special sat.", which is a
    # saturation setting and says nothing about who wrote the sequence.
    sections = {
        "Geometry - Saturation": {"Special sat.": "None"},
        "Sequence - Special": {"MB kernel size": "5"},
    }
    assert special_keys({"sections": sections}) == {"MB kernel size"}


def test_repeat_suffixes_are_stripped_from_special_card_labels() -> None:
    sections = {"Sequence - Special": {"OVS module 1": "a", "OVS module 1 #2": "b"}}
    assert special_keys({"sections": sections}) == {"OVS module 1"}


def test_a_scan_with_no_sections_has_an_empty_special_card() -> None:
    assert special_keys({}) == set()


# ------------------------------------------------------------------ verdicts


def test_a_stock_kernel_with_an_empty_special_card_is_stock() -> None:
    found = identify(scan(sequence="fl"), default_catalog())
    assert found.verdict == STOCK
    assert found.vendor == "Siemens"


def test_a_stock_kernel_with_an_unaccounted_special_card_is_not_called_stock() -> None:
    # This is the case the whole 'unrecognized' bucket exists for. Calling it
    # stock would report a protocol as converting cleanly when it may not.
    found = identify(scan(sequence="tse", special=["Some vendor knob"]), default_catalog())
    assert found.verdict == UNRECOGNIZED
    assert any("no signature accounts for" in reason for reason in found.evidence)


def test_an_unlisted_kernel_is_unrecognized_rather_than_third_party() -> None:
    found = identify(scan(sequence="not_a_real_kernel"), default_catalog())
    assert found.verdict == UNRECOGNIZED
    assert any("not a listed Siemens kernel" in reason for reason in found.evidence)


def test_a_customer_path_marker_is_enough_on_its_own() -> None:
    # Siemens writes CustomerSeq into the path itself, so this is a statement
    # about the sequence rather than an inference about it -- third-party for
    # certain, even though the catalog cannot name which one.
    found = identify(
        scan(sequence="unknown_kernel", path="\\\\USER\\x\\CustomerSeq\\thing"), default_catalog()
    )
    assert found.verdict == THIRD_PARTY
    assert not found.signature
    assert any("CustomerSeq" in reason for reason in found.evidence)


def test_a_scan_with_no_sequence_and_no_special_card_is_unrecognized() -> None:
    found = identify(scan(), default_catalog())
    assert found.verdict == UNRECOGNIZED
    assert any("no sequence binary" in reason for reason in found.evidence)


# ----------------------------------------------------------- the real corpus


@requires_snapshots
def test_every_shipped_example_scan_gets_exactly_one_verdict() -> None:
    catalog = default_catalog()
    for name, protocol in GOLDEN_PROTOCOLS:
        found = identify_protocol(protocol, catalog)
        assert len(found) == len(protocol["scans"]), name
        assert all(item.verdict in VERDICTS for item in found), name


@requires_snapshots
def test_no_stock_verdict_is_given_to_a_scan_that_prints_a_special_card() -> None:
    # The invariant that makes the whole approach work: on every release that
    # prints a Special card at all, a scan that prints one is never called
    # stock. It is either identified or flagged for a person.
    catalog = default_catalog()
    for name, protocol in GOLDEN_PROTOCOLS:
        for item, raw in zip(identify_protocol(protocol, catalog), protocol["scans"]):
            if special_keys(raw):
                assert item.verdict != STOCK, f"{name}: {item.name}"


@requires_snapshots
def test_the_cmrr_multiband_verdict_always_rests_on_real_evidence() -> None:
    # 'epfid' and 'epse' are stock kernel names, so on Numaris/X and VE11C
    # the MB fingerprint is the only thing separating CMRR's multiband EPI
    # from Siemens' ep2d_bold. VB17A prints the sequence file name instead
    # and no Special card at all, so there the binary carries it. Either
    # route is legitimate; neither being present is not.
    catalog = default_catalog()
    vendor_binaries = set(catalog.by_id()["cmrr-mb-epi-bold"].binaries) | set(
        catalog.by_id()["cmrr-mb-epi-diffusion"].binaries
    )
    for name, protocol in GOLDEN_PROTOCOLS:
        for item, raw in zip(identify_protocol(protocol, catalog), protocol["scans"]):
            if item.signature not in ("cmrr-mb-epi-bold", "cmrr-mb-epi-diffusion"):
                continue
            mb = {"MB LeakBlock kernel", "Online multi-band recon."} <= special_keys(raw)
            assert mb or item.binary in vendor_binaries, f"{name}: {item.name}"
            if mb:
                # The kernel is what splits BOLD from diffusion, so a
                # fingerprint match must have one of the two.
                assert item.binary in ("epfid", "epse"), f"{name}: {item.name}"


@requires_snapshots
def test_a_stock_ep2d_bold_is_not_mistaken_for_cmrr_multiband() -> None:
    # The other side of the same question, and the one that would show a
    # false positive: the shipped examples contain stock epfid/epse scans
    # with an empty Special card, and none of them may be claimed by CMRR.
    catalog = default_catalog()
    plain = 0
    for name, protocol in GOLDEN_PROTOCOLS:
        for item, raw in zip(identify_protocol(protocol, catalog), protocol["scans"]):
            if item.binary in ("epfid", "epse") and not special_keys(raw):
                plain += 1
                assert item.verdict == STOCK, f"{name}: {item.name}"
    assert plain, "no bare epfid/epse scan in the examples to test against"


@requires_snapshots
def test_every_shipped_signature_matches_something_in_the_examples() -> None:
    # A signature no example exercises is one nothing verifies. Deleting it
    # is not the fix -- widening the examples is -- but it should be visible.
    catalog = default_catalog()
    matched = {
        item.signature
        for _, protocol in GOLDEN_PROTOCOLS
        for item in identify_protocol(protocol, catalog)
        if item.signature
    }
    unused = sorted({s.id for s in catalog.signatures} - matched)
    assert not unused, f"signatures matched by no shipped example: {', '.join(unused)}"


@requires_snapshots
def test_the_examples_are_mostly_accounted_for() -> None:
    # Not a demand for perfection: 'unrecognized' is a legitimate answer and
    # the corpus contains a few. It is a floor, so a catalog change that
    # quietly stops matching cannot pass unnoticed. The healthy figure is
    # under 2%.
    catalog = default_catalog()
    found = [i for _, p in GOLDEN_PROTOCOLS for i in identify_protocol(p, catalog)]
    counts = summarize(found)
    assert counts[UNRECOGNIZED] / len(found) < 0.05
    assert counts[THIRD_PARTY] > counts[STOCK]


@requires_snapshots
def test_vb17a_is_identified_without_any_special_card() -> None:
    # VB17A prints no Special card at all -- the binary-name route is the
    # only one available, and it has to carry the release on its own.
    catalog = default_catalog()
    for name, protocol in GOLDEN_PROTOCOLS:
        if not name.startswith("VB17A-"):
            continue
        assert all(not special_keys(s) for s in protocol["scans"]), name
        found = identify_protocol(protocol, catalog)
        assert any(item.verdict == THIRD_PARTY for item in found), name


# ------------------------------------------------------------------ rendering


@requires_snapshots
def test_the_report_counts_every_scan_even_when_the_table_is_filtered() -> None:
    name, protocol = next((n, p) for n, p in GOLDEN_PROTOCOLS if len(p["scans"]) > 4)
    found = identify_protocol(protocol, default_catalog())
    whole = render(protocol, found)
    filtered = render(protocol, found, only=FLAGGED)
    summary = f"of {len(found)} scans"
    assert summary in whole and summary in filtered, name


def test_the_report_says_when_it_has_nothing_to_show() -> None:
    assert "no scans found" in render({"source_file": "x", "software_version": "y"}, [])


def test_an_unknown_selector_is_refused() -> None:
    with pytest.raises(ValueError):
        render({}, [identify(scan(sequence="fl"), default_catalog())], only="nonsense")


def test_selectors_cover_every_verdict_and_the_flagged_union() -> None:
    assert set(SELECTORS) == set(VERDICTS) | {FLAGGED}
    assert set(SELECTORS[FLAGGED]) == {THIRD_PARTY, UNRECOGNIZED}


def test_the_explained_report_prints_each_catalog_note_once() -> None:
    protocol = {
        "source_file": "x",
        "software_version": "VE11C",
        "scans": [
            scan(
                sequence="epfid",
                special=["MB LeakBlock kernel", "Online multi-band recon."],
                index=i,
            )
            for i in range(3)
        ],
    }
    found = identify_protocol(protocol, default_catalog())
    text = render(protocol, found, explain=True)
    note = default_catalog().by_id()["cmrr-mb-epi-bold"].note
    assert text.count(note) == 1


# -------------------------------------------------------------------- loading


def test_an_overlay_replaces_a_shipped_signature_by_id(tmp_path) -> None:
    (tmp_path / "site.json").write_text(
        json.dumps(
            {
                "signatures": [
                    {
                        "id": "cmrr-mb-epi-bold",
                        "vendor": "Somewhere else",
                        "family": "renamed",
                        "match": {"binaries": ["cmrr_mbep2d_bold"]},
                    },
                    {
                        "id": "site-only",
                        "vendor": "This site",
                        "family": "a local sequence",
                        "match": {"binaries": ["local_seq"]},
                    },
                ],
                "stock_binaries": {"local_stock": "something local"},
            }
        ),
        encoding="utf-8",
    )
    catalog = load_catalog(tmp_path)
    assert catalog.by_id()["cmrr-mb-epi-bold"].vendor == "Somewhere else"
    assert "site-only" in catalog.by_id()
    assert catalog.stock_binaries["local_stock"] == "something local"
    # Replacing rather than appending: one id, one entry.
    assert len(catalog.signatures) == len(default_catalog().signatures) + 1


def test_a_malformed_catalog_names_the_file_it_could_not_read(tmp_path) -> None:
    (tmp_path / "bad.json").write_text("{ not json", encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        load_catalog(tmp_path)
    assert "bad.json" in str(caught.value)


@pytest.mark.parametrize(
    "payload",
    [
        {"signatures": [{"id": "x", "vendor": "v"}]},
        {"signatures": [{"id": "x", "vendor": "v", "family": "f", "match": {"binaries": "a"}}]},
        {"signatures": [{"id": "x", "vendor": "v", "family": "f", "priority": "high"}]},
        {"stock_binaries": {"fl": 3}},
        {"signatures": "not a list"},
    ],
)
def test_a_malformed_catalog_entry_is_refused(tmp_path, payload: dict) -> None:
    (tmp_path / "bad.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        load_catalog(tmp_path)


def test_the_default_catalog_is_loaded_once() -> None:
    assert default_catalog() is default_catalog()


# ------------------------------------------------------------ the subcommand


@requires_examples
def test_the_subcommand_reports_a_pdf(capsys: pytest.CaptureFixture) -> None:
    assert main(["sequences", find_example("Brady_TMSstudy_Feb2024.pdf")]) == 0
    out = capsys.readouterr().out
    assert "third-party" in out
    assert "CMRR (University of Minnesota) -- multiband EPI, BOLD" in out


@requires_examples
def test_the_subcommand_reads_previously_parsed_json(tmp_path: Path) -> None:
    parsed = tmp_path / "p.json"
    assert main(["parse", find_example("CRISP.pdf"), "--out", str(parsed)]) == 0
    from_pdf = tmp_path / "a.txt"
    from_json = tmp_path / "b.txt"
    assert main(["sequences", find_example("CRISP.pdf"), "--out", str(from_pdf)]) == 0
    assert main(["sequences", str(parsed), "--out", str(from_json)]) == 0
    assert from_pdf.read_text(encoding="utf-8") == from_json.read_text(encoding="utf-8")


@requires_examples
def test_the_subcommand_works_on_json_written_without_the_flattened_view(tmp_path: Path) -> None:
    # Identification reads sections, never the flattened view, so --no-flatten
    # JSON has to keep working -- the same contract 'list' holds to.
    parsed = tmp_path / "p.json"
    assert main(["parse", find_example("CRISP.pdf"), "--no-flatten", "--out", str(parsed)]) == 0
    assert main(["sequences", str(parsed), "--out", str(tmp_path / "r.txt")]) == 0


@requires_examples
def test_the_subcommand_emits_json(tmp_path: Path) -> None:
    out = tmp_path / "found.json"
    assert main(["sequences", find_example("CRISP.pdf"), "--json", "--out", str(out)]) == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["software_version"] == "XA30"
    assert set(payload["counts"]) == set(VERDICTS)
    assert sum(payload["counts"].values()) == len(payload["scans"])
    assert payload["families"]
    assert {"index", "name", "sequence", "verdict"} <= set(payload["scans"][0])


@requires_examples
def test_only_filters_the_rows_but_never_the_counts(tmp_path: Path) -> None:
    whole = tmp_path / "all.json"
    flagged = tmp_path / "flagged.json"
    pdf = find_example("Brady_TMSstudy_Feb2024.pdf")
    assert main(["sequences", pdf, "--json", "--out", str(whole)]) == 0
    assert main(["sequences", pdf, "--json", "--only", FLAGGED, "--out", str(flagged)]) == 0
    everything = json.loads(whole.read_text(encoding="utf-8"))
    subset = json.loads(flagged.read_text(encoding="utf-8"))
    assert subset["counts"] == everything["counts"]
    assert len(subset["scans"]) < len(everything["scans"])
    assert all(s["verdict"] != STOCK for s in subset["scans"])


@requires_examples
def test_explain_adds_the_evidence(tmp_path: Path) -> None:
    pdf = find_example("Brady_TMSstudy_Feb2024.pdf")
    plain = tmp_path / "plain.txt"
    explained = tmp_path / "explained.txt"
    assert main(["sequences", pdf, "--out", str(plain)]) == 0
    assert main(["sequences", pdf, "--explain", "--out", str(explained)]) == 0
    text = explained.read_text(encoding="utf-8")
    assert "why these were identified as they were:" in text
    assert len(text) > len(plain.read_text(encoding="utf-8"))


def test_an_unreadable_input_fails_without_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    assert main(["sequences", str(tmp_path / "nope.json")]) == 1
    assert capsys.readouterr().err.strip()


@requires_examples
def test_a_flawed_overlay_is_reported_without_losing_the_shipped_signatures(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    # A site's mistake should cost them that entry, not the other sixteen.
    (tmp_path / "site.json").write_text(
        json.dumps(
            {
                "signatures": [
                    {
                        "id": "claims-a-siemens-kernel",
                        "vendor": "v",
                        "family": "f",
                        "match": {"binaries": ["fl"]},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    pdf = find_example("Brady_TMSstudy_Feb2024.pdf")
    assert main(["sequences", pdf, "--catalog", str(tmp_path)]) == 0
    captured = capsys.readouterr()
    assert "catalog:" in captured.err
    assert "CMRR (University of Minnesota) -- multiband EPI, BOLD" in captured.out


@requires_examples
def test_finding_third_party_sequences_is_not_an_error_exit(tmp_path: Path) -> None:
    # A research protocol is expected to be full of them. An exit code here
    # would make every ordinary run look like a scripted failure.
    assert (
        main(
            [
                "sequences",
                find_example("Brady_TMSstudy_Feb2024.pdf"),
                "--out",
                str(tmp_path / "r.txt"),
            ]
        )
        == 0
    )


# ------------------------------------------------------- the serialized form


@requires_examples
def test_every_serialized_scan_carries_its_provenance(parsed: ParseFixture) -> None:
    protocol = parsed(find_example("CRISP.pdf")).protocol.to_dict(include_flat=False)
    for scan_dict in protocol["scans"]:
        assert scan_dict["provenance"]["verdict"] in VERDICTS
        # Recomputing must agree with what serialization stored, or the two
        # readers of this JSON would disagree about the same scan.
        assert identify(scan_dict, default_catalog()).verdict == scan_dict["provenance"]["verdict"]


# ----------------------------------------------------------- the stated owner


def test_a_stated_user_owner_is_enough_without_any_signature() -> None:
    # VB17A introduces the binary with SIEMENS: or USER:. That is the scanner
    # saying who supplied the sequence, not an inference from a fingerprint.
    found = identify(scan(sequence="tse_crusher", owner="USER"), default_catalog())
    assert found.verdict == THIRD_PARTY
    assert not found.signature
    assert any("sequence owner 'USER'" in reason for reason in found.evidence)


def test_a_stated_siemens_owner_makes_a_scan_stock_even_off_the_kernel_list() -> None:
    # The stock kernel list is deliberately incomplete; a stated owner is not.
    found = identify(scan(sequence="some_unlisted_kernel", owner="SIEMENS"), default_catalog())
    assert found.verdict == STOCK
    assert found.vendor == "Siemens"


def test_a_signature_still_names_the_sequence_a_user_label_only_flags() -> None:
    found = identify(scan(sequence="cmrr_mbep2d_bold", owner="USER"), default_catalog())
    assert found.verdict == THIRD_PARTY
    assert found.signature == "cmrr-mb-epi-bold"
    # Both statements are recorded, not just whichever was consulted first.
    assert any("sequence owner 'USER'" in reason for reason in found.evidence)
    assert any("sequence binary" in reason for reason in found.evidence)


def test_a_stated_siemens_owner_contradicting_a_signature_is_not_silently_resolved() -> None:
    # Two disagreeing signals is exactly a scan a person should look at, and
    # that is what 'unrecognized' means. Picking a winner would hide it.
    found = identify(scan(sequence="cmrr_mbep2d_bold", owner="SIEMENS"), default_catalog())
    assert found.verdict == UNRECOGNIZED
    assert any("but the export states sequence owner" in reason for reason in found.evidence)


def test_an_absent_owner_field_changes_nothing() -> None:
    # Only VB17A prints it; the other three releases must behave as before.
    with_owner = identify(scan(sequence="fl", owner="SIEMENS"), default_catalog())
    without = identify(scan(sequence="fl"), default_catalog())
    assert with_owner.verdict == without.verdict == STOCK


@requires_snapshots
def test_the_stated_owner_never_contradicts_the_other_detectors() -> None:
    # The claim that lets the owner field decide: across all 110 VB17A scans
    # it agrees with every signature match and every stock-kernel judgement.
    # A disagreement would surface as an 'unrecognized' verdict here.
    catalog = default_catalog()
    seen = 0
    for name, protocol in GOLDEN_PROTOCOLS:
        for item, raw in zip(identify_protocol(protocol, catalog), protocol["scans"]):
            if not (raw.get("header") or {}).get("sequence_owner"):
                continue
            seen += 1
            assert item.verdict != UNRECOGNIZED, f"{name}: {item.name}"
    assert seen == 110, f"expected every VB17A scan to state an owner, saw {seen}"


#: The only scans in the corpus no signature accounts for, pinned by name so
#: that a *new* unaccounted scan still fails this test. All five are in
#: XA60-Potpourri, which was added for the .exar1 work rather than for
#: sequence detection: each runs a Siemens kernel (``epfid``, ``epse``,
#: ``fl``) carrying MGH's FLEET/ACS modifications, which print
#: sequence-specific parameters no shipped signature claims. Writing
#: signatures for them would mean attributing sequences from inference alone,
#: which the catalog's rule against guessed attributions forbids -- and
#: 'unrecognized' is the honest verdict for a scan a person should look at.
#: The five scans, and the three exports of the one protocol that prints them.
#: Potpourri now ships as P1 and P2 -- the same protocol imported onto two XA60
#: scanners -- plus P1_changed, so the same five recur in each. Kept as a
#: product of two small sets rather than fifteen literals, because the fact
#: being pinned is "these five scans, in every Potpourri export" and a flat
#: list would obscure a case where one export gained a sixth.
UNACCOUNTED_SCANS = {
    "ep2d_bold_mgh",
    "ep2d_diff_mgh",
    "ep2d_se_sms_mgh",
    "ABCD_fMRI_rest_MGH",
    "can_neuromelanin",
}
UNACCOUNTED_EXPORTS = {
    "XA60-Potpourri_P1.json",
    "XA60-Potpourri_P1_changed.json",
    "XA60-Potpourri_P2.json",
}

#: Spectroscopy scans that no signature accounts for, in two VE11C protocols
#: added for their own sake rather than for sequence detection. ``head_csi_fid``
#: and ``SPECIAL_ACC`` are spectroscopy kernels, and ``can_neuromelanin`` here
#: is the VE11C build of a sequence the XA60 examples also print. Naming them
#: is the honest record: writing signatures would mean attributing sequences
#: from inference, which the catalog's rules forbid.
UNACCOUNTED_ELSEWHERE = {
    ("VE11C-31P CSI 20230503 NOE.json", "SPECIAL_ACC"),
    ("VE11C-31P CSI 20230503 NOE.json", "head_csi_fid"),
    ("VE11C-BEEST_SPICE_11112025.json", "can_neuromelanin"),
    # The same 31P protocol exported from XA60. Its SPECIAL_ACC scan resolves
    # there and does not on VE11C, so the two exports differ by one entry.
    ("XA60-31P CSI 20230503 NOE.json", "head_csi_fid"),
}

UNACCOUNTED = {
    (export, scan) for export in UNACCOUNTED_EXPORTS for scan in UNACCOUNTED_SCANS
} | UNACCOUNTED_ELSEWHERE


@requires_snapshots
def test_the_shipped_examples_are_accounted_for_apart_from_a_pinned_few() -> None:
    # Was == 0 before XA60-Potpourri arrived, and is still exact: the set of
    # unaccounted scans is named rather than counted, so a regression that
    # unaccounts a sixth scan -- or that quietly resolves one of these five --
    # fails here. Relaxing this further is a deliberate act; loosening the
    # catalog to make it pass is not the alternative.
    catalog = default_catalog()
    unaccounted = {
        (name, item.name)
        for name, protocol in GOLDEN_PROTOCOLS
        for item in identify_protocol(protocol, catalog)
        if item.verdict == UNRECOGNIZED
    }
    assert unaccounted == UNACCOUNTED


def test_a_scan_flagged_only_by_its_owner_label_describes_without_a_dangling_dash() -> None:
    # The owner label says a sequence is not Siemens' without saying whose it
    # is, so the identification carries a family and no vendor. Joining the
    # two fields directly left the summary reading " -- site-installed ...".
    found = identify(scan(sequence="tse_crusher", owner="USER"), default_catalog())
    assert not found.vendor
    assert not describe(found).startswith(" ")
    assert "--" not in describe(found)


@requires_snapshots
def test_no_family_in_any_report_starts_with_a_separator() -> None:
    catalog = default_catalog()
    for name, protocol in GOLDEN_PROTOCOLS:
        text = render(protocol, identify_protocol(protocol, catalog))
        for line in text.splitlines():
            assert not line.startswith("  - --"), f"{name}: {line}"
