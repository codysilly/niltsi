"""Global-per-ledger FAA limits, reserved before network I/O; no secrets stored.

New/missing history starts a 24h bulk / 3min delta quarantine. All callers sharing
an FAA account must use one durable ledger. CI acquisition stays disabled until
account-wide coordination is arranged. Never reset history to bypass cooldowns.
"""
import json
import math
import os
from pathlib import Path
import time
from nms_adapter import FeedError

DEFAULT_STATE = Path(__file__).resolve().parents[1] / '.nms-state/pull-budget.json'
INTERVALS = {'bulk': 24*60*60, 'delta': 3*60}


class PullBudget:
    def __init__(self, path=DEFAULT_STATE, clock=time.time):
        self.path, self.clock = Path(path), clock

    def _operation(self, mode, reserve):
        if mode not in INTERVALS:
            raise FeedError('PULL_MODE_REQUIRED')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix('.lock')
        try:
            lock.mkdir()
        except FileExistsError:
            raise FeedError('PULL_STATE_LOCKED') from None
        try:
            current = self.clock()
            if not math.isfinite(current) or current < 0:
                raise FeedError('PULL_CLOCK_INVALID')
            if not self.path.exists():
                self._write({'schema_version':1, 'updated_epoch':current,
                             'bulk_not_before':current+INTERVALS['bulk'],
                             'delta_not_before':current+INTERVALS['delta']})
                raise FeedError('PULL_HISTORY_UNVERIFIED')
            try:
                state = json.loads(self.path.read_text(encoding='utf-8'))
                if not isinstance(state,dict) or state.get('schema_version') != 1 or any(type(state.get(key)) not in (int,float) or not math.isfinite(state[key]) or state[key] < 0 for key in ('updated_epoch','bulk_not_before','delta_not_before')):
                    raise ValueError()
            except (ValueError, OSError, TypeError):
                raise FeedError('PULL_STATE_INVALID') from None
            if current < state['updated_epoch']:
                raise FeedError('PULL_CLOCK_INVALID')
            if current < state[mode+'_not_before']:
                raise FeedError('BULK_PULL_COOLDOWN' if mode == 'bulk' else 'DELTA_PULL_COOLDOWN')
            if reserve:
                state['updated_epoch'] = current
                state[mode+'_not_before'] = current+INTERVALS[mode]
                if mode == 'bulk':
                    state['delta_not_before'] = max(state['delta_not_before'],current+INTERVALS['delta'])
                self._write(state)
        finally:
            lock.rmdir()

    def _write(self, state):
        temporary = self.path.with_suffix('.tmp')
        try:
            temporary.write_text(json.dumps(state,indent=2)+'\n',encoding='utf-8')
            os.replace(temporary,self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def check(self, mode):
        self._operation(mode,False)

    def reserve(self, mode):
        self._operation(mode,True)
