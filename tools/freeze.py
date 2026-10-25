"""Freeze legends-models: legends-models.exe (PyInstaller one folder), its release zip and the launcher's manifest.

    python tools/freeze.py [--dist DIR] [--work DIR] [--sampler EXE] [--min-launcher V] [--smoke]
    python tools/freeze.py --check-tag vX.Y.Z[-beta.N|-rc.N]

Same shape as the Legends Classic builder's tools/freeze_builder.py: run it with the Python the release ships
(CPython 3.12 x64 with numpy, scipy, pillow and PyInstaller pinned in requirements-freeze.txt), from a venv or CI.
  1. <work>/gen/build-info.json: version, API version, commit (+ dirty), Python / NumPy / SciPy / Pillow /
     PyInstaller versions, the bundled sampler's sha256 (or none);
  2. PyInstaller with tools/legends-models.spec -> <dist>/legends-models/ (legends-models.exe + _internal/);
     then a content guard over the bundle's package data: no game or skin file type (.igb .igz .rar .dds .png ...)
     and no game-format signature (tools/check_no_game_content.sniff_magic) may ship;
  3. <dist>/legends-models-<VERSION>-win64.zip (exe + _internal/ at the top: the launcher's tool_install unpacks it
     into tools\\legends-models\\<version>\\), <dist>/legends-models.json (the release manifest tool_install::
     parse_manifest reads: version, content_version, zip, size, sha256, min_launcher; plus api, sampler_bundled) and
     <dist>/SHA256SUMS.txt;
  4. --smoke: tools/freeze_smoke.py against the frozen folder (synthetic installs, no game data).
--check-tag (CI on a tag): the tag's numeric base must equal legends_models.__version__ (tools/release_metadata.py).
LM_RELEASE_TAG (CI on a tag): the full tag version (0.3.0-beta.1) goes into build-info (which the frozen exe reports
as its --version), the exe's version resource, the zip name and the manifest. A pre-release tag's default
min_launcher is MIN_LAUNCHER-0, so the launcher's own betas of that version accept it (the builder's
requires_xml2fix policy).
Nothing is published. No UPX. --sampler bundles a CI-built sample_enbaya.exe as _internal/legends_models/bin/
(the converters need it for animation clips; without it the exe reports the helper missing, as the CLI does)."""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / 'tools'
SPEC = TOOLS / 'legends-models.spec'
APP = 'legends-models'
EXE_NAME = 'legends-models.exe'
API = 1
CONTENT_VERSION = 1          # pack format (marker + per-hero layout); bump when a rebuild is required
MIN_LAUNCHER = '0.5.0'       # the first launcher with the Character models page (set when that launcher ships)
FORBIDDEN = {'.igb', '.igz', '.rar', '.7z', '.dds', '.png', '.jpg', '.jpeg', '.tga', '.bmp', '.hkx', '.bin',
             '.xmlb', '.engb', '.onnx', '.zip', '.iso', '.xbe'}

sys.path.insert(0, str(REPO))
sys.path.insert(0, str(TOOLS))
from legends_models import __version__ as VERSION  # noqa: E402  (the numeric base)
from release_metadata import metadata, release_version  # noqa: E402


def zip_name(version: str) -> str:
    return f'{APP}-{version}-win64.zip'


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def git(*args) -> str:
    try:
        return subprocess.run(['git', '-C', str(REPO), *args], capture_output=True, text=True, check=True,
                              stdin=subprocess.DEVNULL).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ''


def build_info(version: str, sampler: Path | None) -> dict:
    import numpy
    import PIL
    import PyInstaller
    import scipy
    commit = git('rev-parse', 'HEAD')
    info = {'version': version, 'api': API, 'content_version': CONTENT_VERSION, 'commit': commit[:12] or 'unknown',
            'commit_full': commit or 'unknown', 'dirty': bool(git('status', '--porcelain', '--untracked-files=no')),
            'built': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'python': sys.version.split()[0], 'numpy': numpy.__version__, 'scipy': scipy.__version__,
            'pillow': PIL.__version__, 'pyinstaller': PyInstaller.__version__,
            'sampler': {'file': sampler.name, 'sha256': sha256_file(sampler)} if sampler else None}
    if os.environ.get('GITHUB_RUN_ID'):
        info['ci'] = {'run_id': os.environ['GITHUB_RUN_ID'], 'repository': os.environ.get('GITHUB_REPOSITORY', ''),
                      'ref': os.environ.get('GITHUB_REF', '')}
    return info


def run_pyinstaller(dist: Path, work: Path, gen: Path, sampler: Path | None, version: str):
    env = dict(os.environ, LM_FREEZE_GEN=str(gen), LM_FREEZE_SAMPLER=str(sampler or ''), LM_FREEZE_VERSION=version)
    cmd = [sys.executable, '-m', 'PyInstaller', str(SPEC), '--noconfirm', '--clean', '--log-level', 'WARN',
           '--distpath', str(dist), '--workpath', str(work / 'pyinstaller')]
    print('>', ' '.join(cmd), flush=True)
    rc = subprocess.run(cmd, env=env, cwd=str(REPO)).returncode
    if rc:
        raise SystemExit(f'PyInstaller failed (exit {rc})')


def guard_bundle(folder: Path, sampler: bool):
    """No game or community-skin file type and no game-format signature anywhere in the bundle's package data (the
    Python runtime's own DLLs and .pyd are binaries by nature and are not looked at; the bundled sampler is ours,
    built by CI from tools/sample_enbaya.cpp)."""
    from check_no_game_content import sniff_magic
    data = folder / '_internal' / 'legends_models'
    allowed = {data / 'bin' / 'sample_enbaya.exe'} if sampler else set()
    bad = []
    for p in sorted(data.rglob('*')):
        if not p.is_file() or p in allowed:
            continue
        if p.suffix.lower() in FORBIDDEN:
            bad.append(f'{p} (file type)')
            continue
        with open(p, 'rb') as fh:
            kind = sniff_magic(fh.read(0x10020))
        if kind and p.suffix.lower() not in ('.pyc',):
            bad.append(f'{p} ({kind})')
    if bad:
        raise SystemExit('the bundle carries files that may hold game data: ' + ', '.join(bad[:10]))


def make_zip(folder: Path, archive: Path) -> int:
    tmp = archive.with_suffix('.zip.partial')
    files = sorted(p for p in folder.rglob('*') if p.is_file())
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in files:
            z.write(p, p.relative_to(folder).as_posix())
    os.replace(tmp, archive)
    return len(files)


def write_manifest(dist: Path, archive: Path, version: str, min_launcher: str, sampler: bool) -> dict:
    """legends-models.json in the shape of xml1-builder.json (tool_install::parse_manifest)."""
    manifest = {'version': version, 'content_version': CONTENT_VERSION, 'zip': archive.name,
                'size': archive.stat().st_size, 'sha256': sha256_file(archive), 'min_launcher': min_launcher,
                'api': API, 'sampler_bundled': sampler}
    (dist / f'{APP}.json').write_text(json.dumps(manifest, indent=1) + '\n', encoding='utf-8', newline='\n')
    return manifest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog='freeze.py', description=__doc__.split('\n\n')[0])
    ap.add_argument('--dist', default=str(REPO / 'dist'))
    ap.add_argument('--work', default=str(REPO / 'build' / 'freeze'))
    ap.add_argument('--sampler', help='a CI-built sample_enbaya.exe to bundle (default: none)')
    ap.add_argument('--min-launcher', dest='min_launcher',
                    help=f'the manifest\'s min_launcher (default {MIN_LAUNCHER}; {MIN_LAUNCHER}-0 for a pre-release)')
    ap.add_argument('--smoke', action='store_true', help='run tools/freeze_smoke.py on the frozen folder')
    ap.add_argument('--check-tag', dest='check_tag', help='check v<VERSION> with an optional -beta.N or -rc.N suffix')
    a = ap.parse_args(argv)
    if a.check_tag is not None:
        try:
            version = release_version(a.check_tag, VERSION)
        except ValueError as exc:
            print(exc)
            return 1
        print(f'tag {a.check_tag} = legends-models {version}')
        return 0
    try:
        tag = os.environ.get('LM_RELEASE_TAG') or None
        version = release_version(tag, VERSION)
    except ValueError as exc:
        ap.error(str(exc))
    prerelease = bool(tag) and metadata(tag)['prerelease']
    min_launcher = a.min_launcher or (MIN_LAUNCHER + '-0' if prerelease else MIN_LAUNCHER)
    t0 = time.time()
    dist, work = Path(a.dist).resolve(), Path(a.work).resolve()
    gen = work / 'gen'
    shutil.rmtree(gen, ignore_errors=True)
    gen.mkdir(parents=True)
    dist.mkdir(parents=True, exist_ok=True)
    sampler = Path(a.sampler).resolve() if a.sampler else None
    if sampler and not sampler.is_file():
        raise SystemExit(f'no sampler at {sampler}')
    info = build_info(version, sampler)
    (gen / 'build-info.json').write_text(json.dumps(info, indent=1) + '\n', encoding='utf-8', newline='\n')
    print('build-info:', json.dumps(info))
    folder = dist / APP
    shutil.rmtree(folder, ignore_errors=True)
    run_pyinstaller(dist, work, gen, sampler, version)
    exe = folder / EXE_NAME
    if not exe.is_file():
        raise SystemExit(f'{exe} was not made')
    size = sum(p.stat().st_size for p in folder.rglob('*') if p.is_file())
    print(f'frozen: {folder} ({size / 1e6:.1f} MB unpacked)')
    guard_bundle(folder, sampler is not None)
    for old in dist.glob(f'{APP}-*-win64.zip'):
        old.unlink()
    archive = dist / zip_name(version)
    n = make_zip(folder, archive)
    manifest = write_manifest(dist, archive, version, min_launcher, sampler is not None)
    (dist / 'SHA256SUMS.txt').write_text(f'{manifest["sha256"]}  {archive.name}\n{sha256_file(exe)}  {APP}/{EXE_NAME}\n',
                                         encoding='utf-8', newline='\n')
    print(f'zip: {archive} ({n} files, {manifest["size"] / 1e6:.1f} MB, sha256 {manifest["sha256"]})')
    print('manifest:', json.dumps(manifest))
    if a.smoke:
        rc = subprocess.run([sys.executable, str(TOOLS / 'freeze_smoke.py'), str(folder), '--version', version,
                             *(['--sampler'] if sampler else [])], cwd=str(REPO)).returncode
        if rc:
            print('smoke tests FAILED')
            return 1
    print(f'done in {time.time() - t0:.0f} s')
    return 0


if __name__ == '__main__':
    sys.exit(main())
