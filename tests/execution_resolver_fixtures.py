"""TEST-ONLY helpers for the ADR-035 s5.2 ``ExecutionInstrumentResolver`` port (PR-7).

No production resolver exists or is wired; these pure immutable tables exist only so tests can
inject the SAME resolver instance into the pipeline and the reconciler."""

from __future__ import annotations

from collections.abc import Mapping

from nexora.execution.models import ExecutionInstrumentBinding


class TableResolverForTestsOnly:
    """Exact, case-sensitive ``execution_symbol -> instrument_id`` table; raises on a miss."""

    def __init__(self, table: Mapping[str, str], *, mode: str = "binding") -> None:
        self._table = dict(table)
        self._mode = mode
        self.calls: list[str] = []

    def resolve_execution_instrument(self, execution_symbol: str) -> ExecutionInstrumentBinding:
        self.calls.append(execution_symbol)
        instrument_id = self._table[execution_symbol]  # KeyError == no declaration
        legacy = self._mode == "legacy"
        return ExecutionInstrumentBinding(
            mode="legacy" if legacy else "binding",
            execution_symbol=execution_symbol,
            instrument_id=instrument_id,
            feed_id=None if legacy else "feed-1",
            binding_ref=f"binding:{execution_symbol}",
        )


def identity_resolver(*symbols: str) -> TableResolverForTestsOnly:
    return TableResolverForTestsOnly({symbol: symbol for symbol in symbols})
