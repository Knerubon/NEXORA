# EDGE #77 — adversarial review of PR #88 / #89 and Dukascopy pilot (D1), 2026-10-20

## Independence statement (read first)
The reviewed code (#88, #89) and the D1 adapter (#90) were written by the same Claude lineage
that produced this review. This is a **self-audit by an adversarial pass, not an independent
review**. It issues **no APPROVE** at any head. Per AGENTS.md section 12.7 every item remains
"self-review; independent review pending". I did not modify the #88 or #89 branches.

## Reviewed heads and CI
| PR | Exact head | CI on that head |
|---|---|---|
| #88 | 47f9ea834985748522fdef6c59e013ccef2e10cf | python success, web success |
| #89 | 069894ef43df6baa5f614de678785a7043ed16cd | python success, web success |
| #90 (D1, new) | 858777a6b461713ce098a066b0adad5cb10df80d | pending at report time |

## R1 — PR #88 (D-1 / D-2)
Evidence: original-defect repro on #79 head 196a962 reproduced both defects (`'typo'`, `None`, `0`
exit 105/target; run_id identical for net 4.87654 vs 4.876543211). Same repro on 47f9ea8: invalid
values raise `ExitInputError invalid_ambiguity`; identical run_id **and** identical net under precision 6/28.
Re-run at 47f9ea8: edge tests 381 passed; ruff and mypy clean.

Probes (script kept in the session scratchpad, results below):
- D-1: 15 invalid ambiguity inputs (trailing space, case, zero-width char, hyphen, empty, None, 0, 1,
  True, bytes, str subclass, tuple, list, NaN, object) all rejected; `object.__setattr__` bypass
  rejected at `simulate_exit`; NaN/Inf/sNaN/negative plan prices rejected with `invalid_plan_price`.
- D-2: threads with ambient precision 2/9/50 give identical gross; no undecorated public function
  performs Decimal arithmetic (comparisons only elsewhere).

| ID | Sev | Finding | Reproduction |
|---|---|---|---|
| R1-1 | MINOR | Same validation class as D-1 not applied to other policy fields: `max_hold_bars=True` and `2.5` are accepted and hashed; `max_hold_bars="3"` / `entry_delay=5` raise `TypeError`, not `ExitInputError` | `ExitPolicy(..., max_hold_bars=2.5)` accepted |
| R1-2 | MINOR | `arithmetic_descriptor()` reads the mutable global `EDGE_CONTEXT`, not the active context: mutating it inside a call yields active prec 28 but descriptor prec 5 | probe P4 |
| R1-3 | MINOR | Descriptor omits `clamp` and `capitals`: contexts differing there share a descriptor | probe P5 |
| R1-4 | MINOR | 28-digit rounding is silent (Inexact/Rounded not trapped or recorded): `unit_size` of 25 digits is rounded without any flag. Deterministic, but a loss of precision not visible in the run. Belongs to Q-T12 | probe P7 |
| R1-5 | MINOR | No structural guard forces future public Decimal-arithmetic functions to be decorated; coverage rests on tests that list known functions | review |
| R1-6 | INFO | `target_first` remains a valid, favorable-resolution policy; it is explicit, and ambiguity is counted with sensitivity values. Whether it is an approved semantic is Q-T08 | — |

No CRITICAL or MAJOR finding. Not an APPROVE (see independence statement). Merge blockers for #88 are
unchanged: independent review at this exact head, and #79's own gates.

## R2 — PR #89
Probes and results:
- Allowlist: trade calls (`order_send`, `order_check`, `positions_*`, `orders_*`, `login`, history
  deals) and `_module` raise; no instance attribute returns the module; `order_sent` count 0.
  Terminal `path` and account fields are not captured; no credentials accepted by the CLI/source.
- Offset coverage: `TimeConversion.covers` vs brute force over 3000 random segment sets: 0 mismatches.
- Crossed, non-positive, duplicate, same-timestamp-different-quote, out-of-order, zero-spread and gap
  counts verified by tests; holdout custody: report holds counts/coverage only.
- Retries, window clipping, write-once chunks, tamper detection (edit, missing, extra, manifest edit): covered.

| ID | Sev | Finding | Reproduction |
|---|---|---|---|
| **R2-1** | **MAJOR** | `decimal_str` calls `Decimal.normalize()`, which rounds to the AMBIENT precision. Under `prec=4` a bid of 2345.67 is persisted as `2346`; `dataset_id` differs from the prec-28 run; `verify_dataset` reports clean because the hash is of the corrupted bytes. Same defect class as D-2, in the primary evidence artifact. `normalize()` in `convert.py` also subtracts under the ambient context (latent: not shown to differ at prec 3) | `extract` under `localcontext(prec=4)` |
| **R2-2** | **MAJOR (scale)** | `extract` keeps every tick in `all_ticks` and `verify_dataset` does the same: about 344 bytes/tick measured, so about 3.4 GB per 10 million ticks, before the quality pass. Real multi-week tick history will not fit | tracemalloc, 200k ticks |
| R2-3 | MINOR | Allowlist includes `initialize` (accepts login/password if a caller passes them; this code never does) and `symbol_select` (changes Market Watch, unused). Least privilege says remove both or wrap them. In-process bypass via the private registry is inherent to Python (no sandbox) | probe Q2 |
| R2-4 | MINOR | Writes are not atomic: a crash leaves a truncated chunk, and the restart then refuses with `chunk_exists_with_different_content` until an operator deletes it. A crash during the manifest write blocks further runs the same way | probe Q6 |
| R2-5 | MINOR | A dataset with hard defects (non-monotonic, crossed, non-positive) still gets a normal manifest and `verify_dataset` passes; no predicate enforces "usable" | probe Q7 |
| R2-6 | MINOR | One NaN/Inf quote aborts the whole extraction with `non_finite_decimal`; the defect is not recorded | probe Q3 |
| R2-7 | MINOR | A forged but self-consistent manifest (edit chunk, recompute hashes) verifies clean. `manifest_sha256` needs an external anchor (PR, registry, signature) | probe Q8 |
| R2-8 | MINOR (design) | Offset segments are keyed by RAW time. A DST fall-back repeats raw instants, so the mapping is ambiguous there; it surfaces only as `non_monotonic_count`. Per-segment effective dates in UTC are not representable | analysis |
| R2-9 | MINOR | Extractor is not holdout-aware: it will read and hash a window that a future registry marks as locked | analysis |
| R2-10 | MINOR | Chunk and manifest files are not made read-only | analysis |
| R2-11 | NOT TESTED | Live `copy_ticks_range` window semantics, real-terminal fields (WINDOWS-INTEGRATION) | — |

PR #89 is **not approvable at this head** (R2-1, R2-2 open) and has no independent review. A fix for
R2-1 (e.g. string formatting without `normalize()`, or exact tuple-built Decimals as in #90) and a
streaming rewrite for R2-2 are recommended; I did not push them because the instruction was to review.

## D1 — Dukascopy XAUUSD pilot
- Selection: 2026-09-07..2026-09-13 (7 consecutive days), fixed in `PILOT-PLAN.md` at commit d78c04d
  **before** any code or download; nothing was downloaded.
- Access/licence: see `FINDINGS.md`. Official page confirms a free CSV export tool; the bulk
  `datafeed` `.bi5` endpoint has no official spec found; Terms of Use s.3 forbids bots/scrapers/data
  mining without written consent and database construction. Cloud proxy returns 403 for the host.
  **Status: BLOCKED.** No substitute data was created.
- Adapter (PR #90): offline, preserves original bytes, requires retrieval log and owner-declared
  semantics, treats absent/empty hours as coverage states, independent `verify`, tests on synthetic bytes.
- Pilot readiness: tooling READY on synthetic bytes; pilot itself BLOCKED; layout/time base UNVERIFIED.

## Gates
- **G1 Engine Correctness: NO-GO.** No blocking defect found in #88 by this pass, but there is no
  independent review of the exact head; #79 unreviewed.
- **G2 Data Credibility: NO-GO.** No real dataset; offset semantics unverified; Dukascopy licence
  and acquisition BLOCKED; #89 has MAJOR R2-1/R2-2.

## Confirmations
NO MERGE · NO FREEZE · NO PROD · NO LIVE/DEMO/PAPER ORDER · NO AUTO · NO HOLDOUT ACCESS · NO
STRATEGY SIGNALS/PNL/OPTIMISATION · NO FABRICATED OR SUBSTITUTED DATA · NO SELF-APPROVAL.
