# SPDX-License-Identifier: MIT
"""Release tag policy of legends-models, shared by tools/freeze.py, the release workflow and its tests (offline).

    python tools/release_metadata.py --tag v0.3.0-beta.1 [--notes-output notes.md] [--dist dist] [--changelog CHANGELOG.md]

A release tag is vX.Y.Z, vX.Y.Z-beta.N or vX.Y.Z-rc.N. X.Y.Z must equal legends_models.__version__ (the numeric base);
the full tag version (0.3.0-beta.1) is what the frozen exe, the zip name and legends-models.json carry. A tag with a
suffix is published as a GitHub pre-release that is never marked latest (the launcher's stable channel reads
releases/latest; its Beta channel reads pre-releases). Prints the metadata as one JSON object."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

NUMBER = r'(?:0|[1-9][0-9]*)'
TAG = re.compile(rf'v(?P<base>{NUMBER}\.{NUMBER}\.{NUMBER})(?:-(?:beta|rc)\.{NUMBER})?')


def metadata(tag: str) -> dict:
    match = TAG.fullmatch(tag or '')
    if not match:
        raise ValueError(f'invalid release tag {tag!r}: expected vX.Y.Z[-beta.N|-rc.N]')
    pre = '-' in tag
    return dict(tag=tag, version=tag[1:], base=match['base'], prerelease=pre,
                flags=['--prerelease', '--latest=false'] if pre else [])


def release_version(tag: str | None, base: str) -> str:
    """The version a build carries: `base` without a tag, else the tag's full version (its base must be `base`)."""
    if not tag:
        return base
    meta = metadata(tag)
    if meta['base'] != base:
        raise ValueError(f'tag {tag}, but legends-models is version {base}: update __version__ in '
                         'legends_models/__init__.py (and pyproject.toml)')
    return meta['version']


def release_notes(tag: str, manifest: dict, changelog: str, checksums: str) -> str:
    """Release notes: the CHANGELOG.md section of the tag's base version (required for pre-releases)."""
    meta = metadata(tag)
    base_tag = 'v' + meta['base']
    section, active, found = [], False, False
    for line in changelog.splitlines():
        if line.startswith('## '):
            active = line == '## ' + base_tag or line.startswith('## ' + base_tag + ' ')
            found |= active
            continue
        if active:
            section.append(line)
    if meta['prerelease'] and not found:
        raise ValueError(f'CHANGELOG.md has no "## {base_tag}" section')
    banner = 'Beta - test build\n\n' if meta['prerelease'] else ''
    launcher = str(manifest.get('min_launcher', ''))
    launcher = launcher[:-2] + ' (or a beta of it)' if launcher.endswith('-0') else launcher
    return (banner + f'legends-models {tag} (launcher API {manifest.get("api")}, pack content version '
            f'{manifest.get("content_version")}, for Ultimate Legends {launcher} or newer).\n\n'
            + '\n'.join(section).strip('\n') + '\n\n'
            "The Ultimate Legends launcher's Character models page runs it for you. Standalone use: unzip, then run\n"
            '`legends-models.exe --help`. It contains no game data and no community skins: it builds optional\n'
            'character-model mods for Legends Classic on your PC from games you own; community skins are downloaded\n'
            "from their author's own links and credited.\n\n"
            'SHA-256:\n```\n' + checksums.rstrip('\n') + '\n```\n')


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--tag', required=True)
    ap.add_argument('--notes-output', type=Path)
    ap.add_argument('--changelog', type=Path, default=Path('CHANGELOG.md'))
    ap.add_argument('--dist', type=Path, default=Path('dist'))
    args = ap.parse_args(argv)
    try:
        meta = metadata(args.tag)
        if args.notes_output:
            manifest = json.loads((args.dist / 'legends-models.json').read_text(encoding='utf-8'))
            if manifest['version'] != meta['version']:
                raise ValueError(f'legends-models.json is version {manifest["version"]}, the tag {meta["version"]}')
            notes = release_notes(args.tag, manifest, args.changelog.read_text(encoding='utf-8'),
                                  (args.dist / 'SHA256SUMS.txt').read_text(encoding='utf-8'))
            args.notes_output.write_text(notes, encoding='utf-8', newline='\n')
    except (ValueError, KeyError, OSError) as exc:
        ap.error(str(exc))
    print(json.dumps(meta))


if __name__ == '__main__':
    main()
