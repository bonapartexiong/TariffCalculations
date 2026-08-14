"""
Build the compact, runtime-ready tariff dataset (JSON) from tariffs.xlsx.

This script uses only the Python standard library so it can be run anywhere
without pandas/openpyxl. The output JSON is committed to the repo and loaded
by the API at runtime, which keeps the deployed backend tiny and fast to
cold-start (no Excel parsing, no pandas, no scikit-learn).

Usage:
    python scripts/build_tariff_data.py [path/to/tariffs.xlsx] [path/to/tariffs.json]
"""
from __future__ import annotations

import gzip
import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_XLSX = REPO_ROOT / "tariffs.xlsx"
DEFAULT_OUT = REPO_ROOT / "backend" / "data" / "tariffs.json"


def _read_xlsx_rows(path: Path):
    """Read the first worksheet of an .xlsx into a list of dicts keyed by column letter."""
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall(MAIN_NS + "si"):
                shared.append(
                    "".join(t.text or "" for t in si.iter(MAIN_NS + "t"))
                )

        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
        rows = sheet.findall(".//" + MAIN_NS + "row")

        def cell_value(c):
            t = c.get("t")
            v = c.find(MAIN_NS + "v")
            val = v.text if v is not None else ""
            if t == "s" and val != "":
                val = shared[int(val)]
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(MAIN_NS + "t"))
            return val

        out = []
        for row in rows:
            row_dict = {}
            for c in row.findall(MAIN_NS + "c"):
                ref = c.get("r") or ""
                col = re.match(r"[A-Z]+", ref)
                if col:
                    row_dict[col.group(0)] = cell_value(c)
            out.append(row_dict)
    return out


def _clean_text(value: str) -> str:
    """Normalize a tariff text cell for display and search."""
    text = (value or "").strip()
    # Collapse the hierarchical separators and whitespace runs.
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*([;:])\s*", r"\1 ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ;:-")


def _search_text(group: str, description: str) -> str:
    """Build a lowercase searchable string for semantic matching.

    The "Modified Description" column (leaf path) is the specific, self-contained
    text that distinguishes one tariff line from another. The "Group" column is
    the huge chapter heading shared by hundreds of rows, so including it dilutes
    the discriminating terms. We use the leaf path, falling back to the group
    only when the leaf is empty.
    """
    text = (description or group).lower()
    text = re.sub(r"[;:]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _normalize_tariff(raw: str):
    """Parse a tariff cell and return a clean float fraction (0.08 == 8%)."""
    if raw is None or str(raw).strip() == "":
        return 0.0
    value = float(str(raw).strip())
    # Auto-normalize percentages stored as whole numbers (e.g. 8 -> 0.08).
    if value > 1:
        value = value / 100.0
    # Round away floating-point noise from the spreadsheet (6.5E-2 -> 0.065).
    return round(value, 6)


def build(source: Path, out: Path) -> dict:
    rows = _read_xlsx_rows(source)

    header = rows[0] if rows else {}
    # Column letters -> expected header names (A=HTS Number, B=Group,
    # C=Modified Description, D=Tariff, E=Description).
    records = []
    skipped = 0
    for row in rows[1:]:
        hts = str(row.get("A", "") or "").strip()
        group = _clean_text(row.get("B", ""))
        description = _clean_text(row.get("C", ""))
        tariff = _normalize_tariff(row.get("D", ""))

        search_text = _search_text(group, description)
        if not search_text:
            skipped += 1
            continue

        records.append(
            {
                "hts_number": hts,
                "group": group,
                "description": description or group,
                "tariff": tariff,
                "search_text": search_text,
            }
        )

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": source.name,
        "count": len(records),
        "skipped": skipped,
        "records": records,
    }

    out.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False)
    out.write_text(serialized, encoding="utf-8")

    # Also write a gzip copy so the committed artifact stays small and
    # cold-start load times stay low. The loader prefers the .gz variant.
    gz_path = out.with_suffix(out.suffix + ".gz")
    with gzip.open(gz_path, "wt", encoding="utf-8") as fh:
        fh.write(serialized)

    size_mb = out.stat().st_size / (1024 * 1024)
    gz_mb = gz_path.stat().st_size / (1024 * 1024)
    return {
        "count": len(records),
        "skipped": skipped,
        "size_mb": round(size_mb, 2),
        "gz_size_mb": round(gz_mb, 2),
        "out": str(out),
        "gz_out": str(gz_path),
    }


if __name__ == "__main__":
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_XLSX
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUT
    result = build(source, out)
    print(f"Wrote {result['count']} records ({result['skipped']} skipped) to {result['out']}")
    print(f"JSON size: {result['size_mb']} MB, gzip size: {result['gz_size_mb']} MB")
