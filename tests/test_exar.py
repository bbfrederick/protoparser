"""Round-trip tests for the ``.exar1`` reader.

Nothing about this format is documented, so every claim the reader makes is
asserted here against a real export rather than against a specification. The
tests are deliberately structural -- compression, hashing, GUID layout,
ordering -- because those are what a writer will depend on, and because a
mistake in any of them produces plausible output rather than an error.
"""

from __future__ import annotations

import os
import pathlib
import re
import sqlite3

import pytest

from conftest import (  # noqa: F401  (fixtures)
    EXAR_PROTOCOL_FILES,
    PARAMCHECK_PAIRS,
    archive_path,
    find_exar,
    protocol_archive_path,
    requires_exar,
    requires_paramcheck,
)
from siemens_protocol import exar
from siemens_protocol.exar import archive, build, envelope, patch, store
from siemens_protocol.pipeline import parse_document

#: The double that used to be the one divergence between our serializer and
#: Newtonsoft's, before ``envelope.dotnet_double`` reproduced .NET's rule.
#: Kept as a fixture: it is the value that distinguishes the two formats.
DOTNET_SPELLING = "2.8936200141906738"


# --------------------------------------------------------------------------
# The envelope: DEFLATE, the type header, and SHA-1 addressing
# --------------------------------------------------------------------------


@requires_exar
def test_every_content_blob_is_a_deflated_edf_envelope(archive_path: str) -> None:
    """Each ``Content`` row inflates to a typed header plus a JSON document.

    Parameters
    ----------
    archive_path : str
        Archive under test.

    Returns
    -------
    None
    """
    connection = sqlite3.connect(archive_path)
    try:
        rows = connection.execute("SELECT Hash, Format, Data FROM Content").fetchall()
    finally:
        connection.close()
    assert rows, "archive holds no content"
    for _hash, stored_format, blob in rows:
        assert stored_format == envelope.STORED_FORMAT
        decoded = envelope.parse(blob)
        assert decoded.content_type.startswith("syngo.MR.ExamDataFoundation.Data.")
        assert isinstance(decoded.decode(), dict)


@requires_exar
def test_content_hash_is_sha1_of_the_decompressed_envelope(archive_path: str) -> None:
    """``Content.Hash`` addresses the header-plus-JSON bytes, not the blob.

    This is the whole write path in one assertion: content the scanner will
    accept has to be hashed the same way, or the archive's own references stop
    resolving.

    Parameters
    ----------
    archive_path : str
        Archive under test.

    Returns
    -------
    None
    """
    connection = sqlite3.connect(archive_path)
    try:
        rows = connection.execute("SELECT Hash, Data FROM Content").fetchall()
    finally:
        connection.close()
    for stored_hash, blob in rows:
        assert envelope.parse(blob).hash == stored_hash


@requires_exar
def test_an_untouched_envelope_re_stores_byte_for_byte(archive_path: str) -> None:
    """Reading and writing content that was not edited changes nothing.

    Recompressing would not reproduce the console's DEFLATE stream, so the
    original blob is kept rather than regenerated. Losing that would make
    every round trip rewrite every row.

    Parameters
    ----------
    archive_path : str
        Archive under test.

    Returns
    -------
    None
    """
    connection = sqlite3.connect(archive_path)
    try:
        blobs = [row[0] for row in connection.execute("SELECT Data FROM Content")]
    finally:
        connection.close()
    for blob in blobs:
        assert envelope.parse(blob).to_stored() == blob


@requires_exar
def test_re_encoding_reproduces_the_console_json(archive_path: str) -> None:
    """Python's serializer matches Newtonsoft's, apart from one known double.

    Two-space indentation with CRLF endings is not a guess: it reproduces
    every stored document exactly, which is what makes an edited document
    writable. The single exception is a double that .NET's round-trip format
    spells with seventeen significant digits.

    Parameters
    ----------
    archive_path : str
        Archive under test.

    Returns
    -------
    None
    """
    connection = sqlite3.connect(archive_path)
    try:
        blobs = [row[0] for row in connection.execute("SELECT Data FROM Content")]
    finally:
        connection.close()
    mismatched = []
    for blob in blobs:
        decoded = envelope.parse(blob)
        if envelope.dumps(decoded.decode()) != decoded.payload:
            mismatched.append(decoded)
    assert (
        not mismatched
    ), "re-encoding no longer reproduces the console byte for byte: " + ", ".join(
        sorted({d.kind for d in mismatched})
    )


def test_doubles_are_spelled_the_way_dotnet_spells_them() -> None:
    """.NET writes fifteen significant digits, or seventeen when it must.

    Python's ``repr`` gives the shortest round-tripping form, which differs
    for some doubles -- the field strength in these protocols is the case.
    Getting this wrong is cosmetic (both parse to the same value, and a
    scanner accepted the Python spelling) but it re-addresses the content,
    so a no-op re-encode would no longer hash to where it came from.

    Returns
    -------
    None
    """
    assert envelope.dotnet_double(2.8936200141906738) == DOTNET_SPELLING
    assert repr(2.8936200141906738) != DOTNET_SPELLING
    # A whole number still has to read as a double.
    assert envelope.dotnet_double(650.0) == "650.0"
    assert envelope.dotnet_double(0.5) == "0.5"


def test_a_document_carrying_the_float_marker_is_refused() -> None:
    """The substitution must not be ambiguous.

    Floats are carried through the encoder as marked strings, so a document
    already containing that marker would be rewritten unpredictably.

    Returns
    -------
    None
    """
    with pytest.raises(ValueError, match="float marker"):
        envelope.dumps({"Data": f"text {envelope._FLOAT_MARK} more"})


def test_a_blob_that_is_not_an_envelope_is_rejected() -> None:
    """Content without an ``EDF V1:`` header raises rather than half-parsing.

    Returns
    -------
    None
    """
    with pytest.raises(ValueError, match="EDF V1"):
        envelope.parse(envelope.compress(b'{"$id": "1"}'))


def test_an_edited_document_re_encodes_to_a_fresh_hash() -> None:
    """Replacing a payload drops the stored blob and re-addresses the content.

    Returns
    -------
    None
    """
    original = envelope.build("syngo.MR.ExamDataFoundation.Data.EdfStringContent", {"$id": "1"})
    edited = original.replace({"$id": "1", "Texts": {"": "renamed"}})
    assert edited.hash != original.hash
    assert edited.stored is None
    assert envelope.parse(edited.to_stored()).decode() == {"$id": "1", "Texts": {"": "renamed"}}


# --------------------------------------------------------------------------
# GUID layout
# --------------------------------------------------------------------------


def test_children_guids_use_the_dotnet_byte_layout() -> None:
    """Packed child ids are little-endian in their first three fields.

    Reading them as plain big-endian bytes yields well-formed GUIDs that match
    no element, so the mistake shows up as an empty tree rather than an error.

    Returns
    -------
    None
    """
    identifier = "05eeb1a9-69db-4054-af47-bd771cc0f00f"
    packed = archive.pack_guids([identifier])
    assert packed[:4] == bytes.fromhex("a9b1ee05")
    assert archive.unpack_guids(packed) == [identifier]


def test_unpacking_no_children_yields_no_ids() -> None:
    """A node with a null or empty ``Children`` blob simply has no children.

    Returns
    -------
    None
    """
    assert archive.unpack_guids(None) == []
    assert archive.unpack_guids(b"") == []


# --------------------------------------------------------------------------
# The tree
# --------------------------------------------------------------------------


@requires_exar
def test_the_archive_reads_at_a_real_branch_head(protocol_archive_path: str) -> None:
    """The placeholder branch is never chosen, and the head has live instances.

    Every archive carries a second branch whose baseline is ``-`` and which
    resolves to nothing. Reading at that head yields an empty tree that looks
    like a corrupt file.

    Parameters
    ----------
    protocol_archive_path : str
        Archive under test.

    Returns
    -------
    None
    """
    loaded = exar.read(protocol_archive_path)
    assert loaded.baseline != archive.PLACEHOLDER_BASELINE
    assert loaded.major_version.startswith("VA")
    assert loaded.instances
    # ``program`` raises rather than guess when there are several, which is
    # the right behaviour and the wrong question here: an investigator-level
    # export holds 31 of them and is not thereby unreadable.
    assert loaded.program_nodes


@requires_exar
def test_step_order_is_the_link_chain_not_the_children_blob(protocol_archive_path: str) -> None:
    """Running order comes from the program's linked list.

    The program's ``Children`` blob holds the same steps in a different order.
    Both produce a full set of protocols with all the right values, so the
    wrong one is indistinguishable from the right one by spot check -- which
    is exactly why it is asserted.

    Only the children that are steps take part. A program may hold others --
    the converted ``K23EB_20210802`` holds two ``EdfString`` children -- and
    the chain can only ever contain steps, so comparing it against every child
    reports such an archive as broken rather than the reader as narrow.

    Parameters
    ----------
    protocol_archive_path : str
        Archive under test.

    Returns
    -------
    None
    """
    loaded = exar.read(protocol_archive_path)
    assert loaded.program_nodes, "archive carries protocols but no program"
    by_element = loaded.by_element
    for program in loaded.program_nodes:
        chain = loaded.step_order(program)
        stored = [
            by_element[c].object_id
            for c in program.children
            if c in by_element and by_element[c].kind in archive.STEP_KINDS
        ]
        assert sorted(chain) == sorted(stored), "the two orders must hold the same steps"
        content = loaded.document(program)
        assert chain[0] == content["FirstStepId"]
        assert chain[-1] == content["LastStepId"]


@requires_exar
def test_some_archive_stores_its_steps_out_of_running_order() -> None:
    """At least one program in the corpus proves the two orders differ.

    This is the anti-vacuity half of the check above, and it belongs to the
    corpus rather than to each file. A program whose stored order already
    matches its running order cannot detect reading the ``Children`` blob
    instead of the chain, and a single-scan protocol never can -- one step is
    in the same order either way. Asserting it per archive therefore fails on
    a perfectly good export the moment one arrives, which is how this was
    found. Asserting it nowhere would let the whole corpus drift into orders
    that agree, leaving the check above passing for the wrong reason.

    Returns
    -------
    None
    """
    differing = 0
    for path, _version in EXAR_PROTOCOL_FILES:
        loaded = exar.read(path)
        by_element = loaded.by_element
        for program in loaded.program_nodes:
            chain = loaded.step_order(program)
            stored = [
                by_element[c].object_id
                for c in program.children
                if c in by_element and by_element[c].kind in archive.STEP_KINDS
            ]
            differing += chain != stored
    assert differing, "every program stores its steps in running order; the chain is unexercised"


@requires_exar
def test_every_step_is_named_and_measurement_steps_hold_a_protocol(
    protocol_archive_path: str,
) -> None:
    """Names resolve, and a step holds a protocol exactly when it acquires one.

    The running order mixes two kinds. An ``EdfMeasurementStep`` runs a
    protocol; an ``EdfPauseStep`` is an instruction an operator put between
    scans -- "Count down with RA to start of scan" -- and holds none. Both are
    named and both are in the chain, so walking scans means skipping the
    second rather than assuming it cannot happen.

    The two signals are checked against each other: the instance kind and the
    presence of a protocol must agree, since either alone could be wrong.

    Parameters
    ----------
    protocol_archive_path : str
        Archive under test.

    Returns
    -------
    None
    """
    steps = exar.read(protocol_archive_path).steps
    assert steps
    for step in steps:
        assert step.name, f"step {step.instance.object_id} has no name"
        # The kind and the content must agree about whether this step scans.
        # Testing "is it a pause" instead was answering a narrower question and
        # failed the moment a second non-acquiring kind turned up.
        assert step.acquires == step.runs_a_protocol, (
            f"{step.name}: kind is {step.instance.kind} but "
            f"{'no ' if not step.runs_a_protocol else ''}protocol is attached"
        )
        assert step.instance.kind in archive.STEP_KINDS, (
            f"{step.name}: {step.instance.kind} is in a running order but is not "
            "a declared step kind"
        )
        if step.runs_a_protocol:
            assert step.protocol.xprotocol.startswith("<XProtocol>")


@requires_exar
def test_a_pause_step_is_a_real_thing_the_corpus_contains() -> None:
    """At least one archive exercises the protocol-less step.

    A branch nothing reaches is a claim about the code rather than the format,
    and this one was written only because three archives arrived carrying
    eleven pause steps each and the reader raised on all of them.

    Returns
    -------
    None
    """
    archive = exar.read(find_exar("CHR-MDD.exar1"))
    pauses = [step for step in archive.steps if step.is_pause]
    assert pauses, "no pause step in this archive, so the handling is untested"
    assert all(not step.protocols for step in pauses)
    assert all(step.name for step in pauses)


@requires_exar
def test_protocol_previews_carry_the_labels_the_pdf_prints(archive_path: str) -> None:
    """``Preview`` is a per-protocol map from printed label to protocol path.

    That map is what lets a PDF value be located in the protocol without a
    hand-written table for every parameter.

    Parameters
    ----------
    archive_path : str
        Archive under test.

    Returns
    -------
    None
    """
    for step in exar.read(archive_path).steps:
        if not step.runs_a_protocol:
            continue
        entries = step.protocol.preview
        assert entries
        assert "$id" not in entries
        matched = step.protocol.by_label("TR")
        assert matched, f"{step.name} has no TR preview entry"
        assert matched[0].unit == "ms"
        assert isinstance(matched[0].value, (int, float))


@requires_exar
def test_a_protocol_still_carries_an_ascconv_block(archive_path: str) -> None:
    """Numaris/X wraps XProtocol rather than replacing it.

    Parameters
    ----------
    archive_path : str
        Archive under test.

    Returns
    -------
    None
    """
    for step in exar.read(archive_path).steps:
        if not step.runs_a_protocol:
            continue
        text = step.protocol.xprotocol
        assert "### ASCCONV BEGIN" in text
        assert "### ASCCONV END" in text


# --------------------------------------------------------------------------
# Round trip
# --------------------------------------------------------------------------


@requires_exar
def test_an_archive_round_trips_through_a_written_file(
    archive_path: str, tmp_path: pathlib.Path
) -> None:
    """Reading and writing an archive preserves every table row for row.

    The file is not compared byte for byte: sqlite lays out pages and
    freelists as it sees fit, and the console's own exporter is not
    byte-stable either. What has to survive is the content the scanner reads.

    Parameters
    ----------
    archive_path : str
        Archive under test.
    tmp_path : pathlib.Path
        Destination directory supplied by pytest.

    Returns
    -------
    None
    """
    destination = os.path.join(str(tmp_path), "written.exar1")
    exar.read(archive_path).write(destination)
    before = store.read(archive_path)
    after = store.read(destination)
    assert sorted(before.tables) == sorted(after.tables)
    assert sorted(before.indexes) == sorted(after.indexes)
    for name, table in before.tables.items():
        rewritten = after.tables[name]
        assert rewritten.sql == table.sql, f"{name} schema changed"
        assert rewritten.columns == table.columns, f"{name} columns changed"
        assert rewritten.rows == table.rows, f"{name} rows changed"


@requires_exar
def test_a_written_archive_reads_back_to_the_same_tree(
    archive_path: str, tmp_path: pathlib.Path
) -> None:
    """The round trip preserves what the reader exposes, not just the bytes.

    Parameters
    ----------
    archive_path : str
        Archive under test.
    tmp_path : pathlib.Path
        Destination directory supplied by pytest.

    Returns
    -------
    None
    """
    destination = os.path.join(str(tmp_path), "written.exar1")
    original = exar.read(archive_path)
    original.write(destination)
    written = exar.read(destination)
    assert written.baseline == original.baseline
    assert written.head == original.head
    assert [s.name for s in written.steps] == [s.name for s in original.steps]
    assert [s.protocol.xprotocol for s in written.steps if s.runs_a_protocol] == [
        s.protocol.xprotocol for s in original.steps if s.runs_a_protocol
    ]


@requires_exar
def test_storage_classes_survive_the_round_trip(archive_path: str, tmp_path: pathlib.Path) -> None:
    """Text stays text and blobs stay blobs.

    Sqlite is dynamically typed, so a GUID written back as a blob would still
    store and still look right, but would no longer equal the same GUID held
    as text anywhere else in the file.

    Parameters
    ----------
    archive_path : str
        Archive under test.
    tmp_path : pathlib.Path
        Destination directory supplied by pytest.

    Returns
    -------
    None
    """
    destination = os.path.join(str(tmp_path), "written.exar1")
    exar.read(archive_path).write(destination)
    query = "SELECT DISTINCT typeof(Id), typeof(Children), typeof(ContentHash) FROM Instance"

    def classes(path: str) -> list[tuple[str, ...]]:
        """Return the distinct storage classes of the columns under test.

        Parameters
        ----------
        path : str
            Archive to inspect.

        Returns
        -------
        list of tuple
            One tuple per distinct combination, sorted.
        """
        connection = sqlite3.connect(path)
        try:
            return sorted(connection.execute(query).fetchall())
        finally:
            connection.close()

    assert classes(destination) == classes(archive_path)


# --------------------------------------------------------------------------
# Agreement with the PDF export of the same protocol tree
# --------------------------------------------------------------------------


@requires_exar
def test_the_archive_scan_list_matches_its_pdf_export() -> None:
    """The tree walk reproduces the PDF's scan list exactly and in order.

    This is the join the whole generator rests on. Storage order also yields
    all eighteen scans with all the right values, just permuted, so an
    order-insensitive check here would pass on a broken walk.

    Returns
    -------
    None
    """
    archive_file = find_exar("Potpourri_P2.exar1")
    pdf = os.path.splitext(archive_file)[0] + ".pdf"
    if not os.path.isfile(pdf):
        pytest.skip(f"no PDF export beside {os.path.basename(archive_file)}")
    steps = exar.read(archive_file).steps
    scans = parse_document(pdf).protocol.scans
    assert [s.name for s in steps] == [s.name for s in scans]


# --------------------------------------------------------------------------
# Prescription links: the relation graph the PDF does not record
# --------------------------------------------------------------------------

#: Fields the console rewrites on every save. Two protocols that differ only
#: in these are the same protocol -- the GUID leading ``sWipMemBlock.tFree``
#: and the date-and-time hiding in ``sSpecPara.lFinalMatrixSize*``.
LINK_CHURN = re.compile(r"tCheckUUID|tFree|lFinalMatrixSize")


def _ascconv_literals(text: str) -> dict[str, str]:
    """Return an XProtocol's ASCCONV assignments as ``{key: literal}``.

    Parameters
    ----------
    text : str
        XProtocol text, ASCCONV block included.

    Returns
    -------
    dict of str to str
        One entry per assignment, values stripped of surrounding whitespace.
    """
    found = {}
    for line in text.splitlines():
        match = re.match(r"^(\S+)\s*=\s*(.*?)\s*$", line)
        if match:
            found[match.group(1)] = match.group(2)
    return found


@requires_exar
def test_every_relation_in_the_corpus_decodes(protocol_archive_path: str) -> None:
    """Relations resolve to steps of their own program and use known spellings.

    The source and target are step *object* ids -- the GUID space the JSON
    payloads use, not the one ``Children`` refers to -- so indexing them the
    wrong way finds nothing rather than raising. Checking they resolve is what
    pins that down.

    ``extra`` must stay empty: it collects payload attributes this release
    does not name, so anything landing in it is a spelling the table has not
    seen and a mapping that would otherwise be dropped in silence.

    Parameters
    ----------
    protocol_archive_path : str
        Archive under test.

    Returns
    -------
    None
    """
    loaded = exar.read(protocol_archive_path)
    for program in loaded.programs:
        known = {step.instance.object_id for step in program.steps}
        for link in program.links:
            assert link.source in known, f"{program.name}: relation from an unknown step"
            assert link.target in known, f"{program.name}: relation to an unknown step"
            assert link.source != link.target, f"{program.name}: a step linked to itself"
            assert not link.extra, f"unnamed payload attributes {sorted(link.extra)}"
            if link.is_copy_reference:
                assert link.group in archive.COPY_REFERENCE_GROUPS, f"new group {link.group!r}"
                assert link.constraint == 1
            elif link.kind == archive.SPLIT_JOIN:
                # The branch bracket, which describes structure rather than a
                # parameter: it joins the split step to the join step and
                # carries no payload.
                kinds = {step.instance.object_id: step.instance.kind for step in program.steps}
                assert {kinds[link.source], kinds[link.target]} == {
                    archive.SPLIT_STEP,
                    archive.JOIN_STEP,
                }, "a SplitJoin relation should bracket a split and a join"
                assert (link.constraint, link.group) == (0, None)
            else:
                # The payload-less shape ``31P CSI`` and the converted
                # ``K23EB_20210802`` both carry. Unexplained -- each shadows a
                # real ``CopyReference`` between the same pair and is
                # duplicated several deep -- so what is asserted is only that
                # it stays as observed.
                assert (link.kind, link.constraint, link.group) == ("", 0, None)
            assert link.state == ""


@requires_exar
def test_the_copy_parameter_export_exercises_every_link_group() -> None:
    """One protocol covers the whole vocabulary, one scan per menu item.

    ``copyparametertest`` was acquired for exactly this: twelve scans slaved
    to a thirteenth, each by a different copy-reference method. Without it the
    groups seen in the corpus are three, and the constants would be a list of
    what happened to turn up in clinical use rather than the console's menu.

    Coverage is asserted as equality against :data:`COPY_REFERENCE_GROUPS`
    rather than containment, so a menu item this export stops exercising fails
    here too. The first version of the export had ten links and no
    adjustment-volume scan, which made ``AdjustmentVolume`` look like a group
    the console does not have.

    Returns
    -------
    None
    """
    program = exar.read(find_exar("copyparametertest.exar1")).programs[0]
    links = program.copy_references
    assert len(links) == 12
    assert len({link.source for link in links}) == 1, "the links should form one star"

    assert {link.group for link in links} == set(archive.COPY_REFERENCE_GROUPS)
    # The flags are orthogonal to the group rather than further values of it:
    # both scans exercising one are ordinary centre links with a box ticked.
    flagged = {
        link.group for link in links if link.copies_phase_encoding_direction or link.copies_steps
    }
    assert flagged == {"CenterOfSlicesAndSaturationRegions"}
    assert sum(link.copies_phase_encoding_direction for link in links) == 1
    assert sum(link.copies_steps for link in links) == 1
    assert not any(link.ignores_last_step or link.ignores_measurements for link in links)


@requires_exar
def test_a_prescription_link_leaves_no_trace_in_the_linked_protocol() -> None:
    """The linkage is a property of the program, not of any scan.

    Every slaved scan in ``copyparametertest`` was copied from the same source
    and then linked, so their protocols should differ from it only in the
    fields the console rewrites on every save. That is the claim that makes
    the relation graph the *only* place to read a link from: a generator
    cannot recover one by inspecting a scan.

    Returns
    -------
    None
    """
    program = exar.read(find_exar("copyparametertest.exar1")).programs[0]
    by_object = {step.instance.object_id: step for step in program.steps}
    links = program.copy_references
    source = _ascconv_literals(by_object[links[0].source].protocol.xprotocol)

    for link in links:
        target = by_object[link.target]
        got = _ascconv_literals(target.protocol.xprotocol)
        differing = {
            key
            for key in set(source) | set(got)
            if source.get(key) != got.get(key) and not LINK_CHURN.search(key)
        }
        assert not differing, f"{target.name}: the link moved {sorted(differing)}"


@requires_exar
def test_the_pdf_export_does_not_record_the_links() -> None:
    """A printout of linked scans is indistinguishable from one without them.

    This is why the feature had to be read out of the archive at all. The PDF
    prints all twelve scans with identical parameter sets and says nothing
    about eleven of them being slaved, so a tool that reads only the printout
    silently loses the relationship.

    Returns
    -------
    None
    """
    archive_file = find_exar("copyparametertest.exar1")
    pdf = os.path.splitext(archive_file)[0] + ".pdf"
    if not os.path.isfile(pdf):
        pytest.skip(f"no PDF export beside {os.path.basename(archive_file)}")

    program = exar.read(archive_file).programs[0]
    by_object = {step.instance.object_id: step for step in program.steps}
    links = program.copy_references
    wanted = {by_object[links[0].source].name} | {by_object[link.target].name for link in links}

    printed = {
        scan.name: {
            (title, key): value
            for title, block in scan.sections().items()
            for key, value in block.items()
        }
        for scan in parse_document(pdf).protocol.scans
        if scan.name in wanted
    }
    assert len(printed) == len(wanted)
    values = list(printed.values())
    for name, block in printed.items():
        assert block == values[0], f"{name} prints differently from its source"


@requires_exar
def test_relations_are_stored_in_creation_order_not_running_order() -> None:
    """Some archive proves the two orders differ, so neither may be sorted.

    ``copyparametertest`` lists its targets in running order, because the
    links were made top to bottom -- an accident that would let a generator
    sort the list and still pass. ``CHR-MDD`` links ranks 11, 15, 22, 24, 26,
    30, 19, 20 in that order and is what refutes it.

    Returns
    -------
    None
    """
    differing = 0
    for path, _version in EXAR_PROTOCOL_FILES:
        for program in exar.read(path).programs:
            rank = {step.instance.object_id: n for n, step in enumerate(program.steps)}
            by_source: dict[str, list[int]] = {}
            for link in program.links:
                by_source.setdefault(link.source, []).append(rank[link.target])
            differing += any(order != sorted(order) for order in by_source.values())
    assert differing, "every relation list is in running order; creation order is unexercised"


@requires_exar
def test_a_relation_is_not_always_a_link() -> None:
    """The corpus carries relations with no kind, no payload and no meaning.

    ``31P CSI 20230503 NOE`` holds 21 of them beside 5 real links, duplicated
    ten deep between the same pairs of steps. They are unexplained, and the
    point of the test is that counting relations is not counting links: a
    reader that assumed every relation carried an
    ``EdfCopyReferenceParameters`` payload would raise on this file.

    Returns
    -------
    None
    """
    program = exar.read(find_exar("31P CSI 20230503 NOE.exar1")).programs[0]
    payloadless = [link for link in program.links if not link.is_copy_reference]
    assert payloadless, "no payload-less relation here, so the handling is untested"
    assert program.copy_references, "and no real link either, so neither branch is exercised"
    assert len(program.copy_references) < len(program.links)


#: The four reference modes, as ``EdfProgramContent.TablePositioningMode``
#: spells them, keyed by the ``extravals`` archive saved under each. Empty is
#: the console's derive-from-protocol choice, not a missing field.
REFERENCE_MODES = {
    "extravals_FIX.exar1": "FIX",
    "extravals_ISO.exar1": "ISO",
    "extravals_LOC.exar1": "LocalRange",
    "extravals_derive.exar1": "",
}


def _paramcheck_archive(name: str) -> str:
    """Locate one option-scan archive by file name.

    ``find_exar`` cannot: it searches ``examples/`` alone, and the option
    scans live outside it deliberately. Asking it for one skips the test,
    which reads as a pass.

    Parameters
    ----------
    name : str
        Base file name, such as ``"extravals_FIX.exar1"``.

    Returns
    -------
    str
        Path to the archive.

    Raises
    ------
    AssertionError
        If it is not in the option-scan corpus, which is a missing fixture
        rather than a reason to skip -- ``requires_paramcheck`` has already
        established that the corpus is present.
    """
    for path, _pdf in PARAMCHECK_PAIRS:
        if os.path.basename(path) == name:
            return path
    raise AssertionError(f"{name} is not in the option-scan corpus")


@requires_paramcheck
def test_the_reference_mode_is_stored_once_per_protocol() -> None:
    """Each ``extravals`` archive carries the mode it was saved under.

    The mode is a program-level string rather than anything per scan, which is
    the first half of it not being a geometry input.

    Returns
    -------
    None
    """
    for name, expected in REFERENCE_MODES.items():
        loaded = exar.read(_paramcheck_archive(name))
        program = loaded.programs[0]
        assert loaded.document(program.instance)["TablePositioningMode"] == expected, name


@requires_paramcheck
def test_the_reference_mode_changes_nothing_about_the_slices() -> None:
    """The four modes differ in that one field and in nothing else.

    A correlation across the corpus cannot answer this, because which
    protocols carry which mode is confounded with everything else about them
    -- swept that way the disagreement rate looks like 7% under ``FIX`` and
    33% under derive. These four archives are the same nineteen scans saved
    once per mode, so the comparison is controlled: any difference in the
    stored geometry would have to be the mode.

    Returns
    -------
    None
    """
    blocks = {}
    for name in REFERENCE_MODES:
        program = exar.read(_paramcheck_archive(name)).programs[0]
        blocks[name] = {
            step.name: _ascconv_literals(step.protocol.xprotocol)
            for step in program.steps
            if step.runs_a_protocol
        }

    reference = blocks["extravals_FIX.exar1"]
    assert len(reference) > 15, "too few scans here to say anything"
    for name, block in blocks.items():
        assert set(block) == set(reference), f"{name} holds different scans"
        for scan, literals in block.items():
            against = reference[scan]
            differing = {
                key
                for key in set(against) | set(literals)
                if against.get(key) != literals.get(key) and not LINK_CHURN.search(key)
            }
            assert not differing, f"{name}/{scan}: the mode moved {sorted(differing)}"


# --------------------------------------------------------------------------
# Pairing a many-protocol archive with the printouts that cover some of it
# --------------------------------------------------------------------------

#: An export taken at the investigator level: 31 protocols, with PDFs for 14.
#: It lives in a subdirectory so the flat corpus fixtures do not discover its
#: printouts as 14 unrelated examples, and the tests below name it explicitly.
INVESTIGATOR_EXPORT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "examples",
    "XA60",
    "Frederick_P2",
)


def _investigator_export() -> tuple[str, dict[str, dict]]:
    """Load the investigator archive and every printout beside it.

    Returns
    -------
    tuple
        The archive path and ``{file name: parsed protocol}``.
    """
    archive_file = os.path.join(INVESTIGATOR_EXPORT, "Frederick_P2.exar1")
    if not os.path.isfile(archive_file):
        pytest.skip("the investigator export is not available")
    exports = {}
    for name in sorted(os.listdir(INVESTIGATOR_EXPORT)):
        if name.endswith(".pdf"):
            path = os.path.join(INVESTIGATOR_EXPORT, name)
            exports[name] = parse_document(path).protocol.to_dict()
    return archive_file, exports


def test_a_printout_names_its_protocol_in_the_header_not_the_file_name() -> None:
    """The program name comes off the printed path.

    The scanner requires a program name to be unique within an exam, so the
    printed path identifies the protocol exactly. A file name is whatever
    someone called the export afterwards. They agree across this directory,
    which is what makes it a fixture rather than a counter-example -- the
    point is that the header is the one that cannot drift.

    Returns
    -------
    None
    """
    _archive_file, exports = _investigator_export()
    assert exports, "no printouts beside the investigator archive"
    for name, parsed in exports.items():
        found = build.program_name(parsed)
        assert found, f"{name} declares no protocol in its scan paths"
        assert found == os.path.splitext(name)[0]


def test_pairing_matches_what_it_can_and_reports_the_rest() -> None:
    """An archive with more protocols than printouts pairs the overlap.

    An export taken at the investigator level holds every protocol, and the
    PDFs beside it are usually a fraction. So an unmatched program is the
    ordinary case: everything pairable pairs, and the remainder is reported
    rather than raised. Here 14 of 31 protocols have a printout.

    Returns
    -------
    None
    """
    archive_file, exports = _investigator_export()
    loaded = exar.read(archive_file)
    pairing = build.pair_programs(loaded, exports)

    assert len(pairing.matched) == len(exports), "every printout should pair"
    assert not pairing.unmatched_exports
    assert pairing.unmatched_programs, "this archive holds protocols with no printout"
    assert len(pairing.matched) + len(pairing.unmatched_programs) == len(loaded.programs)


def test_every_pairing_is_confirmed_by_the_scans_it_names() -> None:
    """The paired protocol really is the one the printout describes.

    Checked against the scan list rather than assumed from the name, since a
    name match is one string and this is what it is standing in for. Exact
    agreement is not required: three of these protocols were edited after
    their PDF was exported -- a renamed scan in two, and a doubled space the
    console prints as one in the third -- so the bar is that the counts match
    and almost every name does. That tolerance is the reason to pair on the
    program name in the first place; a set comparison would reject three
    perfectly good pairs.

    Returns
    -------
    None
    """
    archive_file, exports = _investigator_export()
    loaded = exar.read(archive_file)
    programs = {build.match_name(one.name): one for one in loaded.programs}
    pairing = build.pair_programs(loaded, exports)

    for name, key in pairing.matched:
        held = [step.name for step in programs[name].steps if step.runs_a_protocol]
        printed = [scan["name"] for scan in exports[key]["scans"]]
        assert len(held) == len(printed), f"{name}: {len(held)} scans against {len(printed)}"
        agreeing = sum(build.match_name(a) == build.match_name(b) for a, b in zip(held, printed))
        assert agreeing >= len(held) - 1, (
            f"{name}: only {agreeing} of {len(held)} scan names agree, "
            "which is too many for this to be the right protocol"
        )


#: The one place a stored flag and its printout disagree, pinned rather than
#: excluded so it stays visible and so a *second* case fails.
#:
#: ``T10`` is a scan this library wrote and a scanner then loaded and re-saved.
#: Its returned protocol holds bits 1 and 20 while its returned printout shows
#: every flag off -- the console displaying something other than the word it
#: stored. Every console-authored scan in the corpus agrees, so this is a
#: property of that write rather than of the decoding. What it is remains open:
#: it needs a scanner to answer, and the neighbouring ``T01``-``T14`` toggles
#: all came back consistent.
KNOWN_FLAG_DISAGREEMENTS = {
    "CMRR_optionscan_P1_loadtest.exar1": {
        ("T10_Force_equal_slice_timing_True", "PF omits higher k-space"),
        ("T10_Force_equal_slice_timing_True", "Force equal slice timing"),
    },
}


@requires_exar
def test_every_flag_bit_agrees_with_the_card_that_printed_it(protocol_archive_path: str) -> None:
    """A CMRR flags word decodes to what its own printout displays.

    Swept over every archive with a PDF beside it. The check is fully
    discriminating rather than nearly vacuous: across the corpus all fourteen
    mapped bits are observed set, so a table with a bit in the wrong place
    fails here rather than agreeing by everything being off.

    What this cannot tell apart is a console that remapped a converted
    protocol's bits from one that copied them verbatim -- either way the
    console displays the word it holds under the current layout, so agreement
    is guaranteed by construction. It tests the decoder. Whether a conversion
    preserved the author's *intent* is a different question and needs the
    original release's printout, which is why ``K23EB_20210802`` ships with
    its VE11C export beside it.

    Parameters
    ----------
    protocol_archive_path : str
        Archive under test.

    Returns
    -------
    None
    """
    loaded = exar.read(protocol_archive_path)
    folder = os.path.dirname(protocol_archive_path)
    beside = os.path.splitext(protocol_archive_path)[0] + ".pdf"
    if os.path.isfile(beside):
        # A one-protocol export with its own printout beside it. Take that
        # file rather than the whole folder: several variants of one protocol
        # live here -- ``Potpourri_P1``, ``_changed``, ``_loadtest`` all print
        # the same program name -- and handing them all to the pairer makes it
        # refuse, correctly, to guess which is which.
        names = [os.path.basename(beside)]
    else:
        names = [one for one in sorted(os.listdir(folder)) if one.endswith(".pdf")]
    exports = {
        name: parse_document(os.path.join(folder, name)).protocol.to_dict() for name in names
    }
    # Pair rather than require a same-named PDF. The investigator export holds
    # 31 protocols and no printout of its own name, and skipping it would drop
    # the largest population of flag words in the corpus -- silently, since a
    # skip reads like a pass.
    pairs = build.pair_programs(loaded, exports)
    if not pairs.matched:
        pytest.skip(f"no printout pairs with {os.path.basename(protocol_archive_path)}")
    programs = {build.match_name(one.name): one for one in loaded.programs}

    bits = [one for one in patch.MAPPINGS if one.bit is not None]
    known = KNOWN_FLAG_DISAGREEMENTS.get(os.path.basename(protocol_archive_path), set())
    checked = 0
    for program_name, key in pairs.matched:
        printed: dict[str, list[dict[str, str]]] = {}
        for scan in exports[key]["scans"]:
            block: dict[str, str] = {}
            for title, params in scan["sections"].items():
                if "Special" in title:
                    block.update(params)
            printed.setdefault(build.match_name(scan["name"]), []).append(block)

        seen: dict[str, int] = {}
        for step in programs[program_name].steps:
            if not step.runs_a_protocol:
                continue
            if not any(str(patch.sequence_of(step.protocol)) in one.sequences for one in bits):
                continue
            name = build.match_name(step.name)
            blocks = printed.get(name, [])
            seen[name] = seen.get(name, 0) + 1
            if seen[name] > len(blocks):
                continue
            card = blocks[seen[name] - 1]
            raw = patch.read_ascconv(step.protocol.xprotocol, "sWipMemBlock.alFree[0]")
            word = int(raw, 0) if raw else 0
            for one in bits:
                shown = card.get(one.label)
                if shown is None:
                    continue
                checked += 1
                stored = "On" if word >> one.bit & 1 else "Off"
                if (name, one.label) in known:
                    assert shown.strip() != stored, (
                        f"{name}: {one.label} now agrees; drop it from "
                        "KNOWN_FLAG_DISAGREEMENTS rather than leaving it pinned"
                    )
                    continue
                assert shown.strip() == stored, (
                    f"{program_name}/{name}: {one.label} is bit {one.bit}, "
                    f"stored {stored}, printed {shown.strip()}"
                )
    if not checked:
        pytest.skip("this archive prints no mapped flag")
