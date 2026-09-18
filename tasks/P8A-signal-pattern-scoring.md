# P8A — Signal Pattern & Scoring Engine

## Objective

Extend the NEXORA Research Signal Engine with:

- BUY / SELL / WAIT decision
- Entry Zone
- Signal Score (0–100)
- Pattern confirmation
- Pattern conflict detection
- Invalidation level
- TP1 / TP2
- Risk:Reward
- Human-readable reasons
- Machine-readable evidence

This remains RESEARCH / BACKTEST / PAPER only.

DO NOT create broker execution or live-order functionality.

---

## Important Principle

Signal Score is NOT win probability.

Example:

Signal Score = 82/100

MUST NOT be displayed as:

82% chance of winning

Probability / historical win rate may only be displayed after
backtest calibration using sufficient historical samples.

---

# Signal Pipeline

Market Data
    ↓
P&F
    ↓
Adaptive Box
    ↓
Multi-Resolution Matrix
    ↓
Market Structure
    ↓
Support / Resistance
    ↓
Market Regime
    ↓
Pattern Detection
    ↓
Signal Scoring
    ↓
BUY / SELL / WAIT

---

# 1. Signal Decision

Supported actions:

BUY
SELL
WAIT

The engine MUST NOT force BUY or SELL when evidence is weak
or conflicting.

WAIT is a valid and important result.

---

# 2. Signal Components

Initial scoring model:

| Component | Maximum |
|---|---:|
| P&F / Reversal | 20 |
| Market Structure | 25 |
| Support / Resistance | 20 |
| Matrix | 20 |
| Market Regime | 15 |

Base maximum:

100 points

Pattern SHOULD initially behave as confirmation/conflict evidence
rather than blindly adding points beyond 100.

Exact weights MUST be configurable.

Do not hard-code them into core trading logic.

---

# 3. P&F Evidence

Evaluate observable NEXORA P&F state such as:

- current X/O column
- O → X reversal
- X → O reversal
- previous X high break
- previous O low break
- column extension
- failed breakout
- failed breakdown

Do NOT infer or copy proprietary OX BOX10/20/30 semantics.

OX screenshots are research observations only.

---

# 4. Market Structure

Support:

- HH
- HL
- LH
- LL
- structural high
- structural low

Bullish examples:

HL + HH
break above structural high

Bearish examples:

LH + LL
break below structural low

Structure MUST be evaluated using confirmed historical information.

No future data / look-ahead bias.

---

# 5. Support / Resistance Context

A pattern alone MUST NOT generate a strong signal.

Evaluate where it occurs.

Examples:

Bullish reversal near Support
    → positive BUY evidence

Bullish pattern directly below strong Resistance
    → weaker evidence / possible WAIT

Bearish reversal near Resistance
    → positive SELL evidence

Bearish pattern directly above strong Support
    → weaker evidence / possible WAIT

---

# 6. Matrix Confirmation

Use NEXORA Fast / Medium / Slow price-structure states.

Example observations:

X / X / X
    broad bullish alignment

O / O / O
    broad bearish alignment

X / X / O
    short/medium bullish recovery
    while slow structure remains bearish

X / O / O
    possible short-term rebound inside broader bearish structure

These descriptions are NEXORA research interpretations.

DO NOT claim:

Fast = OX BOX10
Medium = OX BOX20
Slow = OX BOX30

unless independently verified in the future.

---

# 7. Market Regime

Supported context:

TREND
RANGE
HIGH_VOLATILITY

Pattern importance MAY depend on regime.

Example:

Trend continuation pattern
    stronger relevance during TREND

Breakout pattern inside RANGE
    requires breakout confirmation

High volatility
    may require stricter confirmation

Do not assume these improve performance until backtested.

---

# 8. Pattern Detection

Initial pattern set:

## Reversal

- Double Top
- Double Bottom
- Head and Shoulders
- Inverse Head and Shoulders

## Continuation

- Flag
- Pennant
- Triangle

## P&F Patterns

- Double Top Breakout
- Double Bottom Breakdown
- bullish reversal
- bearish reversal
- failed breakout
- failed breakdown

Pattern detection MUST be deterministic.

Each detection MUST record:

pattern_type
direction
start_time
confirmation_time
price_range
confidence/evidence metadata
source_data_reference
algorithm_version

Avoid subjective visual-only classification.

---

# 9. Pattern Confirmation

Pattern is supporting evidence.

Example:

Support
+
O → X P&F reversal
+
Double Bottom
+
neckline / structural breakout
+
Fast + Medium bullish alignment

→ stronger BUY setup

Pattern MUST NOT independently force BUY/SELL.

---

# 10. Pattern Conflict

The engine MUST detect conflicting evidence.

Example:

P&F = bullish
Structure = bullish
Pattern = bearish reversal
Location = near Resistance

Result MAY become:

WAIT

or receive a reduced Signal Score.

Every reduction MUST contain a reason.

Example:

"Bearish reversal pattern detected near resistance."

---

# 11. Signal Score

Return:

signal_score: 0..100

Suggested interpretation for research UI:

0–49
WAIT / insufficient evidence

50–64
setup forming

65–79
setup confirmed by primary evidence

80–100
multiple independent evidence groups aligned

IMPORTANT:

These thresholds are initial research configuration.

They MUST be configurable and validated by backtest.

---

# 12. Entry Zone

Prefer an Entry Zone rather than one exact entry price.

Example:

entry_zone:
  low: 4377.5
  high: 4379.5

Entry Zone MUST be derived from deterministic rules.

Possible evidence:

- breakout level
- support/resistance
- structural level
- P&F transition

The algorithm MUST record why the zone was selected.

---

# 13. Invalidation

Every BUY/SELL setup MUST have an invalidation condition.

Example BUY:

price breaks below structural low/support

Example SELL:

price breaks above structural high/resistance

Store:

invalidation_price
invalidation_reason

Do NOT generate arbitrary fixed-distance stops unless the
configuration explicitly requests that strategy.

---

# 14. Targets

Research signal MAY calculate:

TP1
TP2

Possible deterministic sources:

- next structural S/R
- previous swing level
- configured R:R target

Store the calculation method.

---

# 15. Risk:Reward

Calculate expected R:R from:

entry reference
invalidation
target

Example:

Entry = 4380
Invalidation = 4375
TP = 4392

Risk = 5
Reward = 12

R:R = 1:2.4

The engine MUST NOT manipulate targets simply to satisfy a
minimum R:R.

---

# 16. Explainability

Every signal MUST answer:

WHY BUY/SELL/WAIT?

Example:

BUY
Score: 82

Reasons:

+ O→X P&F reversal
+ near structural support
+ previous X high broken
+ Double Bottom confirmed
+ Fast/Medium bullish alignment
- Slow structure remains bearish

Machine-readable evidence MUST accompany human-readable text.

---

# 17. Future Score Movement

Expose conditions that could strengthen, weaken, or cancel
the setup.

Example:

Current:
BUY setup
72/100

If resistance breaks:
potential score increases

If Medium changes O → X:
additional bullish confirmation

If support breaks:
BUY setup cancelled

IMPORTANT:

Do NOT fabricate a future numeric score unless the next state
can be deterministically recalculated.

Prefer:

"additional confirmation"

rather than predicting:

"72 → 88"

unless the scoring engine can actually calculate 88.

---

# 18. Historical Calibration

Future requirement.

Backtest should group similar signal configurations and report:

Signal Score
Historical Win Rate
Sample Size
Expectancy
Profit Factor
Maximum Drawdown

Example:

Signal Score:
78/100

Historical Win Rate:
61%

Sample Size:
1,500

These are separate concepts.

NEVER convert Signal Score directly into win probability.

---

# 19. Backtest Experiments

Compare at minimum:

A:
P&F only

B:
P&F + Structure

C:
P&F + Structure + Matrix

D:
P&F + Structure + Matrix + Regime

E:
P&F + Structure + Matrix + Regime + Pattern

Measure:

- win rate
- expectancy
- profit factor
- maximum drawdown
- trade count
- false-entry proxy
- signal frequency
- entry delay

Pattern logic is accepted only if evidence shows useful
improvement or useful risk reduction.

Do NOT assume more indicators = better system.

---

# 20. Anti-Lookahead Requirements

Pattern detection, Signal Score, Entry Zone, TP and
Invalidation MUST use information available at signal time.

Tests MUST fail if future bars/ticks influence historical signals.

Replay of the same:

dataset
+
configuration
+
engine version

MUST produce identical results.

---

# 21. Suggested API Model

Example only:

SignalDecision {
    action: BUY | SELL | WAIT
    score: int
    entry_zone: optional PriceRange
    invalidation: optional PriceLevel
    targets: list[Target]
    risk_reward: optional float
    patterns: list[PatternEvidence]
    positive_evidence: list[Evidence]
    negative_evidence: list[Evidence]
    config_version: string
    engine_version: string
    source_refs: list[string]
}

Do not change established repository contracts without first
checking the existing architecture and ADRs.

---

# 22. Dashboard

Signal card should eventually show:

XAUUSD

BUY SETUP
Signal Score: 82/100

Entry Zone
4377.5 – 4379.5

TP1
4388

TP2
4398

Invalidation
4371.5

R:R
1:2.4

Evidence:
✓ P&F reversal
✓ Structural support
✓ Breakout
✓ Double Bottom
✓ Fast/Medium alignment
⚠ Slow structure bearish

Do NOT display:

"82% confidence"

or

"82% win probability"

unless historical calibration explicitly supports such a
probability metric.

---

# 23. Testing

Required unit tests:

- BUY evidence
- SELL evidence
- WAIT
- conflicting evidence
- pattern confirmation
- pattern conflict
- pattern near S/R
- Matrix disagreement
- invalidation
- TP calculation
- R:R calculation
- score boundaries 0 and 100
- no future-data access
- deterministic replay
- same dataset/config/version → same result

Add golden fixtures for representative market structures.

---

# 24. Safety Boundary

This phase is:

RESEARCH
BACKTEST
PAPER SIMULATION

It MUST NOT:

- send MT5 orders
- call broker execution endpoints
- create live/demo broker positions
- expose broker credentials
- introduce a hidden execution path

---

# Definition of Done

- Pattern detector implemented and tested
- Pattern conflict supported
- BUY / SELL / WAIT supported
- Signal Score deterministic and configurable
- Entry Zone supported
- Invalidation supported
- TP1 / TP2 supported
- R:R supported
- human-readable reasons generated
- machine-readable evidence stored
- anti-lookahead tests pass
- deterministic replay tests pass
- existing P&F / Matrix / Structure behavior not broken
- no broker execution path introduced
- relevant docs / ADR updated
- lint / type checks / tests pass