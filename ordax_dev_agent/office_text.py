"""Bounded, read-only Office text extraction using the Python standard library.

This is a lazy adapter of computer.text_read, not a new Runtime or MCP
executor. It does not follow hyperlinks, evaluate formulas or load external
resources. Untrusted Office ZIP/XML payloads are subject to strict byte caps.
"""
from __future__ import annotations

import io
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from typing import Any

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_P = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_ENTRY_LIMIT = 8 * 1024 * 1024
_TOTAL_LIMIT = 20 * 1024 * 1024
_TEXT_LIMIT = 4 * 1024 * 1024
_MAX_ROWS = 10000
_MAX_CELLS = 50000
_CELL_REF = re.compile(r"^([A-Z]{1,3})([1-9][0-9]{0,6})$")
_CELL_RANGE = re.compile(r"^([A-Z]{1,3}[1-9][0-9]{0,6}):([A-Z]{1,3}[1-9][0-9]{0,6})$")


class OfficeDocumentError(ValueError):
    """Invalid, unsupported or oversized office document."""


class _SafeArchive:
    def __init__(self, raw: bytes):
        try:
            self.archive = zipfile.ZipFile(io.BytesIO(raw))
        except (OSError, zipfile.BadZipFile) as error:
            raise OfficeDocumentError("invalid Office ZIP container") from error
        self.total = 0

    def xml(self, name: str, *, optional: bool = False) -> ET.Element | None:
        try:
            info = self.archive.getinfo(name)
        except KeyError:
            if optional:
                return None
            raise OfficeDocumentError("required Office document part is missing") from None
        if info.is_dir() or info.file_size > _ENTRY_LIMIT:
            raise OfficeDocumentError("Office XML part exceeds inspection limit")
        self.total += info.file_size
        if self.total > _TOTAL_LIMIT:
            raise OfficeDocumentError("Office XML inspection exceeds cumulative limit")
        try:
            with self.archive.open(info) as handle:
                raw = handle.read(_ENTRY_LIMIT + 1)
            if len(raw) > _ENTRY_LIMIT:
                raise OfficeDocumentError("Office XML part exceeds inspection limit")
            return ET.fromstring(raw)
        except (ET.ParseError, RuntimeError, zipfile.BadZipFile, EOFError) as error:
            raise OfficeDocumentError("invalid Office XML document part") from error

    def close(self) -> None:
        self.archive.close()


def _bounded(text: str) -> str:
    if len(text.encode("utf-8")) > _TEXT_LIMIT:
        raise OfficeDocumentError("Office text exceeds inspection limit; select a smaller range")
    return text


def _docx(archive: _SafeArchive) -> tuple[str, dict[str, Any]]:
    root = archive.xml("word/document.xml")
    assert root is not None
    lines: list[str] = []
    for paragraph in root.iter(_W + "p"):
        fragments: list[str] = []
        for node in paragraph.iter():
            if node.tag == _W + "t" and node.text:
                fragments.append(node.text)
            elif node.tag == _W + "tab":
                fragments.append("\t")
            elif node.tag in {_W + "br", _W + "cr"}:
                fragments.append("\n")
        lines.append("".join(fragments))
        if len(lines) > _MAX_ROWS:
            raise OfficeDocumentError("Word paragraph count exceeds inspection limit")
    return _bounded("\n".join(lines)), {"format": "docx", "paragraph_count": len(lines)}


def _column(value: str) -> int:
    number = 0
    for char in value:
        number = number * 26 + ord(char) - 64
    return number


def _ref(value: str) -> tuple[int, int]:
    match = _CELL_REF.fullmatch(value)
    if match is None:
        raise OfficeDocumentError("invalid Excel cell reference")
    return int(match.group(2)), _column(match.group(1))


def _range(value: str | None) -> tuple[int, int, int, int] | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise OfficeDocumentError("cell_range must be a string")
    match = _CELL_RANGE.fullmatch(value.upper())
    if match is None:
        raise OfficeDocumentError("cell_range must use A1:D100 notation")
    first_row, first_col = _ref(match.group(1))
    last_row, last_col = _ref(match.group(2))
    if first_row > last_row or first_col > last_col:
        raise OfficeDocumentError("cell_range has reversed bounds")
    if (last_row - first_row + 1) * (last_col - first_col + 1) > _MAX_CELLS:
        raise OfficeDocumentError("cell_range exceeds inspection cell limit")
    return first_row, first_col, last_row, last_col


def _shared_strings(archive: _SafeArchive) -> list[str]:
    root = archive.xml("xl/sharedStrings.xml", optional=True)
    if root is None:
        return []
    values: list[str] = []
    for item in root.findall(_S + "si"):
        values.append("".join(node.text or "" for node in item.iter(_S + "t")))
        if len(values) > _MAX_CELLS:
            raise OfficeDocumentError("Excel shared string count exceeds inspection limit")
    return values


def _sheets(archive: _SafeArchive) -> list[tuple[str, str]]:
    workbook = archive.xml("xl/workbook.xml")
    rels = archive.xml("xl/_rels/workbook.xml.rels")
    assert workbook is not None and rels is not None
    targets: dict[str, str] = {}
    for relation in rels.findall(_P + "Relationship"):
        rel_id = relation.get("Id") or ""
        target = relation.get("Target") or ""
        # Only worksheet parts within the current Office archive are allowed.
        # External TargetMode, traversal and unexpected paths are rejected.
        if relation.get("TargetMode") == "External":
            continue
        candidate = (
            posixpath.normpath(target.lstrip("/"))
            if target.startswith("/")
            else posixpath.normpath(posixpath.join("xl", target))
        )
        if candidate.startswith("xl/worksheets/") and ".." not in candidate.split("/"):
            targets[rel_id] = candidate
    sheets: list[tuple[str, str]] = []
    for item in workbook.iter(_S + "sheet"):
        name, rel_id = item.get("name"), item.get(_R + "id")
        if name and rel_id in targets:
            sheets.append((name, targets[rel_id]))
    if not sheets:
        raise OfficeDocumentError("Excel workbook has no supported worksheets")
    return sheets


def _xlsx(
    archive: _SafeArchive,
    *,
    sheet: str | None,
    cell_range: str | None,
) -> tuple[str, dict[str, Any]]:
    selections = _sheets(archive)
    if sheet is not None and (not isinstance(sheet, str) or not 1 <= len(sheet) <= 128):
        raise OfficeDocumentError("sheet must be a non-empty worksheet name")
    picked = selections[0] if sheet is None else next(
        (item for item in selections if item[0] == sheet), None
    )
    if picked is None:
        raise OfficeDocumentError("worksheet not found")
    bounds = _range(cell_range)
    strings = _shared_strings(archive)
    root = archive.xml(picked[1])
    assert root is not None
    lines: list[str] = []
    seen = 0
    for row in root.iter(_S + "row"):
        raw_row = row.get("r") or ""
        if not raw_row.isdigit():
            raise OfficeDocumentError("Excel row lacks numeric index")
        row_number = int(raw_row)
        if bounds is not None and not bounds[0] <= row_number <= bounds[2]:
            continue
        cells: dict[int, str] = {}
        for cell in row.findall(_S + "c"):
            seen += 1
            if seen > _MAX_CELLS:
                raise OfficeDocumentError("Excel cell count exceeds inspection limit")
            raw_ref = cell.get("r") or ""
            index, column = _ref(raw_ref)
            if index != row_number:
                raise OfficeDocumentError("Excel cell row mismatch")
            if bounds is not None and not bounds[1] <= column <= bounds[3]:
                continue
            kind = cell.get("t") or ""
            value = cell.find(_S + "v")
            inline = cell.find(_S + "is")
            if kind == "s":
                try:
                    value_index = int(value.text if value is not None else "")
                    text = strings[value_index]
                    if value_index < 0:
                        raise ValueError()
                except (TypeError, ValueError, IndexError) as error:
                    raise OfficeDocumentError("Excel shared string index is invalid") from error
            elif kind == "inlineStr" and inline is not None:
                text = "".join(node.text or "" for node in inline.iter(_S + "t"))
            else:
                text = value.text or "" if value is not None else ""
                if value is None and cell.find(_S + "f") is not None:
                    text = "[formula without cached value]"
            cells[column] = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
        if not cells:
            continue
        first_col = bounds[1] if bounds is not None else min(cells)
        last_col = bounds[3] if bounds is not None else max(cells)
        if last_col - first_col + 1 > 256:
            raise OfficeDocumentError("Excel row is too wide; select a smaller cell_range")
        lines.append(str(row_number) + "\t" + "\t".join(
            cells.get(col, "") for col in range(first_col, last_col + 1)
        ))
        if len(lines) > _MAX_ROWS:
            raise OfficeDocumentError("Excel row count exceeds inspection limit")
    return _bounded("\n".join(lines)), {
        "format": "xlsx",
        "sheet": picked[0],
        "sheet_names": [item[0] for item in selections],
        "cell_range": cell_range,
        "returned_rows": len(lines),
        "cells_scanned": seen,
        "formula_evaluated": False,
    }


def extract_office_text(
    raw: bytes,
    suffix: str,
    *,
    sheet: str | None = None,
    cell_range: str | None = None,
) -> tuple[str, dict[str, Any]]:
    if suffix not in {".docx", ".xlsx"}:
        raise OfficeDocumentError("unsupported Office format")
    if suffix == ".docx" and (sheet is not None or cell_range is not None):
        raise OfficeDocumentError("sheet and cell_range apply only to Excel files")
    archive = _SafeArchive(raw)
    try:
        if suffix == ".docx":
            return _docx(archive)
        return _xlsx(archive, sheet=sheet, cell_range=cell_range)
    finally:
        archive.close()
