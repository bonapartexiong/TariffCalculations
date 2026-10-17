"""Rate parsing for HTS ``Rates of Duty`` cells.

``parse_rate(raw)`` converts one raw rate cell (General, Special program
rate, or Column 2) into the Rate dict defined in CONTRACTS.md::

    {
      "kind": "free|ad_valorem|specific|compound|cross_ref|uncomputable|empty",
      "raw": str,
      "components": [{"value": str(Decimal), "unit": str, "per": str}],
      "requires_quantity": bool,
      "note": str,
      "cross_ref_target": str | None,
    }

Values are Decimal strings -- this module NEVER returns a float.
Units are normalized (see :func:`normalize_unit`): ``¢``/``cent`` ->
``cent``, ``$`` -> ``dollar``, ``%``/``percent`` -> ``percent``, and
per-units (``kg``, ``doz``, ``each``, ``jewel``, ``article``, ``liter``,
``head``, ``gross``, ``pair``, ``No.`` -> ``number``) are lowercased and
de-abbreviated.

``parse_special(raw)`` parses a Special-column cell such as
``"Free (A, AU, BH, ...)"`` into per-program rates.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

# ---------------------------------------------------------------------------
# Unit normalization
# ---------------------------------------------------------------------------

_UNIT_ALIASES = {
    # per-units / denominators
    "kg": "kg",
    "kgs": "kg",
    "kilogram": "kg",
    "kilograms": "kg",
    "doz": "doz",
    "dozen": "doz",
    "each": "each",
    "jewel": "jewel",
    "jewels": "jewel",
    "jwl": "jewel",
    "jwls": "jewel",
    "article": "article",
    "articles": "article",
    "liter": "liter",
    "liters": "liter",
    "litre": "liter",
    "litres": "liter",
    "l": "liter",
    "head": "head",
    "heads": "head",
    "gross": "gross",
    "pair": "pair",
    "pairs": "pair",
    "no": "number",
    "number": "number",
    "numbers": "number",
    "nos": "number",
    "piece": "piece",
    "pieces": "piece",
    "ton": "ton",
    "tons": "ton",
    "tonne": "tonne",
    "tonnes": "tonne",
    "meter": "meter",
    "meters": "meter",
    "metre": "meter",
    "metres": "meter",
    "m": "meter",
    "m2": "m2",
    "sqm": "m2",
    "carat": "carat",
    "carats": "carat",
    "gram": "gram",
    "grams": "gram",
    "g": "gram",
    "oz": "oz",
    "lb": "lb",
    "lbs": "lb",
    "barrel": "barrel",
    "barrels": "barrel",
    "bbl": "barrel",
    "proof": "proof",
    "pf": "proof",
}


def normalize_unit(token: str | None) -> str:
    """Normalize a per-unit token (``"doz."`` -> ``"doz"``, ``"No."`` -> ``"number"``)."""
    if token is None:
        return ""
    key = token.strip().lower().rstrip(".")
    return _UNIT_ALIASES.get(key, key)


# ---------------------------------------------------------------------------
# Compound splitting (split on '+' respecting parentheses)
# ---------------------------------------------------------------------------


def split_compound(text: str) -> list[str]:
    """Split ``text`` on top-level ``+`` signs (parentheses respected)."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in text:
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth = max(0, depth - 1)
            current.append(ch)
        elif ch == "+" and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return [p.strip().strip("+").strip() for p in parts if p.strip().strip("+").strip()]


# ---------------------------------------------------------------------------
# Single-component parsing
# ---------------------------------------------------------------------------

_HTS_RE = re.compile(r"\d{4}\.\d{2}(?:\.\d{2})?")

_NUM = r"([\d,]+(?:\.\d+)?)"
# NOTE: no \b after "%" / "¢" -- both are non-word characters, so a
# word boundary there never matches at end-of-string.
_PERCENT_RE = re.compile(rf"^{_NUM}\s*(%|percent)\s*(.*)$", re.IGNORECASE)
_CENT_RE = re.compile(
    rf"^{_NUM}\s*(¢|cents?)\s*(?:/\s*([A-Za-z]+\.?)|per\s+([A-Za-z]+\.?)|([A-Za-z]+\.?))?\s*$",
    re.IGNORECASE,
)
_DOLLAR_RE = re.compile(
    rf"^\$\s*{_NUM}\s*(?:/\s*([A-Za-z]+\.?)|per\s+([A-Za-z]+\.?)|([A-Za-z]+\.?))?\s*$"
)
_BARE_RE = re.compile(
    rf"^{_NUM}\s*(?:/\s*([A-Za-z]+\.?)|per\s+([A-Za-z]+\.?))\s*$"
)


def _dec(text: str) -> str:
    """Parse a numeric literal into a Decimal string (never float)."""
    try:
        return str(Decimal(text.replace(",", "").strip()))
    except InvalidOperation:
        raise ValueError(f"Not a decimal number: {text!r}")


def _parse_single_component(part: str) -> dict | None:
    """Parse one (non-compound) rate fragment into a component dict.

    Returns ``None`` if the fragment does not match any known shape.
    """
    part = part.strip()
    if not part:
        return None

    m = _PERCENT_RE.match(part)
    if m:
        rest = m.group(3).strip().lower()
        if rest in ("", "ad valorem", "ad val.", "ad val"):
            return {"value": _dec(m.group(1)), "unit": "percent", "per": "value"}

    m = _CENT_RE.match(part)
    if m:
        per = normalize_unit(m.group(3) or m.group(4) or m.group(5))
        return {"value": _dec(m.group(1)), "unit": "cent", "per": per or "value"}

    m = _DOLLAR_RE.match(part)
    if m:
        per = normalize_unit(m.group(2) or m.group(3) or m.group(4))
        return {"value": _dec(m.group(1)), "unit": "dollar", "per": per or "value"}

    m = _BARE_RE.match(part)
    if m:
        per = normalize_unit(m.group(2) or m.group(3))
        return {"value": _dec(m.group(1)), "unit": "unit", "per": per or "value"}

    return None


def _extract_ref_target(text: str) -> str | None:
    """Extract an HTS reference (``9903.90.08``) or heading (``9902``)."""
    m = _HTS_RE.search(text)
    if m:
        return m.group(0)
    m = re.search(r"\d{4}", text)
    return m.group(0) if m else None


def _rate(
    kind: str,
    raw: str,
    components: list[dict] | None = None,
    note: str = "",
    cross_ref_target: str | None = None,
) -> dict:
    requires_quantity = bool(
        components and any(c["unit"] != "percent" for c in components)
    )
    return {
        "kind": kind,
        "raw": raw,
        "components": components or [],
        "requires_quantity": requires_quantity,
        "note": note,
        "cross_ref_target": cross_ref_target,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_rate(raw: Any) -> dict:
    """Parse one raw rate cell into a Rate dict (CONTRACTS.md).

    Handles: ``"Free"``, ad valorem (``"2.5%"``), specific (``"1¢/kg"``),
    compound (``"17¢ each + 2.5% + 1¢/jewel"``), cross-references
    (``"See 9903.90.08"``, ``"The rate applicable to ..."``), uncomputable
    (``"A duty upon the value of repairs ..."``, ``"No change"``), and
    empty cells. Never returns a float.
    """
    raw_out = "" if raw is None else str(raw)
    s = raw_out.strip()
    if not s:
        return _rate("empty", raw_out)

    low = s.lower()

    # "No change" (Chapter 99 subchapter II): the rate is unchanged from
    # the applicable subheading, so there is no standalone computable rate.
    if low.startswith("no change"):
        return _rate(
            "uncomputable",
            raw_out,
            note="No change: the rate is unchanged from the applicable "
            "subheading; resolve via that subheading.",
        )

    # Duty on the value of repairs/alterations (e.g. Chapter 98): the
    # duty base is the value of the repairs, not the article's value,
    # so no single ad valorem/specific rate is computable from the cell.
    if "value of repairs" in low and "duty" in low:
        return _rate(
            "uncomputable",
            raw_out,
            note="Duty is assessed upon the value of repairs or "
            "alterations, not upon the full value of the article, so no "
            "single computable rate can be derived from this cell.",
        )

    # Strip parenthetical program lists (Special column) and footnote
    # markers for rate parsing; the original text is kept in "raw".
    s2 = re.sub(r"\([^()]*\)", "", s).strip()
    s2 = re.sub(r"\s*\d+/\s*$", "", s2).strip()
    s2 = s2.lstrip("+").strip()
    low2 = s2.lower()

    # Cross-references: "See 9902...", "See 9903.90.08",
    # "See 9822.04.25 (AU)".
    if low2.startswith("see "):
        target = _extract_ref_target(s2)
        note = (
            f"Cross-reference: see {target} for the applicable rate."
            if target
            else "Cross-reference: see the referenced provision for the applicable rate."
        )
        return _rate("cross_ref", raw_out, note=note, cross_ref_target=target)

    # "The rate applicable to ..." / "The duty provided in the applicable
    # subheading [plus X]" (Chapter 99): the base rate comes from another
    # subheading, optionally plus an additional component.
    if "rate applicable to" in low2 or "applicable subheading" in low2:
        if "plus" in low2:
            after_plus = low2.split("plus", 1)[1].strip()
            extra = _parse_single_component(after_plus)
            if extra is not None:
                return _rate(
                    "compound",
                    raw_out,
                    components=[extra],
                    note="Base duty is the rate of the applicable subheading, "
                    "plus the listed component(s).",
                )
        target = _extract_ref_target(s2)
        note = (
            "Rate is the rate applicable to the referenced subheading/article."
            if target is None
            else f"Rate is the rate applicable to subheading {target}."
        )
        return _rate("cross_ref", raw_out, note=note, cross_ref_target=target)

    # Free (with optional trailing punctuation / stripped parens).
    if re.fullmatch(r"free[\*\s.]*", s2, re.IGNORECASE):
        return _rate("free", raw_out)

    # Compound / single-component rates.
    parts = split_compound(s2)
    components: list[dict] = []
    for part in parts:
        comp = _parse_single_component(part)
        if comp is None:
            return _rate(
                "uncomputable",
                raw_out,
                note=f"Could not parse rate component {part!r}; not computed.",
            )
        components.append(comp)

    if not components:
        return _rate("empty", raw_out)
    if len(components) == 1:
        kind = "ad_valorem" if components[0]["unit"] == "percent" else "specific"
        return _rate(kind, raw_out, components=components)
    return _rate("compound", raw_out, components=components)


_PROGRAM_GROUP_RE = re.compile(r"(.+?)\s*\(([^()]*)\)")


def parse_special(raw: Any) -> dict:
    """Parse a Special-column cell into per-program rates.

    Returns ``{"programs": [{"symbol": str, "rate": RateDict}], "raw": str}``.

    Examples:
      ``"Free (A, AU, BH, ...)"`` -> one ``Free`` rate per symbol.
      ``"See 9822.04.25 (AU)"``   -> the cross-reference rate for ``AU``.
      ``"No change"`` / ``""``    -> no programs.
    """
    raw_out = "" if raw is None else str(raw)
    s = raw_out.strip()
    if not s:
        return {"programs": [], "raw": raw_out}
    if s.lower().startswith("no change"):
        return {"programs": [], "raw": raw_out}

    programs: list[dict] = []
    groups = list(_PROGRAM_GROUP_RE.finditer(s))
    if groups:
        for m in groups:
            rate_part = m.group(1).strip()
            symbols = [sym.strip() for sym in m.group(2).split(",")]
            rate = parse_rate(rate_part)
            for sym in symbols:
                if sym:
                    programs.append({"symbol": sym, "rate": rate})
        return {"programs": programs, "raw": raw_out}

    rate = parse_rate(s)
    if rate["kind"] == "empty":
        return {"programs": [], "raw": raw_out}
    # No parenthetical symbols: the rate applies without a named program.
    return {"programs": [{"symbol": "", "rate": rate}], "raw": raw_out}
