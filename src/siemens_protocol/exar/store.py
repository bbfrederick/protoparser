"""Faithful table-level read and write of an ``.exar1`` SQLite container.

This layer knows nothing about protocols. It copies the schema out of
``sqlite_master`` and the rows out of every table, and it writes them back the
same way, so a baseline whose schema differs from XA60's still round-trips
without a code change. That matters more than it looks: the archives seen so
far declare ``EDF:1`` in their baseline string, and a later value is free to
add a column.

Values keep their SQLite storage class -- ``TEXT`` stays ``str``, ``BLOB``
stays ``bytes`` -- because the two are distinguishable in the file and a GUID
written back as a blob would no longer compare equal to the same GUID written
as text. Both appear in one row of ``Instance``: the id columns are text, and
``Children`` is a blob.
"""

from __future__ import annotations

import errno
import os
import pathlib
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Iterator

#: Tables that hold sqlite's own bookkeeping and are rebuilt from the DDL
#: rather than copied row by row.
INTERNAL_PREFIX = "sqlite_"


@dataclass
class Table:
    """One table's declaration and contents.

    Attributes
    ----------
    name : str
        The table name as it appears in ``sqlite_master``.
    sql : str
        The ``CREATE TABLE`` statement, copied verbatim.
    columns : list of str
        Column names in declaration order.
    rows : list of tuple
        Every row, in the order the source returned them.
    """

    name: str
    sql: str
    columns: list[str] = field(default_factory=list)
    rows: list[tuple[Any, ...]] = field(default_factory=list)

    def dicts(self) -> Iterator[dict[str, Any]]:
        """Iterate the rows as column-keyed mappings.

        Returns
        -------
        Iterator of dict
            One mapping per row, in stored order.
        """
        for row in self.rows:
            yield dict(zip(self.columns, row))

    def index_of(self, column: str) -> int:
        """Return a column's position in the row tuples.

        Parameters
        ----------
        column : str
            Column name.

        Returns
        -------
        int
            Zero-based position.

        Raises
        ------
        KeyError
            If the table has no such column.
        """
        try:
            return self.columns.index(column)
        except ValueError as error:
            raise KeyError(f"{self.name} has no column {column!r}") from error

    def find(self, column: str, value: Any) -> list[int]:
        """Return the positions of the rows whose ``column`` equals ``value``.

        Parameters
        ----------
        column : str
            Column to match on.
        value : Any
            Value to match, compared with ``==``.

        Returns
        -------
        list of int
            Row positions, in stored order.
        """
        at = self.index_of(column)
        return [n for n, row in enumerate(self.rows) if row[at] == value]

    def set(self, position: int, column: str, value: Any) -> None:
        """Replace one cell, leaving every other cell of the row untouched.

        Rows are tuples so that a copied row cannot be mutated by accident;
        this rebuilds the one row rather than mutating in place.

        Parameters
        ----------
        position : int
            Row position, as :meth:`find` returns.
        column : str
            Column to write.
        value : Any
            New value. Its Python type decides the stored storage class, so
            pass ``str`` for a TEXT column and ``bytes`` for a BLOB one.

        Returns
        -------
        None
        """
        at = self.index_of(column)
        row = list(self.rows[position])
        row[at] = value
        self.rows[position] = tuple(row)

    def discard(self, column: str, values: set[Any]) -> int:
        """Remove every row whose ``column`` value is in ``values``.

        Parameters
        ----------
        column : str
            Column to match on.
        values : set
            Values to remove, compared with ``in``.

        Returns
        -------
        int
            How many rows were removed.
        """
        at = self.index_of(column)
        keep = [row for row in self.rows if row[at] not in values]
        removed = len(self.rows) - len(keep)
        self.rows = keep
        return removed

    def append(self, values: dict[str, Any]) -> None:
        """Add a row, filling any column ``values`` omits with ``None``.

        Parameters
        ----------
        values : dict
            Column name to value.

        Returns
        -------
        None
        """
        self.rows.append(tuple(values.get(name) for name in self.columns))


@dataclass
class Container:
    """Everything an ``.exar1`` file holds, at table granularity.

    Attributes
    ----------
    tables : dict of str to Table
        Every non-internal table, keyed by name.
    indexes : list of str
        ``CREATE INDEX`` statements, replayed after the rows are inserted.
    """

    tables: dict[str, Table] = field(default_factory=dict)
    indexes: list[str] = field(default_factory=list)

    def rows(self, table: str) -> list[dict[str, Any]]:
        """Return one table's rows as mappings.

        Parameters
        ----------
        table : str
            Table name.

        Returns
        -------
        list of dict
            The rows, or an empty list when the table is absent.
        """
        found = self.tables.get(table)
        return list(found.dicts()) if found is not None else []


def _read_only_uri(path: str) -> str:
    """Return a SQLite URI that opens ``path`` without creating or writing it.

    The URI form is what carries ``mode=ro``; it also has to be escaped, and
    several corpus archives have spaces in their names, so this goes through
    :meth:`pathlib.Path.as_uri` rather than through string concatenation.

    Parameters
    ----------
    path : str
        Path to an existing file.

    Returns
    -------
    str
        A ``file:`` URI with the read-only query appended.
    """
    return pathlib.Path(path).resolve().as_uri() + "?mode=ro"


def read(path: str) -> Container:
    """Load every table of an ``.exar1`` file.

    The connection is opened **read-only, and only if the file already
    exists**, because the obvious spelling does neither.
    ``sqlite3.connect`` creates an empty database at a path that has none,
    so a mistyped or wrongly-joined archive name left a 0-byte ``.exar1``
    behind and then failed several layers up with "archive declares no
    branch" -- a sentence about the *contents* of a file the caller had just
    silently created. In a corpus directory that is worse than a bad error
    message: the stray file is discovered by the same globs the real
    archives are, so the next test run reads it, and the damage presents as
    corpus corruption rather than as a typo.

    Parameters
    ----------
    path : str
        Path to the archive.

    Returns
    -------
    Container
        The schema and rows, ready to inspect or write back.

    Raises
    ------
    FileNotFoundError
        If nothing exists at ``path``. This is an ``OSError``, which every
        caller in this package already handles beside ``ValueError``.
    IsADirectoryError
        If ``path`` names a directory. Also an ``OSError``, and reported
        separately because "no such file" is untrue of a path that exists.
    sqlite3.DatabaseError
        If the file exists but is not a SQLite database -- a PDF beside the
        archive under the same stem being the easy mistake.
    """
    if os.path.isdir(path):
        raise IsADirectoryError(errno.EISDIR, os.strerror(errno.EISDIR), path)
    if not os.path.isfile(path):
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), path)
    connection = sqlite3.connect(_read_only_uri(path), uri=True)
    try:
        container = Container()
        catalog = connection.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL"
        ).fetchall()
        for kind, name, sql in catalog:
            if name.startswith(INTERNAL_PREFIX):
                continue
            if kind == "index":
                container.indexes.append(sql)
            elif kind == "table":
                cursor = connection.execute(f'SELECT * FROM "{name}"')
                container.tables[name] = Table(
                    name=name,
                    sql=sql,
                    columns=[c[0] for c in cursor.description],
                    rows=cursor.fetchall(),
                )
        return container
    finally:
        connection.close()


def write(container: Container, path: str) -> None:
    """Write a container out as a new ``.exar1`` file.

    The result is not byte-identical to the source file -- sqlite is free to
    lay out pages and freelists differently -- but it is identical row for row
    and blob for blob, which is what the scanner reads.

    Parameters
    ----------
    container : Container
        The tables to write.
    path : str
        Destination path. An existing file there is replaced.

    Returns
    -------
    None
    """
    connection = sqlite3.connect(path)
    try:
        with connection:
            for table in container.tables.values():
                connection.execute(f'DROP TABLE IF EXISTS "{table.name}"')
                connection.execute(table.sql)
                if not table.rows:
                    continue
                columns = ", ".join(f'"{c}"' for c in table.columns)
                marks = ", ".join("?" * len(table.columns))
                connection.executemany(
                    f'INSERT INTO "{table.name}" ({columns}) VALUES ({marks})', table.rows
                )
            for sql in container.indexes:
                connection.execute(sql)
    finally:
        connection.close()
