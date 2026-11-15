# SPDX-License-Identifier: MIT
"""Vendored converters: provenance, path hygiene, materialisation (no conversion run)."""
import ast
import io
import json
import os
import re
import shutil
import tempfile
import unittest
import subprocess
import sys
from pathlib import Path
from unittest import mock

from legends_models import engines
from legends_models.errors import ToolError
from legends_models.progress import Reporter
from synth import mua_bin

VENDOR = engines.VENDOR
RESEARCH_PATH = re.compile(r"[A-Z]:[/\\](?:Projects|Games)")


class Vendor(unittest.TestCase):
    def test_every_vendored_file_parses(self):
        files = list(VENDOR.rglob('*.py')) + list(engines.DRIVERS.glob('*.py'))
        self.assertGreater(len(files), 60)
        for p in files:
            ast.parse(p.read_text(encoding='utf-8'), str(p))

    def test_no_research_pc_paths_left_in_code(self):
        for p in VENDOR.rglob('*.py'):
            self.assertIsNone(RESEARCH_PATH.search(p.read_text(encoding='utf-8')), p)

    def test_patch_list_matches_files(self):
        patches = json.loads((VENDOR / 'PATCHES.json').read_text())
        self.assertGreaterEqual(len(patches), 17)
        for patch in patches:
            self.assertIn(patch['new'].split('  #')[0], (VENDOR / patch['file']).read_text(encoding='utf-8'), patch['file'])

    def test_entry_points_the_adapters_call_exist(self):
        need = {'mua1': ['convert.py', 'validate.py', 'fit_weights.py', 'mua_bin.py'],
                'mua2': ['convert_mua2.py', 'refine_bind.py', 'validate_mua2.py', 'mua_bin.py', 'igz.py'],
                'xml2': ['inventory.py', 'build_xml2.py', 'fit_roster.py', 'upscale.py', 'validate_roster.py'],
                'community': ['community_convert.py', 'refine_candidates.py', 'rgba8_probe.py', 'fix_mip_stride.py',
                              'round2/validate.py', 'round2/fit_weights.py']}
        for engine, files in need.items():
            for f in files:
                self.assertTrue((VENDOR / engine / f).is_file(), f'{engine}/{f}')

    def test_mua1_roster_ids_match_the_vendored_converter(self):
        from legends_models import roster
        src = (VENDOR / 'mua1' / 'characters.py').read_text(encoding='utf-8')
        table = ast.literal_eval(src.split('CHARACTERS=', 1)[1].split('\n}', 1)[0] + '\n}')
        for h in roster.HEROES:
            r = h.recipe('mua1')
            if r and r.status != 'none':
                name = roster.MUA1_NAMES.get(h.key, h.key)
                self.assertIn(name, table)
                self.assertEqual(table[name][1], h.skin)
                self.assertEqual(table[name][2], h.animation_db + '.IGB')


class Materialise(unittest.TestCase):
    def test_copies_engine_and_sampler_and_refreshes(self):
        with tempfile.TemporaryDirectory() as t:
            work = Path(t)
            (work / 'bin').mkdir()
            (work / 'bin' / engines.SAMPLER).write_bytes(b'MZ fake sampler')
            root = engines.materialise('community', work)
            self.assertTrue((root / 'tools' / 'community_convert.py').is_file())
            self.assertTrue((root / 'tools' / 'round2' / engines.SAMPLER).is_file())
            self.assertTrue((root / 'tools' / engines.SAMPLER).is_file())
            (root / 'tools' / 'community_convert.py').write_text('tampered')
            (root / 'tools' / '.legends-models-engine').write_text('old')
            engines.materialise('community', work)
            self.assertNotEqual((root / 'tools' / 'community_convert.py').read_text(), 'tampered')

    def test_missing_sampler_is_a_clear_error(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.dict(os.environ, {'LEGENDS_MODELS_SAMPLER': ''}):
            with mock.patch.object(engines, 'sampler_path', lambda work=None: None):
                with self.assertRaises(ToolError):
                    engines.materialise('mua1', Path(t))
                engines.materialise('xml2', Path(t), needs_sampler=False)


class LiveProgress(unittest.TestCase):
    def test_refiner_progress_is_forwarded_before_child_exits_and_log_is_kept(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / 'tools').mkdir()
            script = root / 'tools/refine_bind.py'
            script.write_text('import json,time\nfrom pathlib import Path\n'
                              'print("ordinary diagnostic",flush=True)\n'
                              'print(json.dumps(dict(event="progress",stage="refine_bind",hero="wolverine",'
                              'tier="mua2",call=3,max_calls=120,done=3,total=120,loss=69.,elapsed=12.)),flush=True)\n'
                              'for _ in range(1000):\n'
                              ' if Path("ack").exists():break\n'
                              ' time.sleep(.01)\n'
                              'Path("finished").write_text("yes")\n')
            stream = io.StringIO()
            forwarded_while_running = []
            class LiveReporter(Reporter):
                def emit(self, event, **data):
                    if event == 'progress':
                        forwarded_while_running.append(not (root / 'finished').exists())
                        (root / 'ack').write_text('progress received')
                    super().emit(event, **data)
            code = engines.run('mua2', root, 'refine_bind.py', [], report=LiveReporter(True, stream))
            self.assertEqual(code, 0)
            events = [json.loads(s) for s in stream.getvalue().splitlines()]
            progress = [e for e in events if e['event'] == 'progress']
            self.assertEqual([e['call'] for e in progress], [3])
            self.assertEqual(forwarded_while_running, [True])
            self.assertTrue(all(e['api'] == 1 and 't' in e for e in events))
            self.assertIn('ordinary diagnostic', (root / 'last-run.log').read_text())

    def test_cooperative_cancel_kills_and_reaps_running_engine(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root/'tools').mkdir()
            (root/'tools/refine_bind.py').write_text('import json,time\nprint(json.dumps(dict(event="progress",stage="refine_bind",call=1)),flush=True)\ntime.sleep(30)\n')
            flag = root/'cancel-request'
            children = []
            real_popen = subprocess.Popen
            def spawn(*args, **kwargs):
                child = real_popen(*args, **kwargs)
                children.append(child)
                return child
            class CancellingReporter(Reporter):
                def emit(self, event, **data):
                    if event == 'progress': flag.write_text('cancel')
            with mock.patch.dict(os.environ, {'LEGENDS_MODELS_CANCEL': str(flag)}), mock.patch.object(engines.subprocess, 'Popen', spawn):
                with self.assertRaises(KeyboardInterrupt):
                    engines.run('mua2', root, 'refine_bind.py', [], report=CancellingReporter())
            self.assertEqual(len(children), 1)
            self.assertIsNotNone(children[0].poll())
            self.assertTrue(children[0].stdout.closed)

    def test_timeout_still_kills_and_reaps_child(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / 'tools').mkdir()
            (root / 'tools/sleep.py').write_text('import time\ntime.sleep(30)\n')
            with self.assertRaises(subprocess.TimeoutExpired):
                engines.run('mua2', root, 'sleep.py', [], timeout=.1)

    def test_mua2_extract_refreshes_previous_source_bytes(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            game = root / 'game'
            game.mkdir()
            (game / 'actors.bin').write_bytes(mua_bin([('0301.igz', b'new source')]))
            dest = root / 'evidence/actors/0301.igz'
            dest.parent.mkdir(parents=True)
            dest.write_bytes(b'stale source')
            (root / 'tools').mkdir()
            shutil.copyfile(VENDOR/'mua2/mua_bin.py', root/'tools/mua_bin.py')
            # Driver only needs the stdlib archive reader, so run the real script.
            with mock.patch.dict(os.environ, {'LEGENDS_MODELS_PYTHON': sys.executable}):
                code = engines.run('mua2', root, 'mua2_extract.py', ['--mua2', game, '--id', '0301'],
                                   driver=True)
            self.assertEqual(code, 0, engines.tail(root/'last-run.log'))
            self.assertEqual(dest.read_bytes(), b'new source')

    def test_mua2_refit_discards_animation_cache_from_previous_inputs(self):
        from legends_models.engines import tiers
        from legends_models.plan import Choice
        from legends_models.roster import BY_KEY
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            ev = root / 'evidence/wolverine'
            (ev / 'fit/animations').mkdir(parents=True)
            (ev / 'fit/animations/stale.npz').write_bytes(b'old port or source')
            def execute(engine, work, script, *args, **kwargs):
                if script == 'refine_bind.py':
                    self.assertEqual(args[0][-4:], ['--max-calls', 9, '--max-seconds', 31])
                    self.assertIn('--couple-layers', args[0])
                if script == 'convert_mua2.py':
                    self.assertFalse((ev / 'fit').exists(), 'changed inputs retained filename-only animation/fit cache')
                    (ev / '14301.IGB').write_bytes(b'fresh')
                    (ev / 'gate.json').write_text('{}')
                return 0
            ctx = tiers.Context(root/'port', root, Reporter(True, io.StringIO()), mua2=root/'game', refine_max_calls=9, refine_max_seconds=31)
            with mock.patch.object(tiers, 'materialise', return_value=root), mock.patch.object(tiers, 'run', execute):
                cand = tiers.mua2(BY_KEY['wolverine'], Choice('mua2','0301','accepted',True,''), ctx)
            self.assertEqual(cand.path.read_bytes(), b'fresh')


if __name__ == '__main__':
    unittest.main()
