"""Broker-agnostic, read-only historical tick extraction and verification.

EDGE-VALIDATION-1 (Agent B).

Nothing here sends, modifies or closes orders, stores credentials, or assumes a broker. Raw
broker timestamps are always kept; UTC is derived only through an explicit, operator-declared
`TimeConversion` and is never auto-detected or applied globally.
"""

SCHEMA_VERSION = "nexora.market-history/4"
LEGACY_SCHEMA_V3 = "nexora.market-history/3"
# V3 datasets still verify (integrity), but can never be TRUSTED: they carry no filesystem
# capability record and no operator identity (see docs/research/market-history/THREAT-MODEL-V4.md).
SUPPORTED_SCHEMAS = (LEGACY_SCHEMA_V3, SCHEMA_VERSION)
