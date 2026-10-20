"""Broker-agnostic, read-only historical tick extraction and verification.

EDGE-VALIDATION-1 (Agent B).

Nothing here sends, modifies or closes orders, stores credentials, or assumes a broker. Raw
broker timestamps are always kept; UTC is derived only through an explicit, operator-declared
`TimeConversion` and is never auto-detected or applied globally.
"""

SCHEMA_VERSION = "nexora.market-history/1"
