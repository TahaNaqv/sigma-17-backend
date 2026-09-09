"""Cheap sheet-name probe for .xlsx bytes.

Chaining needs to know which sheets a produced workbook contains so it can tell
whether a downstream engine could actually consume it (see
`source_resolver.CONSUMER_REQUIREMENTS`). That question is asked on request
paths — a picker page, a job submit — so it must not cost a full parse.

`openpyxl.load_workbook(read_only=True)` still builds the shared-string table
and a worksheet object per sheet; pandas is heavier again. An xlsx is a ZIP, and
the sheet names live in exactly one small part of it (`xl/workbook.xml`), so
reading that single member and pulling the `<sheet name=...>` attributes answers
the question in microseconds against a workbook of any size.

Every failure mode (not a ZIP, no workbook.xml, malformed XML, a legacy .xls)
returns `[]` rather than raising: callers treat "no sheets readable" as "unknown",
never as "definitely unusable", so a corrupt or unreadable archive can never
silently remove a job from a picker.
"""

from __future__ import annotations

import io
import logging
import zipfile
from xml.etree import ElementTree

logger = logging.getLogger(__name__)

# The part every xlsx has, holding the sheet list in document order.
_WORKBOOK_PART = "xl/workbook.xml"


def sheet_names_from_xlsx_bytes(raw: bytes) -> list[str]:
    """Return the sheet names of an .xlsx, in workbook order. `[]` if unreadable.

    Hidden sheets are included: a consuming engine reads a sheet by name through
    pandas, which does not care about visibility, so excluding them here would
    under-report what the workbook can satisfy.
    """
    if not raw:
        return []
    try:
        with zipfile.ZipFile(io.BytesIO(raw), "r") as zf:
            try:
                part = zf.read(_WORKBOOK_PART)
            except KeyError:
                return []
    except (zipfile.BadZipFile, OSError):
        # A legacy .xls (OLE2, not a ZIP) lands here, as does a truncated upload.
        return []

    try:
        root = ElementTree.fromstring(part)
    except ElementTree.ParseError:
        return []

    names: list[str] = []
    for element in root.iter():
        # Tags are namespaced (`{...spreadsheetml/2006/main}sheet`); match on the
        # local name so a different namespace revision still parses.
        if element.tag.rsplit("}", 1)[-1] != "sheet":
            continue
        name = element.get("name")
        if name:
            names.append(name)
    return names


def missing_sheets(present: list[str], required: tuple[str, ...]) -> list[str]:
    """Required sheets absent from `present`, in the order `required` declares.

    Comparison is exact — the engines read sheets by exact name through pandas,
    so a case or whitespace variant genuinely does not resolve and must be
    reported as missing rather than quietly accepted.
    """
    have = set(present)
    return [sheet for sheet in required if sheet not in have]
