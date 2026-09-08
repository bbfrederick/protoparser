"""The archive reader: does the document describe the file it came from?

Every assertion here is a sweep over the whole corpus rather than a check
against one recorded document. A golden snapshot of an archive would pin the
reader's output to itself and pass however wrong that output was; asking
instead that nothing is lost, that nothing is invented, and that the archive
and its own PDF export agree is what can actually fail.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import EXAR_PROTOCOL_FILES, find_exar, requires_exar
from siemens_protocol import exar
from siemens_protocol.exar import inspect as ins
from siemens_protocol.exar import patch, store
from siemens_protocol.pipeline import ParseOptions, parse_document
from siemens_protocol.sequences import STOCK, THIRD_PARTY, default_catalog


def _leaves(node: object, path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], object]]:
    """Every scalar in a nested document, with the path that reaches it.

    Parameters
    ----------
    node : object
        A nested mapping, or a scalar.
    path : tuple of str, optional
        The keys walked to get here. Default empty.

    Returns
    -------
    list of tuple
        ``(path, value)`` for each scalar.
    """
    if not isinstance(node, dict):
        return [(path, node)]
    found: list[tuple[tuple[str, ...], object]] = []
    for key, value in node.items():
        found.extend(_leaves(value, path + (key,)))
    return found


@requires_exar
def test_nesting_the_parameter_block_loses_nothing(protocol_archive_path: str) -> None:
    """Every ASCCONV assignment survives the tree, exactly once and unchanged.

    The tree is the whole point of the document, and it is built by splitting
    key names, so the failure it invites is silent: a key that cannot be
    placed lands somewhere else, or overwrites a sibling, and the result still
    looks like a protocol. Counting the leaves against the flat table is what
    makes that visible.

    Parameters
    ----------
    protocol_archive_path : str
        One corpus archive holding protocols.

    Returns
    -------
    None
    """
    archive = exar.read(protocol_archive_path)
    checked = 0
    for step in archive.steps:
        if not step.runs_a_protocol:
            continue
        table = ins.ascconv_table(step.protocol.xprotocol)
        if not table:
            continue
        tree = ins.nest(table)
        leaves = _leaves(tree)
        assert len(leaves) == len(table), (
            f"::error::{protocol_archive_path}: {step.name} has {len(table)} assignments "
            f"but {len(leaves)} leaves in the nested tree"
        )
        rebuilt = {".".join(path): value for path, value in leaves}
        assert len(rebuilt) == len(table)
        for key, literal in table.items():
            flattened = key.replace("[", ".").replace("]", "")
            assert (
                rebuilt.get(flattened, rebuilt.get(key)) == literal
            ), f"::error::{protocol_archive_path}: {step.name} lost or changed {key}"
        checked += 1
    assert checked, f"::error::{protocol_archive_path} yielded no protocol to nest"


@requires_exar
def test_an_array_node_is_a_map_because_the_file_makes_it_one(
    protocol_archive_path: str,
) -> None:
    """Indexed elements sit beside ``__attribute__`` and nothing else.

    This is the observation the representation rests on:
    ``sSliceArray.asSlice.__attribute__.size`` shares a node with
    ``sSliceArray.asSlice[0].dThickness``, so an array is a map as well as a
    sequence and cannot be a JSON list. If some export ever puts another
    named member there, the claim in :mod:`.inspect` that ``__attribute__``
    is the only one stops being true and this fails rather than the nesting
    quietly dropping it.

    Parameters
    ----------
    protocol_archive_path : str
        One corpus archive holding protocols.

    Returns
    -------
    None
    """
    archive = exar.read(protocol_archive_path)
    arrays = 0
    for step in archive.steps:
        if not step.runs_a_protocol:
            continue
        indexed: set[tuple[str, ...]] = set()
        named: dict[tuple[str, ...], set[str]] = {}
        for key in ins.ascconv_table(step.protocol.xprotocol):
            parts = ins._tokens(key)
            assert parts is not None, f"::error::{step.name}: {key!r} did not parse"
            for depth, token in enumerate(parts):
                parent = tuple(parts[:depth])
                if token.isdigit():
                    indexed.add(parent)
                else:
                    named.setdefault(parent, set()).add(token)
        for parent in indexed:
            unexpected = named.get(parent, set()) - {ins.ARRAY_ATTRIBUTE}
            assert not unexpected, (
                f"::error::{protocol_archive_path}: {step.name}: array "
                f"{'.'.join(parent)} carries member(s) {sorted(unexpected)} beside its "
                "indices, which the nesting does not expect"
            )
        arrays += len(indexed)
    assert arrays, f"::error::{protocol_archive_path} has no indexed assignment at all"


@requires_exar
def test_the_sequence_tree_prefix_never_contradicts_the_catalog(
    protocol_archive_path: str,
) -> None:
    """``%SiemensSeq%``/``%CustomerSeq%`` agrees with what the catalog knows.

    The prefix is treated as the archive stating who supplied the sequence,
    which is what gives it precedence over an inferred verdict. That standing
    is only earned while it never disagrees: a ``%SiemensSeq%`` binary that
    matches a third-party signature, or a ``%CustomerSeq%`` one listed as a
    Siemens kernel, would mean one of the two is wrong.

    Parameters
    ----------
    protocol_archive_path : str
        One corpus archive holding protocols.

    Returns
    -------
    None
    """
    catalog = default_catalog()
    signature_binaries = {b for s in catalog.signatures for b in s.binaries}
    archive = exar.read(protocol_archive_path)
    for step in archive.steps:
        if not step.runs_a_protocol:
            continue
        stored = ins.sequence_file(step.protocol)
        owner = ins.sequence_owner(stored)
        if not owner:
            continue
        binary = stored.rsplit("\\", 1)[-1]
        if owner == "%SiemensSeq%":
            assert binary not in signature_binaries, (
                f"::error::{protocol_archive_path}: {step.name} runs {binary!r} from the "
                "Siemens tree, but a third-party signature claims that binary"
            )
        else:
            assert binary not in catalog.stock_binaries, (
                f"::error::{protocol_archive_path}: {step.name} runs {binary!r} from the "
                "customer tree, but the catalog lists that binary as a Siemens kernel"
            )


@requires_exar
def test_a_scan_read_from_an_archive_matches_the_export_beside_it() -> None:
    """The archive and its own PDF agree on the scans and their order.

    Pairing the two readers against each other is the only check available
    that does not compare this package with itself. Names and running order
    must match outright; the sequence column deliberately does not, because
    a Numaris/X printout names the kernel (``epfid``) where the archive names
    the sequence file (``cmrr_mbep2d_bold``).

    Returns
    -------
    None
    """
    archive_path = find_exar("Potpourri_P1.exar1")
    pdf = archive_path[: -len(".exar1")] + ".pdf"
    archive = exar.read(archive_path)
    document = ins.as_protocol(archive, archive.programs[0], archive_path)
    printed = parse_document(pdf, ParseOptions()).protocol.to_dict()

    assert [s["name"] for s in document["scans"]] == [s["name"] for s in printed["scans"]]
    assert document["software_version"] == printed["software_version"]


@requires_exar
def test_the_stored_scan_time_agrees_with_the_printed_one() -> None:
    """``lTotalScanTimeSec`` reproduces the ``TA`` the printout shows.

    It is a derived field the console recomputes, so it is used for the
    listing's ``TA`` rather than assumed correct. Where the assignment is
    absent -- the one-second setter scans, whose zero the console omits --
    the archive reports no time rather than reporting zero.

    Returns
    -------
    None
    """
    from siemens_protocol.listing import parse_acquisition_time

    archive_path = find_exar("Potpourri_P1.exar1")
    pdf = archive_path[: -len(".exar1")] + ".pdf"
    archive = exar.read(archive_path)
    printed = {
        s.name: parse_acquisition_time(s.header.get("ta", ""))
        for s in parse_document(pdf, ParseOptions()).protocol.scans
    }
    compared = 0
    for step in archive.programs[0].steps:
        if not step.runs_a_protocol:
            continue
        stored = ins.acquisition_time(step.protocol)
        if not stored:
            continue
        assert parse_acquisition_time(stored) == printed[step.name], (
            f"::error::{step.name}: archive says {stored}, the export prints "
            f"{printed[step.name]} seconds"
        )
        compared += 1
    assert compared >= 14, f"::error::only {compared} scans carried a stored scan time"


@requires_exar
def test_the_links_a_printout_cannot_show_are_reported_by_name() -> None:
    """Every copy reference resolves to the two scans it joins.

    Link endpoints are step ``ObjectId``s, and the archive holds three GUID
    spaces that all look alike; resolving one through the wrong map finds
    nothing without raising. An unresolved endpoint would surface here as a
    raw GUID in place of a scan name.

    Returns
    -------
    None
    """
    archive_path = find_exar("copyparametertest.exar1")
    archive = exar.read(archive_path)
    document = ins.describe(archive, archive_path, ascconv=False)
    program = document["programs"][0]
    names = {step["name"] for step in program["steps"]}

    assert program["links"], "::error::the copy-parameter export carries no links"
    for link in program["links"]:
        assert link["source"] in names, f"::error::unresolved link source {link['source']}"
        assert link["target"] in names, f"::error::unresolved link target {link['target']}"
    groups = {link["group"] for link in program["links"] if "group" in link}
    assert len(groups) >= 10, f"::error::only {len(groups)} distinct link groups decoded"


@requires_exar
def test_reading_every_corpus_archive_yields_a_document(protocol_archive_path: str) -> None:
    """The document builds, serializes, and names every step it walked.

    Parameters
    ----------
    protocol_archive_path : str
        One corpus archive holding protocols.

    Returns
    -------
    None
    """
    archive = exar.read(protocol_archive_path)
    document = ins.describe(archive, protocol_archive_path, ascconv=False)
    text = json.dumps(document, ensure_ascii=False)
    assert text

    assert document["programs"], f"::error::{protocol_archive_path} produced no programs"
    for program in document["programs"]:
        assert program["step_count"] == len(program["steps"])
        for step in program["steps"]:
            if not step["runs_a_protocol"]:
                continue
            assert step["header"], f"::error::{step['name']} has an empty header"
            assert step["provenance"]["verdict"] in (STOCK, THIRD_PARTY, "unrecognized")


@requires_exar
def test_reading_one_protocol_out_of_an_archive_never_guesses() -> None:
    """A lone protocol is taken; several, or a wrong name, are refused.

    Returning the first of several would describe one protocol while looking
    like a reading of the whole file, which is the failure
    :attr:`..exar.archive.Archive.program` exists to prevent. The
    several-programs case is a real one -- ``Frederick_P2`` is an
    investigator-level export holding 31 -- so it is driven rather than
    staged. Only the empty archive is staged, because the corpus no longer
    carries one.

    Returns
    -------
    None
    """
    from siemens_protocol.cli import _select_program

    archive = exar.read(find_exar("Potpourri_P1.exar1"))
    only = archive.programs[0]
    assert _select_program(archive, None, "x").name == only.name
    assert _select_program(archive, only.name, "x").name == only.name
    with pytest.raises(ValueError, match="holds no protocol named"):
        _select_program(archive, "not a protocol here", "x")

    several = exar.read(find_exar("Frederick_P2.exar1"))
    names = [program.name for program in several.programs]
    assert len(names) > 1, "::error::Frederick_P2 no longer holds several protocols"
    with pytest.raises(ValueError, match=f"holds {len(names)} protocols"):
        _select_program(several, None, "backup.exar1")
    assert _select_program(several, names[-1], "backup.exar1").name == names[-1]

    class _Empty:
        """An archive exported from an empty folder node, which reads fine."""

        programs: list = []

    with pytest.raises(ValueError, match="holds no protocol"):
        _select_program(_Empty(), None, "empty.exar1")


@requires_exar
def test_a_multi_program_archive_describes_every_protocol_it_holds() -> None:
    """Each program gets its own steps, not a share of one running order.

    A backup's programs each run a fraction of the file's steps, and a step
    can be run by more than one -- ``Frederick_P2`` shares 67 of its 435 --
    so a reader that walked the archive's steps once and split them would
    look right on a single-protocol export and be wrong here.

    Returns
    -------
    None
    """
    archive = exar.read(find_exar("Frederick_P2.exar1"))
    document = ins.describe(archive, "Frederick_P2.exar1", ascconv=False)
    assert document["program_count"] == len(document["programs"]) > 1
    for program, decoded in zip(document["programs"], archive.programs):
        assert program["name"] == decoded.name
        assert [s["name"] for s in program["steps"]] == [s.name for s in decoded.steps]
        assert program["steps"], f"::error::{program['name']} came back with no steps"


@requires_exar
def test_the_archive_subcommand_writes_beside_the_archive(tmp_path: Path) -> None:
    """The command runs end to end and does not clobber a parsed PDF.

    ``Potpourri_P1.exar1`` and ``Potpourri_P1.pdf`` share a stem, so replacing
    the extension the way ``parse`` does would let one overwrite the other.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Pytest's temporary directory.

    Returns
    -------
    None
    """
    import shutil

    source = find_exar("copyparametertest.exar1")
    copied = tmp_path / "copyparametertest.exar1"
    shutil.copyfile(source, copied)

    result = subprocess.run(
        [sys.executable, "-m", "siemens_protocol.cli", "archive", str(copied), "--no-ascconv"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    written = tmp_path / "copyparametertest.exar1.json"
    assert written.is_file(), f"::error::nothing written; stderr was {result.stderr}"
    document = json.loads(written.read_text(encoding="utf-8"))
    assert document["format"] == "exar1"
    assert document["programs"][0]["links"]


@requires_exar
def test_every_corpus_archive_pairs_its_preview_labels_with_the_patcher() -> None:
    """The printed-label view uses the labels ``patch.resolve`` looks up.

    The two read ``Preview`` for different reasons -- one to display a
    parameter, one to write it -- and a label spelled differently in the
    document than in the mapping table would make the JSON impossible to act
    on with the rest of this package.

    Returns
    -------
    None
    """
    archive = exar.read(find_exar("Potpourri_P1.exar1"))
    seen = 0
    for step in archive.programs[0].steps:
        if not step.runs_a_protocol:
            continue
        view = ins.printed_view(step.protocol)
        for label in ("TR", "Slice Thickness", "FOV Read"):
            if label not in view:
                continue
            mapping, _reason = patch.resolve(step.protocol, label)
            assert mapping is not None, (
                f"::error::{step.name}: {label!r} is in the document's printed view "
                "but resolves to no mapping"
            )
            seen += 1
    assert seen, "::error::no printed label was checked against the mapping table"


def test_the_corpus_offers_archives_to_sweep() -> None:
    """Discovery found archives, so the sweeps above are not skipping silently.

    A skip reads like a pass, and removing the shipped archives would make
    every check in this module vanish rather than fail.

    Returns
    -------
    None
    """
    assert EXAR_PROTOCOL_FILES, (
        "::error::no .exar1 archives with protocols were discovered, so this module "
        "asserted nothing"
    )


#: Returned scans whose ``.prot`` name did not follow ``alFree[15]``.
#:
#: Both are scans the driver moved from 2 to 1, and both came back with the
#: ``_ABCD_*`` name standing, where the same edit in ``driver_loadtest`` came
#: back rewritten to the bare one. Pinned rather than excluded so a third
#: fails: what differs between the two imports is the open question.
KNOWN_PROT_DISAGREEMENTS = {
    ("NAV_optionscan_P1_loadtest.exar1", "T09_ABCD_navigator_Off"),
    ("NAV_optionscan_P1_loadtest.exar1", "T19_ABCD_navigator_Off"),
}

#: The vNav *setter* binaries, which configure a navigator rather than run
#: one. Neither carries ``sWipMemBlock.alFree[15]`` on any corpus scan --
#: `ep_moco_nav_set` on 7, `ep_moco_nav_set_ABCD` on 144 -- so a `.prot` name
#: on one of these says which vNav it sets up, not anything about a flag.
SETTER_BINARIES = ("ep_moco_nav_set", "ep_moco_nav_set_ABCD")


def base_binary(protocol: object) -> str:
    """The sequence binary a protocol runs, without prefix or subdirectory.

    ``tSequenceFileName`` may carry a subdirectory under the owner prefix --
    ``%CustomerSeq%\\MGH_Moco\\ep_moco_nav_set`` -- so the last component is
    the binary and a prefix test on the whole field is not.

    Parameters
    ----------
    protocol : Protocol
        The protocol to read.

    Returns
    -------
    str
        The binary name.
    """
    return ins.sequence_file(protocol).rsplit("\\", 1)[-1]


#: The ``.prot`` name each ``alFree[15]`` value pairs with on the navigators.
PROT_FOR_FLAG = {"2": "_ABCD_", "1": "Prisma_epi_moco_navigator.prot"}


@requires_exar
def test_the_navigator_prot_name_tracks_its_flag_on_console_authored_scans(
    protocol_archive_path: str,
) -> None:
    """``alFree[15]`` and the ``.prot`` name agree wherever we did not write.

    ``tFree`` is churn and nothing reads it, so this is not protecting a
    mapping -- it is protecting the *reason* the field is churn. The rule is
    exception-free across every console-authored navigator scan in the
    corpus, and the only two scans that break it are ones the driver edited,
    which is what says the rewrite happens on import rather than being a
    property of the value. A console-authored counterexample would overturn
    that reading, so it has to fail rather than pass quietly.

    Parameters
    ----------
    protocol_archive_path : str
        One corpus archive holding protocols.

    Returns
    -------
    None
    """
    name = os.path.basename(protocol_archive_path)
    ours = "loadtest" in name
    archive = exar.read(protocol_archive_path)
    checked = 0
    for step in archive.steps:
        if not step.runs_a_protocol:
            continue
        stamp = patch.sequence_stamp(step.protocol)
        if not stamp.endswith(".prot"):
            continue
        flag = patch.read_ascconv(step.protocol.xprotocol, "sWipMemBlock.alFree[15]")
        if flag is None:
            # ``alFree[15]`` is an ABCD-generation field. Every navigator
            # whose binary ends `_ABCD` carries it -- space_mgh_epinav_ABCD,
            # tfl_mgh_epinav_ABCD, tse_vfl_mgh_epinav_ABCD, 226 scans with no
            # exception -- and the older navigators never do:
            # tfl_mgh_multiecho_epinav, tfl_multiecho_epinav_711 and
            # tse_vfl_mgh_epinav write a `.prot` name with no flag at all.
            # Both setters are outside it whatever they are called, the
            # `_ABCD` one included, so being a setter is checked first.
            binary = base_binary(step.protocol)
            assert binary in SETTER_BINARIES or not binary.endswith("_ABCD"), (
                f"::error::{name}: {step.name} runs {binary}, an ABCD navigator "
                "writing a .prot name with no alFree[15], which the rule does not cover"
            )
            continue
        expected = PROT_FOR_FLAG.get(flag.strip())
        assert (
            expected is not None
        ), f"::error::{name}: {step.name} holds an unseen alFree[15] of {flag!r}"
        agrees = expected in stamp if expected.startswith("_") else stamp == expected
        if not agrees and ours and (name, step.name) in KNOWN_PROT_DISAGREEMENTS:
            continue
        assert agrees, (
            f"::error::{name}: {step.name} holds alFree[15]={flag.strip()} beside "
            f"{stamp!r}, which the flag does not pair with"
        )
        checked += 1


@requires_exar
def test_the_prot_rule_is_actually_exercised_by_the_corpus() -> None:
    """The sweep above sees both flag values on console-authored scans.

    A rule tested only where the flag never varies passes by everything
    agreeing, which is the way this check could be worthless without looking
    it. Both values must appear, away from the archives we wrote.

    Returns
    -------
    None
    """
    seen: dict[str, int] = {}
    for path, _version in EXAR_PROTOCOL_FILES:
        if "loadtest" in os.path.basename(path):
            continue
        archive = exar.read(path)
        for step in archive.steps:
            if not step.runs_a_protocol:
                continue
            if not patch.sequence_stamp(step.protocol).endswith(".prot"):
                continue
            flag = patch.read_ascconv(step.protocol.xprotocol, "sWipMemBlock.alFree[15]")
            if flag is not None:
                seen[flag.strip()] = seen.get(flag.strip(), 0) + 1
    assert set(seen) == {"1", "2"}, f"::error::console-authored scans show alFree[15] {seen}"
    assert sum(seen.values()) >= 50, f"::error::only {sum(seen.values())} scans exercised the rule"


@requires_exar
def test_the_live_set_comes_from_the_element_map(protocol_archive_path: str) -> None:
    """Every element the head's map names resolves to a live instance.

    Reading the live set from ``InstanceChangeSet`` instead describes the last
    save's delta and calls it the file. On an archive written in one changeset
    the two agree, which is why that defect survived the whole corpus; on a
    whole-scanner export written by twelve successive saves it yields 21
    instances of 31164 and looks like a small archive rather than a failure.

    Parameters
    ----------
    protocol_archive_path : str
        One corpus archive holding protocols.

    Returns
    -------
    None
    """
    from siemens_protocol.exar.archive import _element_map, _head_branch

    container = store.read(protocol_archive_path)
    _baseline, head = _head_branch(container)
    named = _element_map(container, head)
    assert named, f"::error::{protocol_archive_path}: the head changeset names no element map"

    archive = exar.read(protocol_archive_path)
    rows = {str(row["Id"]) for row in container.rows("Instance")}
    assert set(archive.instances) == named & rows, (
        f"::error::{protocol_archive_path}: the archive's live set is not what the "
        "head changeset's element map resolves to"
    )
    touched = {
        str(row["InstanceId"])
        for row in container.rows("InstanceChangeSet")
        if str(row.get("ChangeSetId", "")) == head
    }
    assert touched <= set(archive.instances), (
        f"::error::{protocol_archive_path}: the head changeset touched instances the "
        "element map does not resolve, so the map is not the whole live set"
    )


@requires_exar
def test_every_program_and_directory_has_a_path_through_the_tree(
    protocol_archive_path: str,
) -> None:
    """The folder hierarchy is recoverable, and reaches every node.

    It is not where the node structure suggests: an ``EdfDirectory`` carries
    no ``Children`` in any corpus archive and an ``EdfProgram`` no
    ``ParentElementId``, which is what made the tree look flat. It lives in
    the root's ``ParentDirectoryId`` map, keyed in two GUID spaces at once --
    a directory under its ``ObjectId``, a program under its ``Element_id`` --
    so resolving every key in one space finds the folders, misses every
    protocol, and reads as a tree of empty folders.

    Parameters
    ----------
    protocol_archive_path : str
        One corpus archive holding protocols.

    Returns
    -------
    None
    """
    archive = exar.read(protocol_archive_path)
    parents = archive.directory_parents
    assert parents, f"::error::{protocol_archive_path} records no directory parents"

    for node in archive.program_nodes:
        path = archive.path_of(node, parents)
        assert len(path) > 1, (
            f"::error::{protocol_archive_path}: program {archive.label_of(node)!r} has no "
            f"folder above it, path came back as {path}"
        )
        assert path[-1] == archive.label_of(node)
        assert all(part for part in path), f"::error::unnamed folder in {path}"

    directories = [i for i in archive.instances.values() if i.kind == exar.archive.DIRECTORY]
    assert directories, f"::error::{protocol_archive_path} has no directory nodes"
    roots = [d for d in directories if len(archive.path_of(d, parents)) == 1]
    assert len(roots) == 1, (
        f"::error::{protocol_archive_path}: {len(roots)} directories sit at the top of the "
        "tree, so the parent map does not describe one hierarchy"
    )


@requires_exar
def test_the_archive_path_agrees_with_the_one_the_printout_shows() -> None:
    """The recovered path matches the export's, component for component.

    The two roots differ -- the archive says ``Root/Export`` where the page
    prints ``\\\\Research`` -- so the check is on the components below that,
    which are the investigator, the protocol and the scan.

    Returns
    -------
    None
    """
    archive_path = find_exar("Potpourri_P1.exar1")
    pdf = archive_path[: -len(".exar1")] + ".pdf"
    archive = exar.read(archive_path)
    document = ins.describe(archive, archive_path, ascconv=False)
    printed = parse_document(pdf, ParseOptions()).protocol.scans[0]

    tail = document["programs"][0]["steps"][0]["path"].split("/")[-3:]
    assert tail == printed.path.replace("\\", "/").split("/")[-3:], (
        f"::error::archive path {document['programs'][0]['steps'][0]['path']!r} disagrees "
        f"with the printed {printed.path!r}"
    )
