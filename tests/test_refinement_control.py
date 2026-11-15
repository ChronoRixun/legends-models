# SPDX-License-Identifier: MIT
"""Synthetic objective histories: no models, sampler, or game process."""
import importlib.util
import json
import unittest
from pathlib import Path


CONTROL = Path(__file__).resolve().parents[1] / 'legends_models/engines/vendor/mua2/refinement_control.py'


class RefinementLimits(unittest.TestCase):
    def control(self, **kwargs):
        self.assertTrue(CONTROL.is_file(), 'refiner has no objective-call safety controller')
        spec = importlib.util.spec_from_file_location('refinement_control', CONTROL)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.events = []
        self.now = 0.
        self.mod = mod
        return mod.RefinementControl(hero='wolverine', emit=self.events.append,
                                     clock=lambda: self.now, **kwargs)

    def observe(self, guard, x, loss, probe=False):
        guard.before_call()
        guard.after_call([x], loss, {'edge': x}, probe=probe)

    def test_rising_loss_stops_and_keeps_best_not_last_trial(self):
        guard = self.control(rising_calls=3, max_calls=50, max_seconds=100)
        self.observe(guard, 0, 69.)
        self.observe(guard, 1, 50.)
        self.observe(guard, 2, 70.)
        self.observe(guard, 3, 256.)
        with self.assertRaises(self.mod.RefinementStopped):
            self.observe(guard, 4, 796.)
        self.assertEqual(guard.stop_reason, 'divergence')
        self.assertEqual((guard.best_x, guard.best_loss, guard.best_metrics), ([1], 50., {'edge': 1}))
        self.assertEqual(guard.calls, 5)
        self.assertEqual(self.events[-1]['stop_reason'], 'divergence')

    def test_time_cap_checked_before_and_after_objective(self):
        guard = self.control(max_seconds=10)
        self.observe(guard, 0, 7.)
        self.now = 11
        with self.assertRaises(self.mod.RefinementStopped):
            guard.before_call()
        self.assertEqual((guard.calls, guard.best_x, guard.stop_reason), (1, [0], 'wall_clock'))
        guard = self.control(max_seconds=10)
        guard.before_call()
        self.now = 30
        with self.assertRaises(self.mod.RefinementStopped):
            guard.after_call([2], 3., {})
        self.assertEqual(guard.best_x, [2])

    def test_call_cap_bounds_flat_loss_and_probes_do_not_replace_best(self):
        guard = self.control(max_calls=3)
        self.observe(guard, 0, 5.)
        self.observe(guard, 1, 1., probe=True)
        with self.assertRaises(self.mod.RefinementStopped):
            self.observe(guard, 2, 5.)
        self.assertEqual((guard.best_x, guard.stop_reason), ([0], 'max_calls'))
        with self.assertRaises(self.mod.RefinementStopped):
            guard.before_call()
        self.assertEqual(guard.calls, 3)

    def test_nonfinite_loss_cannot_replace_best_or_break_json(self):
        guard = self.control()
        self.observe(guard, 0, 2.)
        with self.assertRaises(self.mod.RefinementStopped):
            self.observe(guard, 1, float('nan'))
        self.assertEqual((guard.best_x, guard.stop_reason), ([0], 'nonfinite_loss'))
        for event in self.events:
            json.dumps(event, allow_nan=False)

    def test_progress_includes_launcher_fields_every_few_calls(self):
        guard = self.control(progress_every=3)
        for i in range(7):
            self.now = i * 2.
            self.observe(guard, i, 10. - i)
        self.assertEqual([e['call'] for e in self.events], [1, 3, 6])
        event = self.events[-1]
        self.assertEqual((event['event'], event['stage'], event['hero'], event['tier']),
                         ('progress', 'refine_bind', 'wolverine', 'mua2'))
        self.assertEqual((event['loss'], event['best_loss'], event['elapsed']), (5., 5., 10.))
        self.assertEqual((event['done'], event['total']), (6, guard.max_calls))


if __name__ == '__main__':
    unittest.main()
