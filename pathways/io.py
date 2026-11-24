"""CSV exchange-bundle loading and validation."""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

INT_FIELDS = {
    "term", "round", "received_round", "capacity", "published_round",
    "term_start", "term_end", "required_credits", "last_start",
    "release_round", "last_transfer_round",
}


@dataclass
class Bundle:
    root: Path
    manifest: Dict[str, List[str]]
    tables: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)

    def rows(self, name: str) -> List[Dict[str, Any]]:
        return self.tables[name]

    def table(self, filename: str) -> List[Dict[str, Any]]:
        return self.tables[filename]


def _convert(fieldname: str, value: str) -> Any:
    if value == "":
        return None
    if fieldname in INT_FIELDS:
        try:
            return int(value)
        except ValueError:
            return value
    return value


def load_bundle(root: str | Path) -> Bundle:
    """Load all tables declared by ``manifest.json``.

    Headers and row widths are validated exactly as in the supplied bundle
    inspector.  Numeric fields are converted to :class:`int`; blank cells become
    ``None``.
    """
    root = Path(root)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    tables: Dict[str, List[Dict[str, Any]]] = {}
    for filename, header in manifest.items():
        path = root / filename
        with path.open(encoding="utf-8", newline="") as source:
            reader = csv.reader(source, strict=True)
            actual_header = next(reader, None)
            if actual_header != header:
                raise ValueError(f"{filename} 的表头与资料清单不符")
            records: List[Dict[str, Any]] = []
            for line_number, raw in enumerate(reader, start=2):
                if len(raw) != len(header):
                    raise ValueError(f"{filename} 第 {line_number} 行的字段数量不符")
                record = {
                    key: _convert(key, value)
                    for key, value in zip(header, raw)
                }
                record["_line"] = line_number
                records.append(record)
            tables[filename] = records
    return Bundle(root=root, manifest=manifest, tables=tables)
