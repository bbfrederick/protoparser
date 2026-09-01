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
import sqlite3

import pytest

from conftest import (  # noqa: F401  (fixtures)
    EXAR_PROTOCOL_FILES,
    archive_path,
    find_exar,
    protocol_archive_path,
    requires_exar,
)
from siemens_protocol import exar
from siemens_protocol.exar import archive, envelope, store
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
    assert loaded.program is not None


@requires_exar
def test_step_order_is_the_link_chain_not_the_children_blob(protocol_archive_path: str) -> None:
    """Running order comes from the program's linked list.

    The program's ``Children`` blob holds the same steps in a different order.
    Both produce a full set of protocols with all the right values, so the
    wrong one is indistinguishable from the right one by spot check -- which
    is exactly why it is asserted.

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
        stored = [by_element[c].object_id for c in program.children if c in by_element]
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
            stored = [by_element[c].object_id for c in program.children if c in by_element]
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
