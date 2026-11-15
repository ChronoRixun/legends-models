# SPDX-License-Identifier: MIT
"""Refuse private accepted bundles anywhere in a public ZIP or frozen folder.

Run for both tool and launcher ZIPs. Hashes/receipts are public metadata; the
actor, independent report and original-input manifest must remain private.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile

REPO = Path(__file__).resolve().parents[1]
RECEIPT = Path(__file__).with_name('accepted-bundles.json')
JSON_LIMIT = 2 * 1024 * 1024


def private_hashes(receipt=RECEIPT):
    data = json.loads(Path(receipt).read_text(encoding='utf-8'))
    hashes = {h for b in data['bundles'] for h in b['files'].values()}
    catalogue = json.loads((REPO / 'legends_models/data/acceptance.json').read_text(encoding='utf-8'))
    return hashes | {r['output_sha256'] for r in catalogue['records'] + catalogue.get('pending_records', [])}


def member_errors(name, stream, hashes):
    p = PurePosixPath(name.replace('\\', '/').lower())
    errors = []
    if (any(x in ('accepted', 'private-accepted') or x.startswith('.accepted-stage-') for x in p.parts)
            or p.suffix in ('.igb', '.igz')):
        errors.append(f'private bundle/game member path: {name}')
    digest = hashlib.sha256()
    head = bytearray()
    for chunk in iter(lambda: stream.read(1 << 20), b''):
        digest.update(chunk)
        if len(head) <= JSON_LIMIT:
            head.extend(chunk[:JSON_LIMIT + 1 - len(head)])
    if digest.hexdigest() in hashes:
        errors.append(f'private bundle file hash: {name}')
    # IGB detection also catches renamed, non-catalogued private actors.
    if len(head) >= 0x30 and head[0x28:0x2c] == b'\xda\xfa\x00\x00':
        errors.append(f'private game actor signature: {name}')
    if len(head) <= JSON_LIMIT:
        try:
            data = json.loads(head)
        except (ValueError, UnicodeError):
            data = None
        if isinstance(data, dict) and data.get('schema') == 1 and {'hero', 'tier', 'source', 'output_sha256', 'inputs'} <= data.keys():
            errors.append(f'private accepted input manifest: {name}')
    return errors


def check_zip(archive, *, hashes=None):
    hashes = private_hashes() if hashes is None else hashes
    errors = []
    try:
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                with z.open(info) as stream:
                    errors.extend(member_errors(info.filename, stream, hashes))
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
        errors.append(f'public ZIP could not be checked: {exc}')
    return errors


def check_folder(folder):
    hashes = private_hashes()
    errors = []
    for p in sorted(Path(folder).rglob('*')):
        if p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction()):
            errors.append(f'public bundle link/junction: {p}')
        elif p.is_file():
            with p.open('rb') as stream:
                errors.extend(member_errors(p.relative_to(folder).as_posix(), stream, hashes))
    return errors


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('zip', nargs='+', type=Path)
    args = ap.parse_args(argv)
    errors = [f'{p}: {e}' for p in args.zip for e in check_zip(p)]
    for error in errors:
        print('FAIL', error)
    if not errors:
        print(f'PASS: {len(args.zip)} public ZIP(s), no private accepted bundle files')
    return bool(errors)


if __name__ == '__main__':
    raise SystemExit(main())
