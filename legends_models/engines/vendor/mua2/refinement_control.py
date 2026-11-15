# SPDX-License-Identifier: MIT
"""Objective-call limits shared by the bind refiner and small synthetic tests.

Checked on both sides of each expensive call (including line-search trials).
The wall-clock limit can overrun by one in-flight call. Gradient probes count
against the budgets but cannot replace the best feasible optimization state.
"""
import math
import time


class RefinementStopped(Exception):
    pass


class RefinementControl:
    def __init__(self, hero, emit, max_calls=120, max_seconds=180., rising_calls=5,
                 progress_every=3, clock=time.monotonic):
        if min(max_calls, max_seconds, rising_calls, progress_every) <= 0 or not math.isfinite(max_seconds):
            raise ValueError('refinement limits must be finite and positive')
        self.hero, self.emit, self.clock = hero, emit, clock
        self.max_calls, self.max_seconds = max_calls, max_seconds
        self.rising_calls, self.progress_every = rising_calls, progress_every
        self.started = clock()
        self.calls = self.rising = 0
        self.previous_loss = None
        self.loss = self.best_loss = None
        self.best_x = None
        self.best_metrics = {}
        self.best_call = 0
        self.stop_reason = None

    @property
    def elapsed(self):
        return self.clock() - self.started

    def progress(self):
        self.emit(dict(event='progress', stage='refine_bind', hero=self.hero, tier='mua2',
                       call=self.calls, max_calls=self.max_calls, done=self.calls, total=self.max_calls,
                       loss=self.loss, best_loss=self.best_loss, elapsed=round(self.elapsed, 3),
                       max_seconds=self.max_seconds, best_call=self.best_call, stop_reason=self.stop_reason,
                       message=f'{self.hero}: bind refinement call {self.calls}/{self.max_calls}'))

    def stop(self, reason):
        self.stop_reason = self.stop_reason or reason
        self.progress()
        raise RefinementStopped(self.stop_reason)

    def before_call(self):
        if self.stop_reason:
            raise RefinementStopped(self.stop_reason)
        if self.elapsed >= self.max_seconds:
            self.stop('wall_clock')
        if self.calls >= self.max_calls:
            self.stop('max_calls')

    def after_call(self, x, loss, metrics, probe=False):
        self.calls += 1
        self.loss = float(loss) if math.isfinite(loss) else None
        if self.loss is None:
            self.stop('nonfinite_loss')
        if not probe:
            if self.best_loss is None or self.loss < self.best_loss:
                self.best_loss, self.best_x = self.loss, x.copy()
                self.best_metrics, self.best_call = dict(metrics), self.calls
            # Small numerical noise and gradient-check perturbations are not divergence.
            rising = self.previous_loss is not None and self.loss > self.previous_loss + 1e-6 * max(1., abs(self.previous_loss))
            self.rising = self.rising + 1 if rising else 0
            self.previous_loss = self.loss
            if self.rising >= self.rising_calls:
                self.stop('divergence')
        if self.elapsed >= self.max_seconds:
            self.stop('wall_clock')
        if self.calls >= self.max_calls:
            self.stop('max_calls')
        if self.calls == 1 or self.calls % self.progress_every == 0:
            self.progress()
