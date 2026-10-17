"""Deterministic Decimal calculator (Module B). NO LLM.

calculate(request) per CONTRACTS.md. Never uses float for money.
All money values are Decimal, each component rounded HALF_UP to cents,
subtotal = sum of the rounded components.

Rate dicts come from rag/rates.py (parse_rate / parse_special) when that
module exists; a defensive fallback stub is used if it is missing.
"""
import re
from decimal import Decimal, ROUND_HALF_UP

try:
    from rag.rates import parse_rate, parse_special
except Exception:  # pragma: no cover - defensive fallback if rates.py missing
    def parse_rate(raw):
        """Minimal fallback parser (used only if rag/rates.py is absent)."""
        if raw is None or str(raw).strip() == "":
            return {"kind": "empty", "raw": "" if raw is None else str(raw),
                    "components": [], "requires_quantity": False,
                    "note": "Empty rate.", "cross_ref_target": None}
        s = str(raw).strip()
        low = s.lower()
        if low in ("free", "free*") or low.startswith("free ") or low.startswith("free("):
            return {"kind": "free", "raw": s, "components": [],
                    "requires_quantity": False, "note": "",
                    "cross_ref_target": None}
        if low.startswith("see "):
            return {"kind": "cross_ref", "raw": s, "components": [],
                    "requires_quantity": False,
                    "note": "Cross-reference rate.", "cross_ref_target": s[4:].strip()}
        pct = re.search(r"([\d.,]+)\s*%", s)
        spec = re.search(
            r"\$?\s*([\d.,]+)\s*(?:¢|c)?\s*per\s+([a-z ]+)", low)
        comps = []
        if pct:
            comps.append({"value": pct.group(1).replace(",", ""),
                          "unit": "percent", "per": ""})
        if spec:
            unit = spec.group(2).strip().split()[0]
            comps.append({"value": spec.group(1).replace(",", ""),
                          "unit": unit, "per": unit})
        if pct and spec:
            kind = "compound"
        elif pct:
            kind = "ad_valorem"
        elif spec:
            kind = "specific"
        else:
            return {"kind": "uncomputable", "raw": s, "components": [],
                    "requires_quantity": False,
                    "note": "Could not parse rate: %s" % s,
                    "cross_ref_target": None}
        return {"kind": kind, "raw": s, "components": comps,
                "requires_quantity": kind in ("specific", "compound"),
                "note": "", "cross_ref_target": None}

    def parse_special(special_raw):
        return {"programs": [], "raw": special_raw or ""}


try:
    from rag.fees_config import (
        MPF_RATE as _MPF_RATE, MPF_MIN as _MPF_MIN, MPF_CAP as _MPF_CAP,
        HMF_RATE as _HMF_RATE, FEES_SOURCE,
    )
except Exception:  # pragma: no cover
    MPF_RATE = Decimal("0.003464")
    MPF_MIN = Decimal("32.71")
    MPF_CAP = Decimal("575")
    HMF_RATE = Decimal("0.00125")
    FEES_SOURCE = "CBP FY2025 MPF/HMF config (external — not in HTS PDF)"
else:
    MPF_RATE, MPF_MIN, MPF_CAP, HMF_RATE = _MPF_RATE, _MPF_MIN, _MPF_CAP, _HMF_RATE

REVISION = "2026 Rev 21"
CENT = Decimal("0.01")
DE_MINIMIS_LIMIT = Decimal("800")

COL2_COUNTRIES = {"north korea", "belarus", "russia", "cuba"}
SUSPENDED_ORIGINS = {"china"}

# MPF-exempt programs per GSN. Exemption applies ONLY if the program is
# actually claimed (i.e. its rate was the one selected).
MPF_EXEMPT_PROGRAMS = {
    "AU", "BH", "CA", "CL", "CO", "S", "SG", "KR", "OM", "P", "PA", "PE",
    "IL", "E", "A", "MX", "JO", "J", "D", "R",
}


def _to_decimal(x):
    if x is None:
        return None
    if isinstance(x, Decimal):
        return x
    if isinstance(x, bool):
        return Decimal(int(x))
    if isinstance(x, int):
        return Decimal(x)
    if isinstance(x, float):
        return Decimal(str(x))  # str() avoids binary-float artefacts
    s = str(x).strip().replace(",", "").replace("$", "")
    if s == "":
        return None
    return Decimal(s)


def _round(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def _normalize_country(name):
    if not name:
        return ""
    s = re.sub(r"\s+", " ", str(name).strip().lower().replace(",", " "))
    s = s.replace("'", "").replace(".", "")
    if "korea" in s and "north" in s:
        return "north korea"
    if s in ("korea democratic peoples republic of",
             "democratic peoples republic of korea", "dprk", "korea dprk",
             "north korea"):
        return "north korea"
    if "russian federation" in s or "russia" in s:
        return "russia"
    if "belarus" in s:
        return "belarus"
    if "cuba" in s:
        return "cuba"
    if "china" in s or s in ("prc", "peoples republic of china"):
        return "china"
    return s


def _normalize_program(program):
    if program is None:
        return None
    s = str(program).strip().upper().strip("()").strip()
    if s == "GSP":
        return "A"
    if s.lower() in ("auto-lowest", "auto_lowest", "auto lowest"):
        return "AUTO-LOWEST"
    if s.replace("_", "-").lower() == "auto-lowest":
        return "AUTO-LOWEST"
    return s


def _rate_from_raw(raw):
    """Accept a Rate dict (returned as-is) or a raw string (parsed)."""
    if raw is None:
        return None
    if isinstance(raw, dict) and "kind" in raw:
        return raw
    return parse_rate(str(raw))


def _rate_from_chunk_field(chunk, *keys):
    for k in keys:
        if chunk.get(k):
            return _rate_from_raw(chunk.get(k))
    return None


def _special_rates(chunk, req):
    """symbol -> Rate dict, from chunk + request + parse_special."""
    out = {}
    for src in (chunk.get("special_programs") or [],
                req.get("special_programs") or []):
        for entry in src:
            if not isinstance(entry, dict):
                continue
            sym = str(entry.get("symbol") or "").strip().upper()
            if not sym:
                continue
            rate = None
            if entry.get("rate") is not None:
                rate = _rate_from_raw(entry.get("rate"))
            elif entry.get("rate_raw"):
                rate = _rate_from_raw(entry.get("rate_raw"))
            if rate is not None:
                out[sym] = rate
    for raw_key in ("special_raw",):
        for src in (chunk.get(raw_key), req.get(raw_key)):
            if src:
                try:
                    parsed = parse_special(src)
                except Exception:
                    parsed = {"programs": []}
                for entry in parsed.get("programs") or []:
                    sym = str(entry.get("symbol") or "").strip().upper()
                    if sym and entry.get("rate") is not None:
                        out.setdefault(sym, entry.get("rate"))
    return out


def _norm_per_unit(u):
    """Normalize a per-unit for comparison: No./each, doz., liters, etc."""
    t = str(u or "").lower().strip().rstrip(".")
    return {
        "no": "each", "number": "each", "each": "each", "ea": "each",
        "doz": "doz", "dozen": "doz", "dozens": "doz",
        "liter": "liter", "liters": "liter", "litre": "liter", "litres": "liter", "l": "liter",
        "kg": "kg", "kilogram": "kg", "kilograms": "kg",
        "gross": "gross", "pair": "pair", "pairs": "pair",
        "head": "head", "jewel": "jewel", "jewels": "jewel",
        "article": "article", "articles": "article",
        "value": "value", "": "",
    }.get(t, t)


def _duty_for_rate(rate, value, quantity, quantity_unit, req):
    """Return (duty Decimal|None, computable bool, explanation str, warnings)."""
    warnings = []
    if rate is None:
        return None, False, "No rate available for this line.", warnings
    kind = rate.get("kind")
    raw = rate.get("raw") or ""
    if kind == "free":
        return Decimal("0"), True, "", warnings
    if kind in ("empty",):
        return None, False, "Rate is empty — no computable duty.", warnings
    if kind in ("cross_ref", "uncomputable"):
        note = rate.get("note") or ("Cross-reference rate: %s" % raw)
        target = rate.get("cross_ref_target")
        if target:
            note += " See %s." % target
        return None, False, note, warnings
    comps = rate.get("components") or []
    if not comps:
        return None, False, "Rate has no components: %s" % raw, warnings

    total = Decimal("0")
    for comp in comps:
        try:
            amt = _to_decimal(comp.get("value"))
        except Exception:
            return None, False, "Unparsable rate component: %s" % (comp,), warnings
        unit = str(comp.get("unit") or "").lower().strip()
        if unit == "percent":
            if amt is None:
                return None, False, "Missing percent value in rate.", warnings
            total += value * amt / Decimal("100")
        else:
            # Specific component. Two shapes exist:
            #  * new parser: unit = currency ("cent"/"dollar"), per = per-unit
            #  * legacy tests: unit IS the per-unit ("jewel"), amount in dollars
            # Cent amounts are cents, not dollars: 100 kg x 9.35c/kg = $9.35,
            # not $935 (previously the /100 was missing entirely).
            if unit in ("cent", "cents", "\u00a2", "c"):
                amt_dollars = (amt / Decimal("100")) if amt is not None else Decimal("0")
                per_unit = str(comp.get("per") or "").lower().strip()
            elif unit in ("dollar", "dollars", "$", "usd"):
                amt_dollars = amt if amt is not None else Decimal("0")
                per_unit = str(comp.get("per") or "").lower().strip()
            else:
                amt_dollars = amt if amt is not None else Decimal("0")
                per_unit = unit
            per_norm = _norm_per_unit(per_unit)
            qty = None
            if per_norm == "jewel":
                jewel_count = req.get("jewel_count")
                if jewel_count is None:
                    jewel_count = req.get("quantity_jewels")
                if jewel_count is not None:
                    qty = _to_decimal(jewel_count)
                elif _norm_per_unit(quantity_unit or "") == "jewel":
                    qty = quantity
            else:
                qty = quantity
            if qty is None:
                return (
                    None, False,
                    "Specific rate (%s per %s) requires a quantity, but none "
                    "was supplied." % (raw, per_unit or "unit"),
                    warnings,
                )
            if (
                quantity_unit
                and per_norm != "jewel"
                and per_norm
                and _norm_per_unit(quantity_unit) != per_norm
            ):
                warnings.append(
                    "Quantity unit %r differs from rate unit %r; quantity "
                    "applied as supplied." % (quantity_unit, per_unit)
                )
            total += qty * amt_dollars
    return total, True, "", warnings


def _parse_stack_entry(entry, req):
    """Return (hts|None, Rate|None, stacking|None) for one stack entry."""
    stacking = None
    if isinstance(entry, str):
        s = entry.strip()
        # Rate-like string ("25%", "additional 25%", "Free") vs HTS code.
        if re.search(r"%|free|per\s+\w+", s.lower()) and not re.fullmatch(
            r"\d{4}(?:\.\d{2}){0,3}", s
        ):
            raw = re.sub(r"^additional\s+", "", s, flags=re.I)
            return None, _rate_from_raw(raw), None
        # Bare HTS code: look up a caller-supplied rate mapping.
        mapping = req.get("chapter99_rates") or {}
        if s in mapping:
            return s, _rate_from_raw(mapping[s]), None
        return s, None, None
    if isinstance(entry, dict):
        if "kind" in entry:  # entry IS a Rate dict
            return entry.get("hts"), entry, entry.get("stacking")
        stacking = entry.get("stacking") or entry.get("stack")
        hts = entry.get("hts") or entry.get("code")
        rate = None
        if entry.get("rate") is not None:
            rate = _rate_from_raw(entry.get("rate"))
        elif entry.get("rate_raw"):
            rate = _rate_from_raw(entry.get("rate_raw"))
        elif entry.get("general_raw"):
            rate = _rate_from_raw(entry.get("general_raw"))
        elif entry.get("raw"):
            rate = _rate_from_raw(entry.get("raw"))
        elif hts and (req.get("chapter99_rates") or {}).get(hts) is not None:
            rate = _rate_from_raw(req["chapter99_rates"][hts])
        return hts, rate, stacking
    return None, None, None


def calculate(req):
    """Deterministic duty calculation. See module docstring / CONTRACTS."""
    req = dict(req or {})
    warnings = []

    # ------------------------------------------------------------ inputs
    try:
        value = _to_decimal(req.get("value", 0))
    except Exception:
        value = None
    quantity = None
    if req.get("quantity") is not None:
        try:
            quantity = _to_decimal(req.get("quantity"))
        except Exception:
            quantity = None
    quantity_unit = req.get("quantity_unit")
    origin = req.get("origin_country") or req.get("origin") or ""
    origin_norm = _normalize_country(origin)
    program = _normalize_program(req.get("program"))
    mode = str(req.get("mode") or "").strip().lower()
    quota_status = str(req.get("quota_status") or "unknown").strip().lower()
    de_minimis = bool(req.get("de_minimis"))
    chunk = req.get("chunk") or {}
    if not isinstance(chunk, dict):
        chunk = {}

    if value is None:
        return _result(
            req, False, Decimal("0"), [], Decimal("0"), Decimal("0"),
            warnings + ["Value is missing or unparsable; cannot compute duty."],
            chunk, None, None, explanation="Value missing/unparsable.",
        )

    # ------------------------------------------------------- rate selection
    general_rate = None
    if req.get("general_rate") is not None:
        general_rate = _rate_from_raw(req.get("general_rate"))
    elif req.get("rate") is not None:
        general_rate = _rate_from_raw(req.get("rate"))
    elif req.get("general_raw"):
        general_rate = _rate_from_raw(req.get("general_raw"))
    else:
        general_rate = _rate_from_chunk_field(chunk, "general_raw")

    col2_rate = None
    if req.get("col2_rate") is not None:
        col2_rate = _rate_from_raw(req.get("col2_rate"))
    else:
        col2_rate = _rate_from_chunk_field(chunk, "col2_raw")

    specials = _special_rates(chunk, req)

    selected_program = None
    selected_rate = None
    is_col2 = origin_norm in COL2_COUNTRIES

    if is_col2:
        selected_rate = col2_rate
        warnings.append(
            "Origin %s is a Column 2 country — Column 2 rate applied."
            % (origin or origin_norm)
        )
        if selected_rate is None or selected_rate.get("kind") == "empty":
            return _result(
                req, False, Decimal("0"), [], Decimal("0"), Decimal("0"),
                warnings + ["No Column 2 rate available for this line."],
                chunk, None, None,
                explanation="No Column 2 rate available.",
            )
    elif program and program != "AUTO-LOWEST":
        if program in specials:
            selected_rate = specials[program]
            selected_program = program
        else:
            selected_rate = general_rate
            warnings.append(
                "Program %s rate not found for this line; falling back to "
                "the General rate." % program
            )
    elif program == "AUTO-LOWEST":
        # Lowest numeric duty among same-kind Special + General candidates.
        general_kind = (general_rate or {}).get("kind")
        candidates = [("General", general_rate)]
        candidates += [(sym, r) for sym, r in specials.items()]
        same_kind = [
            (label, r) for label, r in candidates
            if r is not None and r.get("kind") == general_kind
        ]
        if len(same_kind) < len(candidates):
            warnings.append(
                "Auto-lowest compares only same-kind rates; different-kind "
                "Special rates were not compared against General."
            )
        best = None  # (duty, label, rate)
        for label, r in same_kind:
            duty, ok, _expl, w = _duty_for_rate(
                r, value, quantity, quantity_unit, req)
            warnings.extend(w)
            if ok and (best is None or duty < best[0]):
                best = (duty, label, r)
        if best is not None:
            _duty, label, selected_rate = best
            selected_program = None if label == "General" else label
            if selected_program:
                warnings.append(
                    "Auto-lowest selected program %s." % selected_program)
        else:
            selected_rate = general_rate
            warnings.append(
                "Auto-lowest could not compare rates; General rate applied.")
    else:
        selected_rate = general_rate

    if selected_rate is None:
        return _result(
            req, False, Decimal("0"), [], Decimal("0"), Decimal("0"),
            warnings + ["No General rate available for this line."],
            chunk, None, selected_program,
            explanation="No General rate available.",
        )

    # ------------------------------------------------------------- quota
    if quota_status == "out":
        warnings.append(
            "Over-quota status: the over-quota General line supplied by the "
            "caller was used.")

    # ------------------------------------------------------ §321 de minimis
    if de_minimis and value > DE_MINIMIS_LIMIT:
        warnings.append(
            "De minimis (§321) does not apply: value $%s exceeds the $800 "
            "limit; duty charged normally." % value)
    if de_minimis and value <= DE_MINIMIS_LIMIT:
        if origin_norm in SUSPENDED_ORIGINS:
            warnings.append(
                "De minimis (§321) is suspended for origin %s; duty charged "
                "normally." % (origin or origin_norm))
        else:
            warnings.append(
                "De minimis (§321): value $%s ≤ $800 — duty, MPF and HMF "
                "are $0." % value)
            return _result(
                req, True, Decimal("0"), [], Decimal("0"), Decimal("0"),
                warnings, chunk, selected_rate, selected_program)

    # ---------------------------------------------------------- base duty
    base_duty, computable, explanation, dw = _duty_for_rate(
        selected_rate, value, quantity, quantity_unit, req)
    warnings.extend(dw)
    if not computable:
        return _result(
            req, False, Decimal("0"), [], Decimal("0"), Decimal("0"),
            warnings, chunk, selected_rate, selected_program,
            explanation=explanation)

    # --------------------------------------------------- Chapter 99 stacking
    ch99_duties = []
    in_lieu = False
    for entry in req.get("chapter99_stack") or []:
        hts, rate, stacking = _parse_stack_entry(entry, req)
        if stacking == "in_lieu":
            in_lieu = True
        if rate is None:
            warnings.append(
                "Chapter 99 entry %s has no parseable rate; skipped."
                % (hts or entry))
            continue
        duty, ok, expl, w = _duty_for_rate(rate, value, quantity,
                                           quantity_unit, req)
        warnings.extend(w)
        if not ok:
            warnings.append(
                "Chapter 99 entry %s not computable (%s); skipped."
                % (hts or rate.get("raw"), expl))
            continue
        ch99_duties.append({
            "hts": hts,
            "rate_raw": rate.get("raw"),
            "duty": _round(duty),
            "stacking": stacking or "cumulative",
        })
    if in_lieu:
        warnings.append(
            "In-lieu Chapter 99 rate applies; base duty is NOT added.")
        base_duty = Decimal("0")
        # In-lieu entries replace the base; keep them in ch99_duties.

    # ----------------------------------------------------------------- MPF
    mpf_cfg = req.get("mpf_config") or req.get("mpf_override") or {}
    if not isinstance(mpf_cfg, dict):
        mpf_cfg = {}
    try:
        mpf_rate = _to_decimal(mpf_cfg.get("rate")) or MPF_RATE
        mpf_min = _to_decimal(mpf_cfg.get("min")) or MPF_MIN
        mpf_cap = _to_decimal(mpf_cfg.get("cap")) or MPF_CAP
    except Exception:
        mpf_rate, mpf_min, mpf_cap = MPF_RATE, MPF_MIN, MPF_CAP
    if req.get("mpf_exempt") or (
        selected_program and selected_program in MPF_EXEMPT_PROGRAMS
    ):
        mpf = Decimal("0")
        if selected_program in MPF_EXEMPT_PROGRAMS:
            warnings.append(
                "MPF exempt under claimed program %s." % selected_program)
    else:
        mpf = min(max(value * mpf_rate, mpf_min), mpf_cap)

    # ----------------------------------------------------------------- HMF
    hmf_cfg = req.get("hmf_config") or req.get("hmf_override") or {}
    if not isinstance(hmf_cfg, dict):
        hmf_cfg = {}
    try:
        hmf_rate = _to_decimal(hmf_cfg.get("rate")) or HMF_RATE
    except Exception:
        hmf_rate = HMF_RATE
    if mode == "ocean":
        hmf = value * hmf_rate
    else:
        hmf = Decimal("0")
        warnings.append(
            "HMF applies to ocean shipments only; mode %r — HMF $0."
            % (mode or "unspecified"))

    # ------------------------------------------------------------- rounding
    base_r = _round(base_duty)
    mpf_r = _round(mpf)
    hmf_r = _round(hmf)
    subtotal = base_r + sum(d["duty"] for d in ch99_duties) + mpf_r + hmf_r
    return _result(req, True, base_r, ch99_duties, mpf_r, hmf_r,
                   warnings, chunk, selected_rate, selected_program,
                   subtotal=subtotal)


def _result(req, computable, base_r, ch99_duties, mpf_r, hmf_r, warnings,
            chunk, selected_rate, selected_program, explanation=None,
            subtotal=None):
    if subtotal is None:
        subtotal = base_r + sum(d["duty"] for d in ch99_duties) + mpf_r + hmf_r
    citations = []
    if chunk.get("hts") is not None or chunk.get("page") is not None:
        citations.append({
            "hts": chunk.get("hts"),
            "page": chunk.get("page"),
            "revision": chunk.get("revision") or REVISION,
            "general_raw": chunk.get("general_raw"),
        })
    # Non-computable: never emit $0.00 components — $0 is a real answer
    # (e.g. Free) and must not be confused with "could not compute".
    if not computable:
        base_r = mpf_r = hmf_r = subtotal = None
        ch99_duties = []
    if explanation and explanation not in warnings:
        warnings = list(warnings) + [explanation]
    result = {
        "computable": computable,
        "breakdown": {
            "base_duty": base_r,
            "ch99_duties": ch99_duties,
            "mpf": mpf_r,
            "hmf": hmf_r,
            "subtotal": subtotal,
            "total": subtotal,
        },
        "warnings": list(warnings),
        "citations": citations,
        "provenance": {
            "revision": REVISION,
            "source": "HTS 2026 Rev 21 PDF + CBP fee config (external)",
            "fees_source": FEES_SOURCE,
        },
        "inputs_echo": {
            "value": req.get("value"),
            "quantity": req.get("quantity"),
            "quantity_unit": req.get("quantity_unit"),
            "origin_country": req.get("origin_country") or req.get("origin"),
            "program": req.get("program"),
            "mode": req.get("mode"),
            "quota_status": req.get("quota_status"),
            "chapter99_stack": req.get("chapter99_stack"),
            "de_minimis": req.get("de_minimis"),
        },
        "selected_program": selected_program,
        "rate_used": selected_rate,
    }
    if explanation:
        result["explanation"] = explanation
    return result
