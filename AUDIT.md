# Audit — prior bugs → fixes in this rebuild

Prior-audit findings (as briefed for this rebuild) and where each is
fixed in Module C. "Prior code" = the pre-rebuild implementation that
used a spreadsheet as ground truth and an unconstrained LLM for rates.

| # | Prior bug | Symptom / impact | Fix in this rebuild | Test |
| --- | --- | --- | --- | --- |
| 1 | **100x percent bug** | A 5% rate was applied as 500% (percent treated as a multiplier), inflating duty 100x | Rates are Decimal strings, never floats; percent means per-hundred in `rag/calculator`; `rag/rates.parse_rate` never returns float | `tests/test_api.py` (calculator contract via pipeline); `GROUNDING.md` rate shapes |
| 2 | **MPF minimum ignored** | MPF charged below the $32.71 floor on low-value entries | Calculator applies MPF min; config in `backend/config.py` (`MPF_MIN`) + `rag/fees_config.py` | Pipeline passes fee config; fee defaults asserted in config tests |
| 3 | **MPF cap ignored** | MPF charged above the $575 cap on high-value entries | Calculator applies MPF cap (`MPF_CAP`) | Same as above |
| 4 | **HMF applied to non-ocean modes** | 0.125% HMF added to air/land/mail shipments | HMF is ocean-only: `mode` validated in `backend/validators.py`; calculator applies HMF only when `mode == "ocean"` | Extended calculate test (`mode: ocean` + fee path) |
| 5 | **MPF/HMF presented as PDF data** | Fees cited as if from the HTS | Fees labelled external everywhere: disclaimer, provenance, `GROUNDING.md`; config lives outside the PDF | `health` / calculate responses carry external labelling; `GROUNDING.md` |
| 6 | **LLM invented a 150% rate** | An unconstrained LLM produced a plausible-looking but false rate with no citation | LLM (if any) never sets rates; rates come only from indexed PDF chunks; `rag/answer` abstains with no retrieval; every rate cited `(HTS, p.N, Rev 21)` | `test_rag_query_abstains_on_nonsense` |
| 7 | **Empty-HTS rows returned as matches** | Rows with no HTS code matched queries and produced nonsense duties | Retrieval never returns empty-HTS chunks (CONTRACTS); pipeline match requires a real chunk; `build_index --validate` rejects malformed chunks | `test_build_index_refuses_non_pdf`; validator tests |
| 8 | **Heading rows treated as dutiable** | 4-digit headings returned as if they were duty lines | `dutiable` is true only for 8-digit (or 6-digit when no 8-digit) lines; heading matches carry a warning | Pipeline `_find_match` surfaces retrieval warnings |
| 9 | **TRQ in/out conflated** | Peanuts (and similar) quoted at one rate regardless of quota status | `quota_status: in \| out \| unknown`; `unknown` says the result depends on quota status; demo runs both legs | Demo queries (c1)/(c2)/(c3) |
| 10 | **Chapter 99 stacking wrong** | §301/§232 lines double-counted, or `in_lieu` lines added on top of base duty | Chunk `stacking` field (`cumulative \| in_lieu \| standalone \| unknown`); calculator applies the per-note rule; `unknown` is surfaced, not assumed | Demo query (d): 8501.51.20 + 9903.88.01 |
| 11 | **GSP `Free (A)` presented as claimable** | Users told they could claim GSP savings while GSP is lapsed | Answer flags GSP-lapsed whenever `(A)` rates surface; never presents `Free (A)` as claimable without the flag | Demo query (e); `GROUNDING.md` GSP section |
| 12 | **GN 12 cited as live** | Deleted General Note 12 cited as authority | Answer flags GN12-deleted; ingest must not present GN 12 as current | `GROUNDING.md` GN facts |
| 13 | **GN 19–24 cited at old locations** | Transferred notes cited where they no longer live | Retrieval resolves the transfer and cites the current location | `GROUNDING.md` GN facts |
| 14 | **Float rounding** | Binary-float rounding drifted totals by cents | Each component rounded HALF_UP to cents; subtotal = sum of rounded components; Decimal throughout | Calculator contract (CONTRACTS.md) |
| 15 | **NaN / bool accepted as value** | `NaN`, `Infinity`, `true` passed validation and crashed or mis-computed | Validators reject bool/NaN/inf explicitly; `value` must be a finite Decimal in 0.01–100M | `test_calculate_rejects_bool_nan`, `test_validators_nan_bool_rejected` |
| 16 | **Exception detail leaked** | Tracebacks / file paths / payloads in error responses | Generic error bodies (`code` + `message` only) in Flask handlers and Netlify adapters; pipeline never leaks internals | `test_404_and_405_no_leak`, `_assert_no_email_no_leak`, Netlify adapter tests |
| 17 | **Personal email in responses** | A personal address appeared in API output | No email addresses anywhere in responses; every test asserts the no-email invariant | `_assert_no_email_no_leak` on every API test |
| 18 | **Spreadsheet treated as ground truth** | Stale/incorrect spreadsheet overrode the PDF | Spreadsheet disregarded; PDF-only ingest (`scripts/build_index.py` refuses non-PDF input; never touches spreadsheet sources) | `test_build_index_refuses_non_pdf`; `README.md` grounding guarantees |
| 19 | **Confidence outside [0, 1]** | NaN / >1 confidence scores surfaced to clients | `clamp_confidence` coerces any input to [0, 1], never raises | `test_confidence_clamp` |
| 20 | **Punctuation in descriptions rejected** | `Men's`, `;`, `%`, `+`, quotes caused 400s | Validators explicitly allow apostrophes, semicolons, commas, quotes, `+`, `%`; only control characters rejected | `test_calculate_apostrophe_semicolon_allowed` |

## Still to verify (not Module C's to assert)

- Exact PDF pages for GN 12 / GN 19–24 / Chapter 99 notes — ingest
  builder's checklist in `GROUNDING.md`.
- Calculator-side unit tests for MPF min/cap, HMF ocean-only, TRQ,
  and stacking arithmetic live with the calculator builder; Module C
  tests cover the API/validation surface and the degraded (module
  missing) path.


---

## Integration fixes found by running the demo against the real index (2026-10-17)

These were NOT visible in unit tests with synthetic fixtures; each was found by
executing the built index end-to-end and fixed before delivery:

| # | Symptom in demo | Root cause | Fix | Regression test |
|---|---|---|---|---|
| 1 | `parse_rate("2.5%")` → `uncomputable` (reported by backend builder) | `_PERCENT_RE` used `\b` after `%` | Anchor without `\b` | `test_rates.py` |
| 2 | Ch.99 row 9903.88.01 parsed alone → `tariff_line` / `standalone` | Chapter came from page context, absent on isolated pages | Chapter inferred from HTS prefix (`99xx` → Ch.99) | `test_parser.py::test_ch99_row_classified_by_hts_prefix_when_parsed_alone` |
| 3 | `build_index --sample` wrote 5 chunks (cover/TOC) | Sample = first N pages | Sample = parser's curated set (GRI + GN3 + Ch61 + Ch85 + Ch99 + appendix) | manual: 58 chunks |
| 4 | Health reported 58 chunks with full index loaded | Shared `meta.json` overwritten by later `--sample` build | Per-output `index.meta.json`; health counts the actual index file | `test_api.py::test_health_shape` |
| 5 | Ch.99 HTS echoed as `99038801` | Validator stripped dots from HTS codes | Canonical dotted form (`9903.88.01`) preserved | `test_api.py::test_calculate_extended` |
| 6 | Peanuts in-quota returned $0.00 breakdown, no warning | Non-computable path emitted zeros | Non-computable breakdown is `None` + explanation in warnings | `test_calculator.py::test_noncomputable_breakdown_is_none_not_zero` |
| 7 | Peanuts in-quota: 100 kg × 9.35¢/kg → **$935** | Cent components treated as dollars; quantity compared to currency unit, not per-unit | ¢ → $ ÷ 100; compare quantity unit to `per` (`kg`), with per-unit normalization | `test_calculator.py::test_specific_cent_converted_to_dollars` |
| 8 | All GN pages 37–669 labelled `gn:11` (Singapore/Chile/… content) | GN headings are titled lines, not "General Note N"; sticky counter stuck at 11 | Titled-heading detection (≤36, no rule markers) + running-head → GN map from the TOC (GN 25 Singapore, 26 Chile, 27 Morocco, 28 Australia, 29 DR-CAFTA, 30 Bahrain, 31 Oman, 32 Peru, 34 Colombia, 35 Panama, 36 Japan) | `test_parser.py::test_gn_numbering_advances_past_3` |
| 9 | GSP question never flagged GSP-lapsed | BM25 drowns note chunks; note scan + flag match failed on `Generalized\nSystem` line wrap | Direct index scan for note text + whitespace-normalized matching in scan and flag detector | `test_answer.py::test_gsp_question_flags_lapsed_even_without_gsp_lines`, `test_gsp_flag_survives_newline_wrapped_phrase` |

## Known limitations (unverified / not fixed)

- **GN labels**: a handful of stray chunks remain (e.g. 7 chunks at pp.903–911,
  Schedule B appendix pages, labelled `gn:6`; 1 chunk at p.178 labelled `gn:7`).
  Page numbers and text are correct; only the `gn:` id prefix is off on these.
- **60 Ch.99 quota-period rows** (e.g. `9903.17.01`) genuinely have no rate cell —
  their duty treatment lives in the notes; `general_raw` is `None`.
- **Parser description joins**: dotted leaders / `(con.)` continuations are joined
  best-effort; a few descriptions read tersely (e.g. `4202.21.30` "Of reptile
  leather Other:"). Rates, units, pages are unaffected.
- **MPF/HMF values** (0.3464%, min $32.71, cap $575; HMF 0.125%) are CBP FY2025
  figures, external to the HTS PDF. They change by fiscal year — refresh
  `rag/fees_config.py` (or pass overrides) when CBP publishes new figures.
- **China appendix** (pp.4290–4533) is reference-only per its own disclaimer; it is
  chunked as `appendix` / `reference_only=True` and never used as a rate source.
- **§321 de minimis**: suspension for some origins (e.g. China) is modelled as a
  fixed list in the calculator; check current CBP guidance before relying on it.
- **9903.88.01**: the operative table row is at PDF p.3989 ("duty provided in the
  applicable subheading plus 25%"); the U.S. Note 20 text is at p.3563. Do not
  conflate the two pages.
