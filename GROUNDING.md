# Grounding notes — HTS 2026 Rev 21 (Pub. 5802)

Facts the implementation relies on, and the traps that produced the
prior audit's bugs. Where a fact needs an exact PDF page, the ingest
builder must confirm it against the PDF — **no page number is asserted
here unless it comes from `CONTRACTS.md` or the task spec**. Do not
invent page numbers to fill gaps.

## Revision / provenance

- Revision: **2026 Rev 21**; publication: **Pub. 5802**; preface date
  **2026-10-09**. The preface date is **NOT** an effective date for
  all lines — do not present it as one.
- Source PDF file: `2026HTSRev21_0_cgh9.pdf`.
- **PDF pagination = PDF page numbers, 1 = cover.** Citations use the
  PDF page number (`(HTS, p.N, Rev 21)`), not any printed page number.
- Every chunk and every response carries revision/publication (see
  `CONTRACTS.md` chunk dict). `meta.json` records the PDF SHA-256 so
  the served index is traceable to the exact PDF build.

## How to read a tariff line (the column trap)

A tariff line has three rate columns:

| Column | Meaning |
| --- | --- |
| **Column 1 General** | Normal Trade Relations (NTR) rate — the default rate |
| **Column 1 Special** | Preferential program rates, e.g. `Free (A)`, `See 9902…`, `5% (AU)` |
| **Column 2** | Cuba / North Korea / designated-country rate — almost never the rate for an ordinary import |

**Column trap:** Column 2 must never be used as the default rate.
Special-program rates apply only when the program is claimed *and*
the program is currently in force for that origin/good. A `Free`
in Special with a program symbol does **not** mean duty-free for
everyone — it means duty-free *under that program, if eligible*.

## Rate examples / shapes the parser must handle

Rates appear in these shapes (see `rag/rates.py` contract —
`parse_rate` returns Decimal strings, never floats):

- `Free` → kind `free`.
- Ad valorem: `5%`, `2.5%` → kind `ad_valorem`, value as Decimal
  percent. **Percent means per hundred**: 5% of $1,000 = $50, not
  $5,000 (the old 100x bug — see `AUDIT.md`).
- Specific: `3.5¢/kg`, `$1.10/doz` → kind `specific`, requires a
  quantity in the stated unit; without quantity it is uncomputable.
- Compound: `5% + 10¢/kg` → kind `compound`.
- Cross-reference: `See 9902.85.01` → kind `cross_ref`; follow the
  target, do not guess a number.
- Empty cell → kind `empty`; an empty rate is **not** zero, not Free,
  and not a match (the empty-HTS bug — see `AUDIT.md`).

## MPF / HMF are external (not in the PDF)

- **MPF** (Merchandise Processing Fee): **0.3464%** of value, **min
  $32.71**, **cap $575** (CBP FY2025 figures, external). Applies to
  formal entries; de minimis / informal entries are out of scope for
  MPF in this calculator unless the caller says otherwise.
- **HMF** (Harbor Maintenance Fee): **0.125%**, **ocean-only**.
  Never apply HMF to air, land, or mail shipments.
- Both live in `rag/fees_config.py` / env overrides
  (`MPF_RATE`, `MPF_MIN`, `MPF_CAP`, `HMF_RATE`). They must be labelled
  "external — not in the HTS PDF" in every response that uses them.
  The old code treated them as if they came from the PDF; that is
  fixed (see `AUDIT.md`).

## Chapter 99 — Section 232 / Section 301

- Chapter 99 notes carry **additional** duties on top of the Chapter
  1–97 rate, and their stacking rule is per-note: `cumulative`,
  `in_lieu`, `standalone`, or `unknown` (chunk field `stacking`).
  - `cumulative`: add on top of the base duty.
  - `in_lieu`: replaces the base duty for the covered goods — do not
    add both.
  - `unknown`: say so; do not assume cumulative.
- **Section 301** (China-origin additional duties) lives in Chapter 99
  notes, e.g. the `9903.88.01` line used in the demo for a China-origin
  electric motor (`8501.51.20 + 9903.88.01`). The caller supplies
  `chapter99_stack`; the calculator must look each stacked line up in
  the index and apply its own stacking rule.
- **Section 232** (steel / aluminum and derivatives) likewise lives in
  Chapter 99. Do not hard-code Section 232/301 rates in Module C —
  they come from the indexed Chapter 99 chunks, and Chapter 99 lines
  carry expiry/effective-period fields (`effective_period`) that must
  be surfaced. A Chapter 99 line whose period has expired must be
  flagged, not silently applied.
- Chapter 99 lines are `kind: chapter99` chunks; they are not
  dutiable base lines (`dutiable: false` unless they are themselves
  an 8-digit tariff line).

## Tariff-rate quotas (TRQ)

- Some goods (e.g. peanuts) have **different HTS lines for in-quota
  vs out-of-quota** quantities — demo pair `1202.41.05` (in-quota) vs
  `1202.30.80` (out-of-quota). The `quota_status` input (`in | out |
  unknown`) selects which line applies; `unknown` must say the result
  depends on quota status, not silently pick one.
- The PDF gives the in/out rates and notes. Quota **fill levels** and
  whether quota remains are external administration data — not in the
  PDF, and never to be invented.

## GSP — lapsed

- **GSP (Generalized System of Preferences) is lapsed.** A Special
  rate marked `Free (A)` (program symbol `A` = GSP) is **not**
  currently claimable as a live benefit. The answer module must flag
  this ("GSP-lapsed") whenever a `Free (A)` / `(A)` rate is surfaced,
  and must never present it as a claimable saving without the flag.
- Demo query (e): *"Is Free (A) claimable?"* — the correct grounded
  answer is: no, not while GSP is lapsed; the General rate applies
  unless another in-force program covers the good/origin.

## General Notes (GN) facts

- **GN 12 is deleted.** Any chunk or citation referencing GN 12 as
  live law is stale. The answer module must flag GN12-deleted when
  GN 12 content is retrieved; ingest must not present GN 12 as
  current authority.
- **GN 19–24 were transferred.** Content formerly in GN 19–24 now
  lives elsewhere in the notes structure (per the task spec). Do not
  cite "GN 19"–"GN 24" as live locations; retrieval must resolve the
  transfer and cite the current location. Ingest must confirm the
  exact destination notes against the PDF.
- General Notes, Chapter Notes, Section Notes, and Statistical Notes
  are `kind: general_note | chapter_note | section_note |
  statistical_note` chunks. They govern classification (GRI) and
  program eligibility; a rate without its governing note is
  incomplete, so retrieval should surface the note alongside the line.

## GRI / classification

- Classification follows the General Rules of Interpretation (GRI),
  surfaced as `kind: gri` chunks. The calculator estimates duty for a
  *given* HTS line; it does not bind a classification. Every response
  carries the generic disclaimer: the estimate is not a binding
  classification.
- Heading rows (4-digit) and suffix/indent rows are **not dutiable**
  (`dutiable: false`); only 8-digit lines (or 6-digit when no 8-digit
  line exists) are dutiable. Returning a heading row as a "match"
  without a warning is the heading-row bug (see `AUDIT.md`).

## What to verify in-PDF (ingest builder's checklist)

- [ ] Exact PDF pages for GN 12 deletion notice and GN 19–24 transfer.
- [ ] Exact PDF pages for the Chapter 99 Section 232 / 301 notes and
      their effective/expiry periods.
- [ ] Peanuts TRQ note text for the 1202.41.05 / 1202.30.80 pair.
- [ ] Any `See 9902…` cross-reference targets used by the demo lines.
- [ ] That no chunk with an empty HTS is returned as a tariff-line match.
