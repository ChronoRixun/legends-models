# SPDX-License-Identifier: MIT
"""Check the release files tools/freeze.py wrote before anything is published.

    python tools/verify_dist.py <dist> [--version V] [--sampler]

  manifest   legends-models.json: version (= --version), content_version, zip, size, sha256, min_launcher, api,
             sampler_bundled (= --sampler); the zip name carries the version (the launcher's version_in_zip_name)
  zip        the file's size and SHA-256 are the manifest's; legends-models.exe and _internal/ at the top; nothing
             outside them; no game or skin file type in the package data (the bundled sampler excepted)
  sums       SHA256SUMS.txt names the zip and legends-models/legends-models.exe with their real hashes
Exit 1 on any mismatch."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

FORBIDDEN = {'.igb', '.igz', '.rar', '.7z', '.dds', '.png', '.jpg', '.jpeg', '.tga', '.bmp', '.hkx', '.bin',
             '.xmlb', '.engb', '.onnx', '.zip', '.iso', '.xbe'}
FIELDS = {'version': str, 'content_version': int, 'zip': str, 'size': int, 'sha256': str, 'min_launcher': str,
          'api': int, 'sampler_bundled': bool}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def verify(dist: Path, version: str | None, sampler: bool | None) -> list[str]:
    errors = []
    try:
        m = json.loads((dist / 'legends-models.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        return [f'legends-models.json: {e}']
    for k, t in FIELDS.items():
        if not isinstance(m.get(k), t) or (t is int and isinstance(m.get(k), bool)):
            errors.append(f'manifest: {k} missing or not {t.__name__}: {m.get(k)!r}')
    if errors:
        return errors
    if version is not None and m['version'] != version:
        errors.append(f'manifest: version {m["version"]}, expected {version}')
    if sampler is not None and m['sampler_bundled'] != sampler:
        errors.append(f'manifest: sampler_bundled {m["sampler_bundled"]}, expected {sampler}')
    if m['zip'] != f'legends-models-{m["version"]}-win64.zip':
        errors.append(f'manifest: zip {m["zip"]} is not legends-models-{m["version"]}-win64.zip')
    archive = dist / m['zip']
    if not archive.is_file():
        return errors + [f'{archive} is missing']
    digest = sha256_file(archive)
    if archive.stat().st_size != m['size'] or digest != m['sha256']:
        errors.append(f'zip: size {archive.stat().st_size} / sha256 {digest} != manifest {m["size"]} / {m["sha256"]}')
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
        bad = z.testzip()
        if bad:
            errors.append(f'zip: corrupt member {bad}')
        if 'legends-models.exe' not in names:
            errors.append('zip: no legends-models.exe at the top')
        stray = [n for n in names if n != 'legends-models.exe' and not n.startswith('_internal/')]
        if stray:
            errors.append(f'zip: files outside legends-models.exe and _internal/: {stray[:5]}')
        data = [n for n in names if n.startswith('_internal/legends_models/')]
        if not data:
            errors.append('zip: no _internal/legends_models/ package data')
        forbidden = [n for n in data if Path(n).suffix.lower() in FORBIDDEN]
        if forbidden:
            errors.append(f'zip: game or skin file types in the package data: {forbidden[:5]}')
        has_sampler = '_internal/legends_models/bin/sample_enbaya.exe' in names
        if has_sampler != m['sampler_bundled']:
            errors.append(f'zip: sampler present {has_sampler}, manifest says {m["sampler_bundled"]}')
        try:
            info = json.loads(z.read('_internal/legends_models/data/build-info.json'))
            if info.get('version') != m['version']:
                errors.append(f'zip: build-info version {info.get("version")} != manifest {m["version"]}')
        except (KeyError, ValueError) as e:
            errors.append(f'zip: build-info.json: {e!r}')
        exe_digest = hashlib.sha256(z.read('legends-models.exe')).hexdigest() if 'legends-models.exe' in names else ''
    try:
        sums = dict(reversed(line.split('  ', 1)) for line in
                    (dist / 'SHA256SUMS.txt').read_text(encoding='utf-8').splitlines() if line.strip())
    except (OSError, ValueError) as e:
        return errors + [f'SHA256SUMS.txt: {e}']
    if sums.get(m['zip']) != digest:
        errors.append(f'SHA256SUMS.txt: {m["zip"]} {sums.get(m["zip"])} != {digest}')
    if sums.get('legends-models/legends-models.exe') != exe_digest:
        errors.append(f'SHA256SUMS.txt: legends-models.exe {sums.get("legends-models/legends-models.exe")} != {exe_digest}')
    return errors


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('dist')
    ap.add_argument('--version')
    ap.add_argument('--sampler', action='store_true', default=None)
    a = ap.parse_args(argv)
    errors = verify(Path(a.dist), a.version, a.sampler)
    for e in errors:
        print('FAIL', e)
    if not errors:
        m = json.loads((Path(a.dist) / 'legends-models.json').read_text(encoding='utf-8'))
        print(f'PASS release files: {m["zip"]} ({m["size"]} bytes, sha256 {m["sha256"]}), manifest and SHA256SUMS agree')
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
