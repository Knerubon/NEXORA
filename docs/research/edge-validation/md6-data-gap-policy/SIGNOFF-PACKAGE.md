# MD-6 Quant sign-off package

Status: **DRAFT / PROPOSAL. NOTHING IN THIS DOCUMENT SET IS AUTHORITATIVE UNTIL SIGNED.**
Quant sign-off: **PENDING**. No approval, review verdict or sign-off has been given, claimed or implied.
The author is an engineer preparing a proposal, same-lineage self-review only; Gate C (independent Quant
reviewer) is required.

## Rule: order of events

Sign-off must be recorded **before** (1) any implementation of this policy in code, (2) acceptance of any real
dataset against it, (3) any strategy evaluation on affected instruments and windows. Any of these done earlier
has no standing under this policy.

## What the Quant reviewer must read

1. [README.md](README.md): scope, status vocabulary, observed facts about cited tooling.
2. [MD6-DECISION-REGISTER.md](MD6-DECISION-REGISTER.md): all 18 decisions.
3. [ACCEPTANCE-CRITERIA.md](ACCEPTANCE-CRITERIA.md): Part A criteria, Part B firewall, threats, reproducibility.
4. [OPEN-QUESTIONS.md](OPEN-QUESTIONS.md).
5. [PREREGISTRATION-TEMPLATE.md](PREREGISTRATION-TEMPLATE.md).
6. [GAP-INTERVAL-PARTITION.md](GAP-INTERVAL-PARTITION.md), [EVALUATION-EXPOSURE-LINEAGE.md](EVALUATION-EXPOSURE-LINEAGE.md),
   [QUARANTINE-IDENTITY.md](QUARANTINE-IDENTITY.md) and their `tests/test_md6_*` executable specifications.
7. Repository authority: `docs/requirements.md`, `docs/architecture.md`, `AGENTS.md` sections 0, 3, 9, 11;
   ADR-012, ADR-014, ADR-019.
8. References only (not authority): PR #93 head `e74ad035e7f21aa07528965da6ebe190c0512793`
   (`packages/nexora/market_history/quality.py`, `clock.py`, `convert.py`, market-history README) and the master
   readiness reports on `origin/claude/edge-readiness-master-20261020-v1`.

## Reviewer checklist

| # | Check | Result |
|---|---|---|
| 1 | No numeric threshold appears as a recommendation anywhere; every unresolved value is literally OPEN | PENDING |
| 2 | Every proposed rule has a rationale and a measurable test | PENDING |
| 3 | Data-quality (Part A) and trading-performance (Part B) decisions are separate; no outcome input to Part A | PENDING |
| 4 | Gap definition, comparison direction, edge semantics acceptable (D01, D06) | PENDING |
| 5 | Parameterization structure acceptable (D02, D17) | PENDING |
| 6 | Missing-vs-quiet evidence hierarchy and gap interval partition acceptable and free of P/L (D03, D04, D05, GAP-INTERVAL-PARTITION.md) | PENDING |
| 7 | UTC / offset / fold treatment fail-closed; +10800 s still UNVERIFIED (D07) | PENDING |
| 8 | Severity classes and dispositions acceptable (D08, D09, D10, D11) | PENDING |
| 9 | Incomplete window and quarantine governance acceptable, separation of duties, content-addressed quarantine and requalification (D12, D13, QUARANTINE-IDENTITY.md) | PENDING |
| 10 | Capability profile broker-agnostic (D14) | PENDING |
| 11 | Preregistration, holdout custody and exposure lineage acceptable (D15, D16, EVALUATION-EXPOSURE-LINEAGE.md) | PENDING |
| 12 | Tooling requirements (D18) correctly separated from this proposal | PENDING |
| 13 | Reviewer attests no holdout or backtest output was used to evaluate this proposal | PENDING |
| 14 | Independence: reviewer is not the author's lineage | PENDING |

## Questions the reviewer must answer

At minimum Q1 to Q15, QE-3, QE-4, QE-7, QE-8, QI-1, QI-4 and QI-5 of [OPEN-QUESTIONS.md](OPEN-QUESTIONS.md), plus: for each decision D01 to D18, accept,
accept with change, reject or defer.

## Record-of-decision template (to be completed by the Quant approver only)

```text
MD-6 DECISION RECORD
Proposal PR / head SHA reviewed (full 40 chars): <PENDING>
Reviewer (name/role), independent of author: <PENDING>
Date (UTC): <PENDING>
Attestation: no holdout/backtest/P&L used to evaluate: <PENDING>

Per decision:
  ID | disposition (accept / accept-with-change / reject / defer) | value or rule (if any) | rationale | evidence refs
  MD6-D01 | PENDING | PENDING | PENDING | PENDING
  ... (one line per D01..D18)

Numeric values set by the Quant (each with rationale and non-outcome evidence reference):
  max_gap_ms / tier ceiling / soft limit / quantile: <PENDING>
  tolerances (soft defects, zero spread, unexplained coverage): <PENDING>
  edge tolerance / minimum coverage / heartbeat tolerance: <PENDING>

Conditions and expiry: <PENDING>
Preregistration id to be created from this record: <PENDING>
Signature / authority reference: <PENDING>
```

## Statement of non-authority

Until a completed record above exists and is committed, this proposal sets no threshold, freezes nothing,
approves nothing and must not be cited as a decision. Sign-off by the Quant does not imply merge authority,
baseline freeze (G3) or any production change; those require Rin under AGENTS.md section 20.
