"""The protocol tree an ``.exar1`` archive describes, and how to walk it.

An archive is a small version-control repository, not a document. ``ChangeSet``
rows form a DAG with author, timestamp and two parent slots; ``Branch`` names a
head; and ``Content`` is addressed by hash so identical protocols share a row.
Reading one therefore means picking a head and resolving the instances that are
live at it, exactly as checking out a commit would.

Three separate GUID spaces meet in the ``Instance`` table, and confusing them
is the easiest way to get a plausible but wrong answer:

``Id``
    identifies one *version* of one node, and is what ``InstanceChangeSet``
    refers to.
``Element_id``
    identifies the node across versions, and is what the packed ``Children``
    blobs refer to.
``ObjectId``
    identifies the domain object, and is what the *JSON payloads* refer to --
    ``FirstStepId``, ``LastStepId`` and the ``LinksFrom`` edges.

Scan order is the other trap. ``Children`` is storage order, not running order:
walking it yields the right eighteen protocols in the wrong sequence, and since
the parameter values are all still present the mistake looks like success. The
running order is a linked list held in the program's own content -- start at
``FirstStepId`` and follow ``LinksFrom`` until ``LastStepId``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Iterator

from . import envelope, store
from .envelope import Envelope

#: Width of a packed GUID in a ``Children`` blob.
GUID_BYTES = 16

#: ``Branch.Baseline`` for the bookkeeping branch that every archive carries
#: alongside the real one. It has no live instances, so it is never the head
#: to read from.
PLACEHOLDER_BASELINE = "-"

#: Instance types, as ``Instance.InstanceType`` spells them.
PROGRAM = "EdfProgram"
MEASUREMENT_STEP = "EdfMeasurementStep"
PROTOCOL = "EdfProtocol"
STRING = "EdfString"


def unpack_guids(blob: bytes | None) -> list[str]:
    """Decode a packed ``Children`` blob into element ids.

    The GUIDs are stored in .NET's ``Guid.ToByteArray`` layout, which is
    little-endian for the first three fields and big-endian for the rest.
    Reading them as plain big-endian bytes yields well-formed GUIDs that match
    nothing, so the mistake surfaces as an empty tree rather than an error.

    Parameters
    ----------
    blob : bytes or None
        The stored blob, or ``None`` for a node with no children.

    Returns
    -------
    list of str
        Element ids in stored order, lowercase and hyphenated.
    """
    if not blob:
        return []
    return [
        str(uuid.UUID(bytes_le=blob[at : at + GUID_BYTES]))
        for at in range(0, len(blob), GUID_BYTES)
    ]


def pack_guids(ids: list[str]) -> bytes:
    """Encode element ids back into a packed ``Children`` blob.

    Parameters
    ----------
    ids : list of str
        Element ids, in the order to store them.

    Returns
    -------
    bytes
        The concatenated .NET-layout GUIDs.
    """
    return b"".join(uuid.UUID(one).bytes_le for one in ids)


@dataclass(frozen=True)
class PreviewEntry:
    """One entry of a protocol's ``Preview`` map.

    These carry the label and unit the console displays, which are also the
    label and unit the PDF export prints, making the map a per-protocol
    dictionary from printed label to protocol path.

    Attributes
    ----------
    path : str
        The preview key, for example ``sub.0.msr.tr.0``.
    label : str
        The displayed label, for example ``TR``.
    unit : str
        The displayed unit, for example ``ms``. Often a single space.
    value : Any
        The displayed value, already typed by the JSON decoder.
    """

    path: str
    label: str
    unit: str
    value: Any


@dataclass
class Instance:
    """One row of the ``Instance`` table, with its children decoded.

    Attributes
    ----------
    id : str
        Version identity.
    element_id : str
        Node identity across versions.
    object_id : str
        Domain-object identity, as the JSON payloads refer to it.
    kind : str
        ``Instance.InstanceType``.
    content_hash : str or None
        Address of this node's content, if it has any.
    children : list of str
        Child *element* ids, in stored order.
    label_element_id : str or None
        Element whose content holds this node's displayed name.
    """

    id: str
    element_id: str
    object_id: str
    kind: str
    content_hash: str | None
    children: list[str] = field(default_factory=list)
    label_element_id: str | None = None


@dataclass
class Protocol:
    """One acquisition's stored protocol.

    Attributes
    ----------
    instance : Instance
        The tree node this protocol hangs from.
    document : dict
        The decoded ``EdfProtocolContent``.
    """

    instance: Instance
    document: dict[str, Any]

    @property
    def xprotocol(self) -> str:
        """Return the raw XProtocol text.

        Numaris/X did not replace XProtocol, it wrapped it: this string is the
        same brace-delimited tree the VB and VE releases wrote, ASCCONV block
        included.

        Returns
        -------
        str
            The contents of the document's ``Data`` field.
        """
        return self.document.get("Data", "")

    @property
    def preview(self) -> dict[str, PreviewEntry]:
        """Return the ``Preview`` map as typed entries.

        Returns
        -------
        dict of str to PreviewEntry
            Keyed by preview path. Newtonsoft's ``$id`` bookkeeping key is
            dropped.
        """
        entries: dict[str, PreviewEntry] = {}
        for path, raw in self.document.get("Preview", {}).items():
            if path == "$id" or not isinstance(raw, dict):
                continue
            entries[path] = PreviewEntry(
                path=path,
                label=raw.get("Label", ""),
                unit=raw.get("Unit", ""),
                value=raw.get("Value"),
            )
        return entries

    def by_label(self, label: str) -> list[PreviewEntry]:
        """Find preview entries whose displayed label matches ``label``.

        Parameters
        ----------
        label : str
            The label as printed, for example ``TR``. Compared without
            surrounding whitespace and without regard to case.

        Returns
        -------
        list of PreviewEntry
            Every matching entry, in preview order.
        """
        wanted = label.strip().casefold()
        return [e for e in self.preview.values() if e.label.strip().casefold() == wanted]


@dataclass
class Step:
    """One measurement step: a named node holding one or more protocols.

    Attributes
    ----------
    instance : Instance
        The step's tree node.
    name : str
        The displayed name, which is the scan name the PDF export prints.
    protocols : list of Protocol
        The protocols this step runs, in stored order.
    """

    instance: Instance
    name: str
    protocols: list[Protocol] = field(default_factory=list)

    @property
    def protocol(self) -> Protocol:
        """Return the step's single protocol.

        Returns
        -------
        Protocol
            The first protocol.

        Raises
        ------
        ValueError
            If the step holds no protocol at all.
        """
        if not self.protocols:
            raise ValueError(f"measurement step {self.name!r} holds no protocol")
        return self.protocols[0]


@dataclass
class Archive:
    """One ``.exar1`` file, read at a branch head.

    Attributes
    ----------
    container : store.Container
        The raw tables, kept so the archive can be written back unchanged.
    contents : dict of str to Envelope
        Every ``Content`` row, keyed by hash.
    instances : dict of str to Instance
        Live instances at the head, keyed by version id.
    baseline : str
        The branch's ``Baseline`` string, which is the compatibility key the
        scanner checks -- ``MAJORVERSION:VA60A, PROTOCOL:66010002, ...``.
    head : str
        The changeset id the instances were resolved at.
    """

    container: store.Container
    contents: dict[str, Envelope]
    instances: dict[str, Instance]
    baseline: str
    head: str

    @property
    def by_element(self) -> dict[str, Instance]:
        """Index the live instances by element id.

        Returns
        -------
        dict of str to Instance
            One entry per element. ``Children`` blobs resolve through this.
        """
        return {one.element_id: one for one in self.instances.values()}

    @property
    def by_object(self) -> dict[str, Instance]:
        """Index the live instances by domain-object id.

        Returns
        -------
        dict of str to Instance
            One entry per object. The JSON payloads' ids resolve through this.
        """
        return {one.object_id: one for one in self.instances.values()}

    @property
    def major_version(self) -> str:
        """Return the release named in the baseline, for example ``VA60A``.

        Returns
        -------
        str
            The ``MAJORVERSION`` field, or an empty string if absent.
        """
        for part in self.baseline.split(","):
            name, _, value = part.partition(":")
            if name.strip() == "MAJORVERSION":
                return value.strip()
        return ""

    def document(self, instance: Instance) -> dict[str, Any]:
        """Decode one instance's content.

        Parameters
        ----------
        instance : Instance
            The node whose content to decode.

        Returns
        -------
        dict
            The decoded document, or an empty mapping when the node has none.
        """
        if not instance.content_hash:
            return {}
        found = self.contents.get(instance.content_hash)
        return found.decode() if found is not None else {}

    def label_of(self, instance: Instance) -> str:
        """Resolve an instance's displayed name.

        Names live on a separate ``EdfString`` node rather than inside the
        protocol, so that renaming a scan does not rewrite -- and so does not
        re-hash -- the protocol itself. The string content is a locale table
        whose empty-string key holds the default.

        Parameters
        ----------
        instance : Instance
            The node whose name to resolve.

        Returns
        -------
        str
            The displayed name, or an empty string when there is none.
        """
        element = instance.label_element_id
        if not element:
            return ""
        holder = self.by_element.get(element)
        if holder is None:
            return ""
        return self.document(holder).get("Texts", {}).get("", "")

    @property
    def program(self) -> Instance | None:
        """Return the root program node.

        Returns
        -------
        Instance or None
            The single ``EdfProgram`` instance, or ``None`` if absent.
        """
        for one in self.instances.values():
            if one.kind == PROGRAM:
                return one
        return None

    def step_order(self) -> list[str]:
        """Return the object ids of the measurement steps, in running order.

        The order is a linked list in the program's content, not the order of
        the program's ``Children`` blob. Following the blob instead yields the
        same steps permuted, which is a failure that looks like success.

        Returns
        -------
        list of str
            Object ids from ``FirstStepId`` onwards. Empty when there is no
            program node.
        """
        root = self.program
        if root is None:
            return []
        content = self.document(root)
        edges: dict[str, list[str]] = {
            source: [edge["TargetId"] for edge in payload.get("$values", [])]
            for source, payload in content.get("LinksFrom", {}).items()
            if source != "$id" and isinstance(payload, dict)
        }
        order: list[str] = []
        seen: set[str] = set()
        current = content.get("FirstStepId")
        while current and current not in seen:
            seen.add(current)
            order.append(current)
            following = edges.get(current) or []
            current = following[0] if following else None
        return order

    @property
    def steps(self) -> list[Step]:
        """Return the measurement steps in running order, with their protocols.

        Returns
        -------
        list of Step
            One entry per step, matching the scan order of the PDF export.
        """
        by_element = self.by_element
        by_object = self.by_object
        built: list[Step] = []
        for object_id in self.step_order():
            node = by_object.get(object_id)
            if node is None:
                continue
            protocols = []
            for child in node.children:
                held = by_element.get(child)
                if held is not None and held.kind == PROTOCOL:
                    protocols.append(Protocol(instance=held, document=self.document(held)))
            built.append(Step(instance=node, name=self.label_of(node), protocols=protocols))
        return built

    def replace_content(self, instance: Instance, document: dict[str, Any]) -> str:
        """Store an edited document and point ``instance`` at it.

        Content is addressed by hash, so an edit re-addresses the node rather
        than overwriting anything: the new document gets a new ``Content`` row
        and the instance's ``ContentHash`` is repointed at it. The old row is
        left in place, both because another instance may still share it -- the
        table is deduplicated, and identical protocols do collide -- and
        because discarding superseded content in a version-control store is
        not this layer's decision to make.

        The repoint is done by instance ``Id`` and never by hash, for the same
        deduplication reason: rewriting every row that happened to share the
        old hash would silently edit protocols the caller never named.

        Parameters
        ----------
        instance : Instance
            The node to repoint. Must already have content.
        document : dict
            The replacement document.

        Returns
        -------
        str
            The new content hash.

        Raises
        ------
        ValueError
            If the instance has no content to replace.
        """
        if not instance.content_hash:
            raise ValueError(f"instance {instance.id} has no content to replace")
        previous = self.contents[instance.content_hash]
        fresh = previous.replace(document)
        digest = fresh.hash
        if digest not in self.contents:
            self.contents[digest] = fresh
            self.container.tables["Content"].append(
                {
                    "Hash": digest,
                    "Data": fresh.to_stored(),
                    "Format": envelope.STORED_FORMAT,
                }
            )
        rows = self.container.tables["Instance"]
        for position in rows.find("Id", instance.id):
            rows.set(position, "ContentHash", digest)
        instance.content_hash = digest
        return digest

    def write(self, path: str) -> None:
        """Write the archive out as a new ``.exar1`` file.

        Parameters
        ----------
        path : str
            Destination path. An existing file there is replaced.

        Returns
        -------
        None
        """
        store.write(self.container, path)


def _head_branch(container: store.Container) -> tuple[str, str]:
    """Choose which branch head to read the archive at.

    Every archive carries a placeholder branch whose baseline is ``-`` and
    which has no live instances, alongside the real one.

    Parameters
    ----------
    container : store.Container
        The loaded tables.

    Returns
    -------
    tuple of (str, str)
        The baseline string and the head changeset id.

    Raises
    ------
    ValueError
        If the archive declares no usable branch.
    """
    rows = container.rows("Branch")
    real = [r for r in rows if str(r.get("Baseline", "")).strip() != PLACEHOLDER_BASELINE]
    chosen = real or rows
    if not chosen:
        raise ValueError("archive declares no branch")
    return str(chosen[0].get("Baseline", "")), str(chosen[0].get("Head", ""))


def _live_instances(container: store.Container, head: str) -> dict[str, Instance]:
    """Resolve the instances that exist at one changeset.

    Parameters
    ----------
    container : store.Container
        The loaded tables.
    head : str
        The changeset id to resolve at.

    Returns
    -------
    dict of str to Instance
        Live instances keyed by version id.
    """
    live = {
        str(row["InstanceId"])
        for row in container.rows("InstanceChangeSet")
        if str(row.get("ChangeSetId", "")) == head
    }
    resolved: dict[str, Instance] = {}
    for row in container.rows("Instance"):
        identifier = str(row["Id"])
        if live and identifier not in live:
            continue
        resolved[identifier] = Instance(
            id=identifier,
            element_id=str(row["Element_id"]),
            object_id=str(row["ObjectId"]),
            kind=str(row["InstanceType"]),
            content_hash=row["ContentHash"],
            children=unpack_guids(row["Children"]),
            label_element_id=row["LabelElement_id"],
        )
    return resolved


def read(path: str) -> Archive:
    """Read an ``.exar1`` archive at its branch head.

    Parameters
    ----------
    path : str
        Path to the archive.

    Returns
    -------
    Archive
        The decoded archive, retaining every raw table so it can be written
        back unchanged.
    """
    container = store.read(path)
    baseline, head = _head_branch(container)
    contents = {str(row["Hash"]): envelope.parse(row["Data"]) for row in container.rows("Content")}
    return Archive(
        container=container,
        contents=contents,
        instances=_live_instances(container, head),
        baseline=baseline,
        head=head,
    )
