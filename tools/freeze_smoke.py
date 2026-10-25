# SPDX-License-Identifier: MIT
"""Smoke tests of a frozen legends-models folder on synthetic installs (no game data, no network, no game start).
tools/freeze.py --smoke runs them on dist/legends-models; the release workflow runs them again on the unpacked zip.

    python tools/freeze_smoke.py <folder with legends-models.exe> [--version V] [--sampler]

The fixture is made here from tests/synth.py: a stand-in Legends Classic folder (Actors\\<skin>.IGB for every roster
hero, each a minimal valid IGB of invented content, and an empty libIGSg.dll), a loose-file MUA1 folder and an MUA2
actors.bin holding invented bytes. Checks (PASS / FAIL each; exit 1 on any failure):
  layout     legends-models.exe + _internal/legends_models: data/ (community.json, community-credits.md,
             acceptance.json, build-info.json), engines/vendor/PATCHES.json, the converters' entry points, drivers/;
             build-info.json's version is --version; bin/sample_enbaya.exe present exactly when --sampler
  version    `--version` prints --version (the frozen exe reports the full release version)
  help       `--help` names every command
  scan       `scan --json`: every stdout line one JSON object with "event" and "api": 1; the port, MUA1 (2006 loose)
             and MUA2 (pc-bin) found; the bundled numpy / scipy / PIL importable; the sampler reported iff bundled
  plan       `plan --mode upgraded --json`: one row per roster hero; Wolverine on the MUA2 tier
  build      `build --mode sharpen --hero storm`: exit 0, writes mods\\Sharpened-Storm (marker + actor) and its
             +Sharpened-Storm load-order line - the full convert / gate / publish path inside the frozen exe
  verify     `verify --pack Models-Sharpened`: exit 0, ok
  remove     `remove --pack Models-Sharpened`: folder and line gone; verify then exits 30
  offline    `build --mode community --offline`: exit 11 (nothing in the download cache, nothing downloaded);
             `fetch --offline`: exit 20
  usage      an unknown mode: exit 2
  shim       the exe as the converters' interpreter (`-B script.py`): NumPy, SciPy, Pillow and the vendored MUA1
             igb_file reading a synthetic IGB"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / 'tests')]
from legends_models import __version__ as BASE_VERSION, roster  # noqa: E402
from synth import igb, mua_bin  # noqa: E402

COMMANDS = ('scan', 'plan', 'build', 'verify', 'remove', 'fetch')
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = ''):
    print(f'{"PASS" if ok else "FAIL"} {name}' + (f': {detail}' if detail and not ok else ''), flush=True)
    if not ok:
        FAILED.append(name)


def fixture(root: Path) -> list:
    port = root / 'Legends Classic'
    (port / 'Actors').mkdir(parents=True)
    for h in roster.HEROES:
        (port / 'Actors' / f'{h.skin}.IGB').write_bytes(igb(text=f'synthetic {h.key}'))
    (port / 'libIGSg.dll').write_bytes(b'')
    mua1 = root / 'MUA1'
    (mua1 / 'actors').mkdir(parents=True)
    (mua1 / 'actors' / '0301.igb').write_bytes(igb(version=0xF0000008))
    mua2 = root / 'MUA2'
    mua2.mkdir()
    (mua2 / 'actors.bin').write_bytes(mua_bin([('0301.igz', b'synthetic')]))
    for n in ('materials.bin', 'textures.bin'):
        (mua2 / n).write_bytes(b'')
    return ['--port', str(port), '--mua1', str(mua1), '--mua2', str(mua2), '--work', str(root / 'work'),
            '--steam', str(root / 'no-steam'), '--json']


def run(exe: Path, *args, env=None, timeout=600):
    p = subprocess.run([str(exe), *map(str, args)], capture_output=True, text=True, encoding='utf-8',
                       errors='replace', stdin=subprocess.DEVNULL, env=env, timeout=timeout)
    events, bad = [], []
    for line in p.stdout.splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            bad.append(line)
            continue
        if not isinstance(ev, dict) or 'event' not in ev or ev.get('api') != 1:
            bad.append(line)
        events.append(ev)
    return p, events, bad


def last(events):
    return events[-1] if events else {}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('folder')
    ap.add_argument('--version', default=BASE_VERSION, help='the version the build must report')
    ap.add_argument('--sampler', action='store_true', help='the build bundles sample_enbaya.exe')
    a = ap.parse_args(argv)
    folder = Path(a.folder).resolve()
    exe = folder / 'legends-models.exe'
    pkg = folder / '_internal' / 'legends_models'

    need = [exe, pkg / 'data' / 'community.json', pkg / 'data' / 'community-credits.md', pkg / 'data' / 'acceptance.json',
            pkg / 'data' / 'build-info.json', pkg / 'engines' / 'vendor' / 'PATCHES.json',
            pkg / 'engines' / 'vendor' / 'mua1' / 'convert.py', pkg / 'engines' / 'vendor' / 'mua2' / 'convert_mua2.py',
            pkg / 'engines' / 'vendor' / 'xml2' / 'build_xml2.py',
            pkg / 'engines' / 'vendor' / 'community' / 'community_convert.py',
            pkg / 'engines' / 'drivers' / 'community_finish.py']
    missing = [str(p.relative_to(folder)) for p in need if not p.is_file()]
    check('layout', not missing, f'missing {missing}')
    if not exe.is_file():
        return 1
    try:
        info = json.loads((pkg / 'data' / 'build-info.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        info = {'error': repr(e)}
    check('layout: build-info version', info.get('version') == a.version, f'{info.get("version")} != {a.version}')
    bundled = (pkg / 'bin' / 'sample_enbaya.exe').is_file()
    check('layout: sampler', bundled == a.sampler and bool(info.get('sampler')) == a.sampler,
          f'bundled {bundled}, build-info {info.get("sampler")}, expected {a.sampler}')

    with tempfile.TemporaryDirectory(prefix='lm-smoke-') as t:
        root = Path(t)
        env = dict(os.environ, LOCALAPPDATA=str(root / 'appdata'))
        for k in ('LEGENDS_MODELS_PYTHON', 'LEGENDS_MODELS_SAMPLER', 'LEGENDS_MODELS_MIRROR', 'PYTHONPATH', 'PYTHONHOME'):
            env.pop(k, None)
        base = fixture(root)
        port = Path(base[1])

        p, _, _ = run(exe, '--version', env=env)
        check('version', p.returncode == 0 and p.stdout.strip() == a.version, f'exit {p.returncode}: {p.stdout!r}')
        p, _, _ = run(exe, '--help', env=env)
        check('help', p.returncode == 0 and all(c in p.stdout for c in COMMANDS), f'exit {p.returncode}: {p.stderr[-300:]}')

        p, ev, bad = run(exe, 'scan', *base, env=env)
        res = last(ev)
        check('scan: jsonl', p.returncode == 0 and not bad and res.get('event') == 'result',
              f'exit {p.returncode}, bad lines {bad[:2]}, stderr {p.stderr[-400:]}')
        inst, helpers = res.get('installs', {}), res.get('helpers', {})
        check('scan: installs', inst.get('port', {}).get('found') and inst.get('mua1', {}).get('flavour') == '2006-loose'
              and inst.get('mua2', {}).get('flavour') == 'pc-bin', json.dumps(inst)[:400])
        mods = helpers.get('python_modules', {})
        check('scan: bundled numpy/scipy/PIL', bool(mods) and all(mods.values()), json.dumps(helpers)[:400])
        check('scan: sampler', bool(helpers.get('sampler')) == a.sampler, f'sampler {helpers.get("sampler")}')

        p, ev, bad = run(exe, 'plan', '--mode', 'upgraded', *base, env=env)
        rows = {r.get('hero'): r for r in last(ev).get('rows', [])}
        check('plan', p.returncode == 0 and not bad and set(rows) == {h.key for h in roster.HEROES}
              and rows.get('wolverine', {}).get('tier') == 'mua2',
              f'exit {p.returncode}, {len(rows)} rows, wolverine {rows.get("wolverine")}, {p.stderr[-300:]}')

        p, ev, bad = run(exe, 'build', '--mode', 'sharpen', '--hero', 'storm', *base, env=env)
        res = last(ev)
        hero = port / 'mods' / 'Sharpened-Storm'
        order = port / 'mods' / 'load-order.txt'
        lines = order.read_text(encoding='utf-8').splitlines() if order.is_file() else []
        check('build: sharpen', p.returncode == 0 and not bad and res.get('written') is True
              and (hero / 'legends-models.json').is_file() and (hero / 'Actors' / '14401.IGB').is_file()
              and '+Sharpened-Storm' in lines,
              f'exit {p.returncode}, result {json.dumps(res)[:400]}, load-order {lines}, {p.stderr[-400:]}')
        marker = json.loads((hero / 'legends-models.json').read_text(encoding='utf-8')) if hero.is_dir() else {}
        check('build: marker', marker.get('generator') == 'legends-models' and marker.get('version') == a.version,
              f'{ {k: marker.get(k) for k in ("generator", "version")} }')

        p, ev, _ = run(exe, 'verify', '--pack', 'Models-Sharpened', *base, env=env)
        check('verify', p.returncode == 0 and last(ev).get('ok') is True, f'exit {p.returncode}: {json.dumps(last(ev))[:300]}')
        p, ev, _ = run(exe, 'remove', '--pack', 'Models-Sharpened', *base, env=env)
        lines = order.read_text(encoding='utf-8').splitlines() if order.is_file() else []
        check('remove', p.returncode == 0 and not hero.exists() and not any('Sharpened-Storm' in x for x in lines),
              f'exit {p.returncode}: {json.dumps(last(ev))[:300]}, load-order {lines}')
        p, _, _ = run(exe, 'verify', '--pack', 'Models-Sharpened', *base, env=env)
        check('remove: verify mismatch', p.returncode == 30, f'exit {p.returncode}')

        p, ev, _ = run(exe, 'build', '--mode', 'community', '--hero', 'beast', '--offline', *base, env=env)
        check('offline: build', p.returncode == 11 and last(ev).get('written') is False and not (port / 'mods' / 'Community-Beast').exists(),
              f'exit {p.returncode}: {json.dumps(last(ev))[:300]}')
        p, ev, _ = run(exe, 'fetch', '--offline', *base, env=env)
        check('offline: fetch', p.returncode == 20 and last(ev).get('event') == 'error', f'exit {p.returncode}')
        p, _, _ = run(exe, 'plan', '--mode', 'bogus', *base, env=env)
        check('usage', p.returncode == 2, f'exit {p.returncode}')

        probe = root / 'probe.py'
        sample = root / 'synthetic.igb'
        sample.write_bytes(igb())
        probe.write_text(
            'import sys\nimport numpy, scipy.spatial, scipy.sparse, scipy.optimize, PIL.Image\n'
            'sys.path.insert(0, sys.argv[1])\nimport igb_file, model\n'
            'f = igb_file.IgbFile(open(sys.argv[2], "rb").read())\n'
            'o = f.objects[0]\nassert (o.name, o.get(2), o.get(3)) == ("igThing", 7, "hello"), (o.name, o.get(2), o.get(3))\n'
            'print("probe ok", numpy.__version__, scipy.__version__, PIL.__version__)\n', encoding='utf-8')
        p, _, _ = run(exe, '-B', probe, pkg / 'engines' / 'vendor' / 'mua1', sample, env=env)
        check('shim', p.returncode == 0 and 'probe ok' in p.stdout, f'exit {p.returncode}: {p.stdout[-300:]} {p.stderr[-600:]}')

    print(f'smoke: {"FAILED " + ", ".join(FAILED) if FAILED else "all passed"}')
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
