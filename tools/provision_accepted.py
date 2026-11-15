# SPDX-License-Identifier: MIT
"""Stage an offline, catalogue-approved private bundle into <work>/accepted.

The separately reviewed receipt pins ALL three files, including the original
input bindings. Never reconstruct that receipt from an untrusted delivery.
No game installation or current source archive is read or modified here.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import uuid

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from legends_models import accepted, gate  # noqa: E402
from legends_models.build import acceptance_records  # noqa: E402
from legends_models.plan import Choice  # noqa: E402
from legends_models.roster import BY_KEY  # noqa: E402

FILES = {'actor.IGB', 'manifest.json', 'report.json'}
DEFAULT_RECEIPT = Path(__file__).with_name('accepted-bundles.json')


def _digest(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('invalid SHA-256 in receipt')
    return value


def read_receipt(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data['schema'] != 1 or not isinstance(data['bundles'], list):
        raise ValueError('unsupported bundle receipt')
    seen = set()
    for row in data['bundles']:
        digest = _digest(row['output_sha256'])
        if digest in seen or set(row['files']) != FILES:
            raise ValueError('duplicate receipt or incorrect bundle file list')
        seen.add(digest)
        for value in row['files'].values():
            _digest(value)
        if row['files']['actor.IGB'] != digest:
            raise ValueError('receipt actor hash differs from output hash')
    return data['bundles']


def _no_link(path):
    if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
        raise ValueError(f'link/junction refused: {path}')


def provisioning_records():
    """Pending reviews are visible here only, never to accepted runtime reuse."""
    records = acceptance_records()
    data = json.loads((REPO / 'legends_models/data/acceptance.json').read_text(encoding='utf-8'))
    records.update({r['output_sha256']: r for r in data.get('pending_records', [])})
    return records


def verify_bundle(folder, receipt, records=None, *, allow_pending=False):
    """Verify integrity and catalogue/report/manifest identity; numeric pass is separate."""
    folder = Path(folder).absolute()
    _no_link(folder)
    if not folder.is_dir() or {p.name for p in folder.iterdir()} != FILES:
        raise ValueError('bundle must contain exactly actor.IGB, manifest.json, report.json')
    for name in FILES:
        p = folder / name
        _no_link(p)
        if not p.is_file() or gate.sha256(p) != receipt['files'][name]:
            raise ValueError(f'bundle SHA-256 mismatch: {name}')
    digest = receipt['output_sha256']
    records = provisioning_records() if records is None else records
    record = records.get(digest)
    if record is None:
        raise ValueError('output is absent from this tool acceptance catalogue')
    for key in ('hero', 'tier', 'source', 'output_sha256'):
        if record[key] != receipt[key]:
            raise ValueError(f'receipt/catalogue identity mismatch: {key}')
    status = record.get('status', 'accepted')
    if status not in ('accepted', 'pending') or receipt.get('status', 'accepted') != status:
        raise ValueError('receipt/catalogue review status mismatch')
    if status == 'pending' and not allow_pending:
        raise ValueError('owner review pending; explicit --allow-pending is required')
    if receipt.get('input_provenance', 'complete') != 'complete':
        raise ValueError('original input provenance incomplete; cannot provision this bundle')
    meta = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    report = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
    choice = Choice(record['tier'], record['source'], 'accepted', True, 'provision')
    accepted._validate_manifest(meta, record, BY_KEY[record['hero']], choice, accepted._report_inputs(report))
    for ref in meta['inputs']:
        if ref['root'] not in ('source', 'port', record['tier']):
            raise ValueError('invalid bundle input root')
        if ref['root'] == 'source' and ref['path'] is not None:
            raise ValueError('source actor binding must have a null path')
    # A numeric failure does not revoke approval of exact owner-accepted bytes.
    # The runtime re-hashes the user's actual input files before allowing reuse.
    if status == 'pending':
        result = gate.check_report(folder / 'report.json', folder / 'actor.IGB', require_layers=True)
        if result['candidate_sha256'] != digest or not result['numeric_pass']:
            raise ValueError('pending bundle must have a hash-bound numeric PASS')
        result.update(owner_accepted=False, reason='owner_review_pending', acceptance_reason='Native owner review pending')
        return result
    return accepted.accepted_gate(folder / 'report.json', folder / 'actor.IGB', record)


def provision(bundle, work, receipt, *, check_only=False, records=None, allow_pending=False):
    bundle, work = Path(bundle).absolute(), Path(work).absolute()
    result = verify_bundle(bundle, receipt, records, allow_pending=allow_pending)
    _no_link(work)
    root = work / 'accepted'
    if root.resolve().is_relative_to(bundle.resolve()):
        raise ValueError('shared work root must not be inside the source bundle')
    _no_link(root)
    target = root / receipt['output_sha256']
    _no_link(target)
    if target.exists():
        verify_bundle(target, receipt, records, allow_pending=allow_pending)
        return dict(status='already_present', path=str(target), **result)
    if check_only:
        return dict(status='verified_not_staged', path=str(target), **result)
    root.mkdir(parents=True, exist_ok=True)
    stage = root / ('.accepted-stage-' + uuid.uuid4().hex)
    stage.mkdir()
    try:
        for name in sorted(FILES):
            shutil.copyfile(bundle / name, stage / name)
        verify_bundle(stage, receipt, records, allow_pending=allow_pending)
        # Directory rename publishes the complete group at once. Never replace
        # an existing destination, including one another provisioner just made.
        stage.rename(target)
    finally:
        if stage.exists():
            shutil.rmtree(str(stage.absolute()))
    return dict(status='staged', path=str(target), **result)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--bundle', required=True, type=Path, help='private directory containing the three bundle files')
    ap.add_argument('--work', required=True, type=Path, help='launcher shared work root, NOT ports/<id>')
    ap.add_argument('--output-sha256', required=True, help='catalogued owner-accepted actor SHA-256')
    ap.add_argument('--receipt', type=Path, default=DEFAULT_RECEIPT, help='reviewed three-file hash inventory')
    ap.add_argument('--check-only', action='store_true', help='verify without writing any file')
    ap.add_argument('--allow-pending', action='store_true', help='explicitly stage a numeric-passing pending review; does not grant owner acceptance or bypass missing inputs')
    args = ap.parse_args(argv)
    try:
        rows = read_receipt(args.receipt)
        row = next((r for r in rows if r['output_sha256'] == args.output_sha256), None)
        if row is None:
            raise ValueError('output has no reviewed bundle receipt')
        result = provision(args.bundle, args.work, row, check_only=args.check_only, allow_pending=args.allow_pending)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print(json.dumps(dict(status='refused', error=str(exc))))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
