from datetime import UTC, datetime, timedelta
from decimal import Decimal
from nexora.market_regime import RegimeSnapshot, RegimeState
from nexora.matrix import MatrixResolutionState, MatrixSnapshot
from nexora.pnf import PnfTransition
from nexora.signals import SignalConfig, SignalEngine, SignalWeights
from nexora.structure import CandidateLevel, ConfirmedPivot, StructureSnapshot

NOW = datetime(2026, 2, 3, 9, 0, tzinfo=UTC)

def cfg():
    return SignalConfig(symbol='XAUUSD', cooldown_events=0, expiry_events=12, version='p8a-test-v1', weights=SignalWeights(pnf_reversal=20, structure=25, support_resistance=20, matrix=20, regime=15, pattern_confirmation=10, pattern_conflict_penalty=15), wait_score_max=49, action_score_min=65, action_gap_min=8, pattern_price_tolerance=Decimal('0.8'), entry_zone_half_width=Decimal('0.5'), target_rr_tp1=Decimal('1.5'), target_rr_tp2=Decimal('2.5'))

def tr(direction='X', kind='reversal', price='100.2'):
    return PnfTransition(type=kind, reason='reversed' if kind == 'reversal' else 'extended', symbol='XAUUSD', column_id=1, direction=direction, from_price=Decimal(price)-Decimal('0.5'), to_price=Decimal(price), boxes_moved=1, event_time=NOW+timedelta(minutes=2), source_event_id=f'evt-{direction}-{price}', identity_key=f'transition-{direction}-{price}', config_version='p8a-test-v1', effective_box_size=Decimal('1.0'), sizing_rule_version='p8a-box-v1')

def mat(alignment='aligned_bullish', direction='X', price='100.2'):
    t = tr(direction=direction, kind='reversal', price=price)
    return MatrixSnapshot(schema_version=1, symbol='XAUUSD', sequence=1, watermark_sequence=1, generated_at=NOW, alignment=alignment, strength=3, resolutions=(MatrixResolutionState(name='fast', symbol='XAUUSD', direction=direction, latest_transition=t, status='ready'), MatrixResolutionState(name='medium', symbol='XAUUSD', direction=direction, latest_transition=t, status='ready'), MatrixResolutionState(name='slow', symbol='XAUUSD', direction=direction, latest_transition=t, status='ready')))

def reg(label='trend'):
    return RegimeSnapshot(schema_version=1, symbol='XAUUSD', sequence=1, state=RegimeState(label=label, reason='unit_test', effective_time=NOW, source_ref='regime:test', config_version='p7-regime-v1'))

def st(pivots, levels=(('support','99.0','confirmed'),)):
    return StructureSnapshot(schema_version=1, symbol='XAUUSD', sequence=len(pivots), pivots=tuple(ConfirmedPivot(kind=kind, price=Decimal(price), occurrence_time=NOW + timedelta(minutes=i*2), confirmation_time=NOW + timedelta(minutes=i*2+1), source_transition_id=f'pivot-{i}', config_version='p3-fixed-v1') for i, (kind, price, _status) in enumerate(pivots,1)), levels=tuple(CandidateLevel(side=side, price=Decimal(price), status=status, source_pivot_id=f'level-{i}', updated_at=NOW + timedelta(minutes=i)) for i, (side, price, status) in enumerate(levels,1)))

structure = st((('low','100.0','confirmed'),('high','104.0','confirmed'),('low','100.1','confirmed')), (('support','99.8','confirmed'),('resistance','104.2','confirmed')))
assessment = SignalEngine(cfg())._assess_components(structure=structure, regime=reg('range'), matrix=mat('mixed', direction='X', price='103.9'))
print('buy', assessment.buy_points, 'sell', assessment.sell_points)
print('positive', assessment.positive_evidence)
print('negative', assessment.negative_evidence)
print('patterns', assessment.patterns)
print('decision', SignalEngine(cfg()).evaluate(structure=structure, regime=reg('range'), matrix=mat('mixed', direction='X', price='103.9')))
