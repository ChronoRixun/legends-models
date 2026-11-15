# SPDX-License-Identifier: MIT
"""Run the production refiner on a tiny synthetic mesh; no owned data or sampler."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np


class RefineBestState(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('scipy'), 'optional engine dependency SciPy is unavailable')
    def test_divergence_writes_best_fit_and_defers_gate(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            out = root / 'evidence/wolverine'
            (out / 'fit/animations/db').mkdir(parents=True)
            pos = np.array([[0.,0.,0.], [.1,0.,0.], [0.,.1,.2]])
            weights = np.tile([1.,0.,0.,0.], (3,1))
            indices = np.ones((3,4), dtype='u1')
            fit = dict(pos=pos, outline_norm=np.zeros((3,3)), norm=np.zeros((3,3)),
                       weights=weights, indices=indices)
            np.savez(out / 'fit/fit.npz', **fit)
            np.savez(out / 'fit/animations/db/idle.npz', qt=np.zeros((1,2,7)),
                     names=['Root','Spine'], use_translation=[False,False])
            manifest = dict(template=str(root/'template.IGB'), candidate=str(out/'actor.IGB'),
                            source=str(root/'source.igz'), clips=[['idle','db']], fit={})
            (out/'conversion.json').write_text(json.dumps(manifest))
            class Model:
                def __init__(self, path, *args):
                    self.path = Path(path)
                    self.pos, self.weights, self.indices = pos.copy(), weights.copy(), indices.copy()
                    self.palette, self.parent, self.names = [0,1], np.array([-1,0]), ['Root','Spine']
                    self.bones = [SimpleNamespace(get=lambda k,i=i: i) for i in range(2)]
                    self.inverse_by_bone = np.tile(np.eye(4), (2,1,1))
                    self.inverse_by_bone[1,3,2] = -1
                    self.f = SimpleNamespace(objects_of=lambda kind: [SimpleNamespace(get=lambda k: 'outline')])
                def skin(self, world):
                    return self.pos
            def pose(*args):
                world = np.tile(np.eye(4), (2,1,1))
                world[1,3,2] = 1
                return world
            def source(path, target):
                return SimpleNamespace(path=Path(path), pos=pos.copy(), norm=np.zeros((3,3)),
                                       coat_mask=np.zeros(3,dtype=bool), tri=np.array([[0,1,2]]))
            written = []
            def write_candidate(src, target, path, fitted, image):
                written.append(fitted['pos'].copy())
                path.write_bytes(b'synthetic actor')
            deps = {'model': SimpleNamespace(Model=Model, pose=pose),
                    'validate': SimpleNamespace(edges=lambda m:np.array([[0,1],[1,2],[2,0]]),
                                                chain_distance=lambda *args:np.ones(3)),
                    'convert_mua2': SimpleNamespace(ROOT=root, PORT=root, GAME=root, Source=source,
                        atlas=lambda *args:(None,{}), resolve=lambda *args:{}, write_candidate=write_candidate)}
            vendor = Path(__file__).resolve().parents[1] / 'legends_models/engines/vendor/mua2'
            with mock.patch.dict(sys.modules, deps), mock.patch.object(sys, 'path', [str(vendor), *sys.path]):
                spec = importlib.util.spec_from_file_location('synthetic_refine_bind', vendor/'refine_bind.py')
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
            def worsening_optimizer(fun, x0, **kwargs):
                for shift in [.1,.2,.3,.4,.5]:
                    fun(x0 + shift)
                self.fail('divergence guard did not interrupt optimizer line-search calls')
            with mock.patch.object(module, 'minimize', worsening_optimizer), \
                 mock.patch.dict('os.environ', {'MUA2_FIT_DEADLINE': ''}), contextlib.redirect_stdout(io.StringIO()):
                module.refine('wolverine', rising_calls=3)
            np.testing.assert_array_equal(written[0], pos)
            with np.load(out/'fit/fit.npz') as saved:
                np.testing.assert_array_equal(saved['pos'], pos)
            report = json.loads((out/'bind-refinement.json').read_text())
            self.assertEqual((report['stop_reason'], report['best_call']), ('divergence', 1))
            # Three edges, body and outline, each .015 above the 98.5% margin.
            self.assertAlmostEqual(report['best_loss'], .00135)
            self.assertGreater(report['last_loss'], report['best_loss'])
            manifest = json.loads((out/'conversion.json').read_text())
            self.assertTrue(manifest['fit']['gate_deferred_to_independent_validator'])
            self.assertIsNone(manifest['fit']['gate_pass'])


if __name__ == '__main__':
    unittest.main()
