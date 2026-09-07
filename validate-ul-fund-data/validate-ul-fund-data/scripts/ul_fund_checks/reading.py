"""Turns an .xlsx into a plain Python structure the checks can reason about.

Two things make this layer necessary rather than just calling pandas:

1. We need to know whether a cell *is a formula*, not only what it evaluates
   to. openpyxl cannot give you both from one load, so we load twice.
2. We need real Excel cell references (``FUND_1!B7``) in findings, which means
   keeping row and column positions rather than a tidy DataFrame.

Columns are resolved by header name, never by letter. Column order drifts
between sheets and between quarterly exports; position-based reading is the
single most common way a validator silently checks the wrong data.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

HEADER_ROW = 1
FIRST_DATA_ROW = 2


@dataclass(frozen=True)
class Cell:
    sheet: str
    row: int
    column_letter: str
    value: Any
    is_formula: bool
    number_format: str

    @property
    def ref(self) -> str:
        """Excel-style reference, e.g. ``FUND_1!B7``."""
        return f"{self.sheet}!{self.column_letter}{self.row}"

    @property
    def is_blank(self) -> bool:
        return self.value is None or (isinstance(self.value, str) and not self.value.strip())


@dataclass(frozen=True)
class Column:
    name: str
    sheet: str
    letter: str
    cells: tuple[Cell, ...]

    def __iter__(self) -> Iterator[Cell]:
        return iter(self.cells)

    def where(self, predicate) -> list[Cell]:
        """Cells failing/matching a predicate, in row order."""
        return [c for c in self.cells if predicate(c)]

    @property
    def values(self) -> list[Any]:
        return [c.value for c in self.cells]


@dataclass
class Sheet:
    name: str
    headers: tuple[str, ...]
    columns: dict[str, Column]
    n_data_rows: int
    blank_rows: tuple[int, ...]
    has_merged_cells: bool
    is_hidden: bool

    def column(self, name: str) -> Column | None:
        return self.columns.get(name)

    def rows(self) -> Iterator[dict[str, Cell]]:
        """Data rows as ``{header: Cell}`` dicts, in sheet order."""
        for i in range(self.n_data_rows):
            yield {name: col.cells[i] for name, col in self.columns.items()}


@dataclass
class Book:
    path: Path
    sheets: dict[str, Sheet]

    @property
    def sheet_names(self) -> list[str]:
        return list(self.sheets)

    def __iter__(self) -> Iterator[Sheet]:
        return iter(self.sheets.values())


def _normalise_header(raw: Any) -> str:
    return str(raw).strip() if raw is not None else ""


def read_workbook(path: str | Path) -> Book:
    """Load a workbook into the structure above.

    ``data_only=True`` gives cached formula results; the default load gives the
    formula strings. Neither alone is enough, so we zip them.
    """
    path = Path(path)
    wb_values = load_workbook(path, data_only=True)
    wb_formulas = load_workbook(path, data_only=False)

    sheets: dict[str, Sheet] = {}
    for name in wb_values.sheetnames:
        ws_v, ws_f = wb_values[name], wb_formulas[name]
        sheets[name] = _read_sheet(name, ws_v, ws_f)
    return Book(path=path, sheets=sheets)


def _read_sheet(name, ws_values, ws_formulas) -> Sheet:
    headers = tuple(
        _normalise_header(c.value) for c in ws_values[HEADER_ROW] if _normalise_header(c.value)
    )
    last_row = ws_values.max_row or HEADER_ROW
    all_rows = list(range(FIRST_DATA_ROW, last_row + 1))

    def read(row: int, index: int, letter: str) -> Cell:
        return Cell(
            sheet=name,
            row=row,
            column_letter=letter,
            value=ws_values.cell(row, index).value,
            is_formula=_is_formula(ws_formulas.cell(row, index).value),
            number_format=ws_values.cell(row, index).number_format,
        )

    positions = [(header, i, get_column_letter(i)) for i, header in enumerate(headers, start=1)]
    grid = {row: [read(row, i, letter) for _, i, letter in positions] for row in all_rows}

    def row_is_blank(row: int) -> bool:
        return all(cell.is_blank for cell in grid[row])

    # Trailing blank rows are an artefact of how the file was saved, not a
    # defect. Blank rows *between* data rows are, and W3 reports them.
    while all_rows and row_is_blank(all_rows[-1]):
        all_rows.pop()
    blank_rows = tuple(row for row in all_rows if row_is_blank(row))

    # Blank rows are excluded from the column data so that one structural
    # defect produces one finding, rather than a not-blank failure on every
    # column that happens to sit on that row.
    data_rows = [row for row in all_rows if row not in blank_rows]

    columns: dict[str, Column] = {}
    for position, (header, _, letter) in enumerate(positions):
        columns[header] = Column(
            name=header,
            sheet=name,
            letter=letter,
            cells=tuple(grid[row][position] for row in data_rows),
        )

    return Sheet(
        name=name,
        headers=headers,
        columns=columns,
        n_data_rows=len(data_rows),
        blank_rows=blank_rows,
        has_merged_cells=bool(ws_values.merged_cells.ranges),
        is_hidden=ws_values.sheet_state != "visible",
    )


def _is_formula(raw: Any) -> bool:
    return isinstance(raw, str) and raw.startswith("=")
