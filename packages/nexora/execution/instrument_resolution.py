"""Single fail-closed use of the ADR-035 s5.2 ``ExecutionInstrumentResolver`` port (PR-7).

The PR-1 ``ExecutionInstrumentResolver`` Protocol (``models.py``) is the ONE port for both
the pipeline and the reconciler; both go through ``resolve_binding_instrument_id`` so the
semantics cannot diverge. Pure: no I/O, no clock, no broker/adapter, no implementation of
the port (none is wired in production; OPEN-9).

Rules (ADR-035 s5.1/s5.2, INV-19/20):

* binding mode ONLY. A ``legacy`` binding is denied (OPEN-9 is undecided);
* the returned binding must be an exact ``ExecutionInstrumentBinding`` for EXACTLY the
  requested ``execution_symbol`` (case-sensitive, no trim/prefix/similarity);
* any failure of any kind (no resolver, wrong type, raise, wrong/mismatched binding,
  blank or non-str symbol/id) is ``execution_binding_missing``: fail closed, and the
  exception text (which could carry a payload) is never propagated;
* nothing here infers ownership from symbol/side/quantity and nothing here knows a broker
  symbol (the adapter owns ``instrument_id <-> broker_symbol``).
"""

from __future__ import annotations

from nexora.execution.models import ExecutionInstrumentBinding

REASON_EXECUTION_BINDING_MISSING = "execution_binding_missing"


class ExecutionBindingMissingError(LookupError):
    """No acceptable binding for the symbol. Carries only a fixed reason code."""

    def __init__(self) -> None:
        self.code = REASON_EXECUTION_BINDING_MISSING
        super().__init__(REASON_EXECUTION_BINDING_MISSING)


def resolve_binding_instrument_id(resolver: object, execution_symbol: object) -> str:
    """``execution_symbol -> instrument_id`` exactly once, or raise
    ``ExecutionBindingMissingError``. Never returns an unvalidated value."""

    try:
        if type(execution_symbol) is not str or not execution_symbol.strip():
            raise ExecutionBindingMissingError
        resolve = getattr(resolver, "resolve_execution_instrument", None)
        if resolve is None or not callable(resolve):
            raise ExecutionBindingMissingError
        binding = resolve(execution_symbol)
        if (
            type(binding) is not ExecutionInstrumentBinding
            or binding.mode != "binding"
            or type(binding.execution_symbol) is not str
            or binding.execution_symbol != execution_symbol
            or type(binding.instrument_id) is not str
            or not binding.instrument_id.strip()
        ):
            raise ExecutionBindingMissingError
        return binding.instrument_id
    except ExecutionBindingMissingError:
        raise
    except Exception:
        raise ExecutionBindingMissingError from None
