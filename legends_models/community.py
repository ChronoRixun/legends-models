# SPDX-License-Identifier: MIT
"""BaconWizard17's community skins: fetch on the player's PC, quarantine, verify, extract.

Nothing is bundled. The tool downloads from the author's own MediaFire links (the page's direct
link is resolved locally), or from a mirror URL configured by whoever has the author's OK to host
the archives (for the launcher later; the credit stays). Every archive must match the manifest's
size and SHA-256 before it leaves quarantine, and its member list must equal the manifest's list
and pass the path/extension allow-list before anything is extracted. Members are never executed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from importlib import resources
from pathlib import Path, PurePosixPath

from .errors import DOWNLOAD, MISSING_INPUT, ToolError

# Community names used by the pack posts / research rows -> roster keys.
NAMES = {'Beast': 'beast', 'Colossus': 'colossus', 'Cyclops': 'cyclops', 'Emma Frost': 'frost', 'Gambit': 'gambit',
         'Iceman': 'iceman', 'Jubilee': 'jubilee', 'Magma': 'magma', 'Nightcrawler': 'nightcrawler',
         'Jean Grey': 'phoenix', 'Professor X': 'profxastral', 'Psylocke': 'psylocke', 'Rogue': 'rogue',
         'Storm': 'storm', 'Wolverine': 'wolverine', 'Magneto': 'magneto', 'Juggernaut': 'juggernaut',
         'Sabretooth': 'sabretooth', 'Toad': 'toad', 'Pyro': 'pyro'}
KEY_TO_NAME = {v: k for k, v in NAMES.items()}
AUTHOR_URL = re.compile(r'https://www\.mediafire\.com/file(?:_premium)?/[A-Za-z0-9]+/[^/?#]+\.rar/file')
ARCHIVE_NAME = re.compile(r'[0-9]{2,3}XX_[A-Za-z0-9_()-]+\.rar')
USER_AGENT = 'legends-models (player-local fetch of community skins with credit)'


def manifest():
    return json.loads(resources.files('legends_models.data').joinpath('community.json').read_text(encoding='utf-8'))


MIRROR_ENV = 'LEGENDS_MODELS_MIRROR'


def resolve_mirror(arg=None, data=None):
    """Mirror base URL, first that is set: --mirror, the LEGENDS_MODELS_MIRROR environment variable, the
    manifest's own "mirror" entry (empty in this repository: set by whoever hosts the archives with the
    author's OK, e.g. a release of a community-models repository). Empty -> author links only."""
    for c in (arg, os.environ.get(MIRROR_ENV), (data or manifest()).get('mirror')):
        if c and c.strip():
            return c.strip()
    return None


def credits_text():
    return resources.files('legends_models.data').joinpath('community-credits.md').read_text(encoding='utf-8')


def entries_for(hero_key, data=None):
    data = data or manifest()
    name = KEY_TO_NAME.get(hero_key, hero_key)
    return [e for e in data['archives'] if e['character'] == name]


def entry(hero_key, costume, data=None):
    for e in entries_for(hero_key, data):
        if e['costume'].casefold() == costume.casefold():
            return e
    raise ToolError(f'no community archive for {hero_key} / {costume}', MISSING_INPUT)


# ----------------------------------------------------------------------------------- allow-list
def check_member_names(names, allowed_suffixes):
    """Path safety + extension allow-list (from models-community safe_archive.check_members)."""
    if not names:
        raise ToolError('empty archive', DOWNLOAD)
    for name in names:
        q = name.replace('\\', '/')
        p = PurePosixPath(q)
        parts = q.rstrip('/').split('/')
        if p.is_absolute() or ':' in q or any(s in ('', '.', '..') or s.endswith(('.', ' ')) for s in parts):
            raise ToolError(f'unsafe archive path {name!r}', DOWNLOAD)
        if q.endswith('/') or not p.suffix:
            continue  # directory record
        if p.suffix.lower() not in allowed_suffixes:
            raise ToolError(f'archive member not on the allow-list: {name!r}', DOWNLOAD)
    return True


def check_against_manifest(names, record, allowed_suffixes):
    check_member_names(names, allowed_suffixes)
    norm = lambda n: n.replace('\\', '/').rstrip('/')
    got, want = sorted(map(norm, names)), sorted(map(norm, record['members']))
    if got != want:
        extra = sorted(set(got) - set(want))
        missing = sorted(set(want) - set(got))
        raise ToolError(f'archive members differ from manifest (extra {extra[:5]}, missing {missing[:5]})', DOWNLOAD)
    return True


# ----------------------------------------------------------------------------------- download
class _ButtonFinder(HTMLParser):
    def __init__(self):
        super().__init__()
        self.href = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'a' and a.get('id') == 'downloadButton' and self.href is None:
            self.href = a.get('href')


def resolve_mediafire(page):
    """The direct link on a MediaFire file page (the 'downloadButton' anchor), host-checked."""
    finder = _ButtonFinder()
    finder.feed(page.decode('utf-8', 'replace') if isinstance(page, bytes) else page)
    href = finder.href
    if not href:
        raise ToolError('MediaFire page has no download button (link changed or removed)', DOWNLOAD)
    u = urllib.parse.urlparse(href)
    if u.scheme != 'https' or not (u.hostname or '').endswith('.mediafire.com'):
        raise ToolError(f'unexpected direct-link host {u.hostname!r}', DOWNLOAD)
    return href


def archive_name(record):
    name = record['archive']
    if not ARCHIVE_NAME.fullmatch(name):
        raise ToolError(f'unapproved archive file name {name!r}', DOWNLOAD)
    return name


def source_urls(record, mirror=None):
    """Candidate URLs in order: mirror (if configured) then the author's link."""
    urls = []
    if mirror:
        m = urllib.parse.urlparse(mirror)
        if m.scheme != 'https':
            raise ToolError('mirror must be an https URL', DOWNLOAD)
        urls.append(('mirror', mirror.rstrip('/') + '/' + urllib.parse.quote(archive_name(record))))
    if not AUTHOR_URL.fullmatch(record['url']):
        raise ToolError(f'manifest URL is not an approved author link: {record["url"]}', DOWNLOAD)
    urls.append(('author', record['url']))
    return urls


def http_get(url, limit):
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 (https only, checked by callers)
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ToolError('download larger than the manifest allows', DOWNLOAD)
    return data


def fetch(record, cache, mirror=None, get=http_get, report=None, offline=False):
    """Download one archive into <cache>/quarantine, verify, then move it to <cache>/archives.
    Returns the verified archive path. Reuses an already-verified archive. offline: cache only, a missing
    archive is refused (DOWNLOAD) without any network access."""
    cache = Path(cache)
    name = archive_name(record)
    final = cache / 'archives' / f'{record["sha256"][:16]}-{name}'
    if final.exists() and sha256_file(final) == record['sha256']:
        return final
    if offline:
        raise ToolError(f'{name} is not in the download cache and --offline forbids downloading it', DOWNLOAD)
    limit = int(manifest_limit(record))
    errors = []
    for kind, url in source_urls(record, mirror):
        try:
            data = get(url, limit)
            if kind == 'author' and not data.startswith(b'Rar!'):
                data = get(resolve_mediafire(data), limit)  # page -> direct link
            q = cache / 'quarantine'
            q.mkdir(parents=True, exist_ok=True)
            tmp = q / (name + '.part')
            tmp.write_bytes(data)
            verify_archive(tmp, record)
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp, final)
            if report:
                report.emit('download', archive=name, source=kind, bytes=len(data), sha256=record['sha256'],
                            character=record.get('character'), costume=record.get('costume'))
            return final
        except ToolError as e:
            errors.append(f'{kind}: {e}')
        except OSError as e:
            errors.append(f'{kind}: {e}')
    for p in (cache / 'quarantine').glob(name + '.part'):
        p.unlink()
    raise ToolError(f'could not fetch {name}: ' + '; '.join(errors), DOWNLOAD)


def manifest_limit(record):
    return max(record['bytes'], 1)


def verify_archive(path, record):
    data = Path(path).read_bytes()
    if len(data) != record['bytes']:
        raise ToolError(f'size {len(data)} != manifest {record["bytes"]}', DOWNLOAD)
    if not data.startswith(b'Rar!'):
        raise ToolError('not a RAR archive', DOWNLOAD)
    digest = hashlib.sha256(data).hexdigest()
    if digest != record['sha256']:
        raise ToolError(f'SHA-256 {digest} != manifest {record["sha256"]}', DOWNLOAD)
    return digest


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


# ----------------------------------------------------------------------------------- extraction
EXTRACTORS = [
    ('unrar', [r'C:\Program Files\WinRAR\UnRAR.exe', r'C:\Program Files (x86)\WinRAR\UnRAR.exe', 'UnRAR.exe', 'unrar']),
    ('7z', [r'C:\Program Files\7-Zip\7z.exe', r'C:\Program Files (x86)\7-Zip\7z.exe', '7z.exe', '7z']),
    ('tar', [os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'tar.exe')]),  # bsdtar reads RAR
]


def find_extractor(preferred=None):
    for kind, candidates in EXTRACTORS:
        if preferred and kind != preferred:
            continue
        for c in candidates:
            path = c if os.path.isabs(c) and os.path.isfile(c) else shutil.which(c) if not os.path.isabs(c) else None
            if path:
                return kind, path
    return None


def list_members(archive, extractor):
    kind, exe = extractor
    if kind == 'unrar':
        out = _run([exe, 'lb', '-c-', str(archive)])
        names = out.splitlines()
        listing = _run([exe, 'lt', '-c-', str(archive)])
        if re.search(r'(?i)Type:.*(?:link|junction)|\bTarget:', listing):
            raise ToolError('archive contains links', DOWNLOAD)
        return [n for n in names if n.strip()]
    if kind == '7z':
        out = _run([exe, 'l', '-slt', '-ba', str(archive)])
        names = []
        for block in out.split('\n\n'):
            m = re.search(r'(?m)^Path = (.+)$', block)
            if m:
                if re.search(r'(?mi)^(?:Symbolic Link|Hard Link) = .+$', block):
                    raise ToolError('archive contains links', DOWNLOAD)
                names.append(m.group(1).strip())
        return names
    out = _run([exe, '-tf', str(archive)])
    return [n for n in out.splitlines() if n.strip()]


def extract(archive, dest, record, extractor, allowed_suffixes):
    """List, check, then extract into a NEW empty folder and re-check what landed there."""
    dest = Path(dest)
    names = list_members(archive, extractor)
    check_against_manifest(names, record, allowed_suffixes)
    if dest.exists() and any(dest.iterdir()):
        raise ToolError(f'extraction folder not empty: {dest}', DOWNLOAD)
    dest.mkdir(parents=True, exist_ok=True)
    kind, exe = extractor
    if kind == 'unrar':
        _run([exe, 'x', '-o-', '-y', '-c-', str(archive), str(dest) + os.sep])
    elif kind == '7z':
        _run([exe, 'x', '-y', '-snl-', f'-o{dest}', str(archive)])
    else:
        _run([exe, '-xf', str(archive), '-C', str(dest)])
    landed = []
    for p in dest.rglob('*'):
        if p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction()):
            raise ToolError(f'link extracted: {p}', DOWNLOAD)
        if p.is_file():
            landed.append(p.relative_to(dest).as_posix())
    want = {m.replace('\\', '/') for m in record['members']}
    if not set(landed) <= want:
        raise ToolError(f'unexpected extracted files {sorted(set(landed) - want)[:5]}', DOWNLOAD)
    return dest


def _run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, errors='replace')
    if r.returncode:
        raise ToolError(f'{Path(cmd[0]).name} failed ({r.returncode}): {r.stderr.strip()[:300]}', DOWNLOAD)
    return r.stdout


def platform_root(folder):
    """Packs that ship per-platform subfolders (Magma) use the XML2 PC one (the research choice)."""
    folder = Path(folder)
    sub = [d for d in folder.iterdir() if d.is_dir() and d.name.lower().startswith('for xml2 (pc')]
    return sub[0] if len(sub) == 1 else folder


def skin_file(folder, cel=True):
    """The pack's actor skin: '<NN>XX (Skin).igb' (cel, default) or '(Skin - No Cel).igb'."""
    want = '(skin).igb' if cel else '(skin - no cel).igb'
    hits = [p for p in platform_root(folder).rglob('*.igb') if p.name.lower().endswith(want)]
    if len(hits) != 1:
        raise ToolError(f'expected one {want} in {folder}, found {len(hits)}', MISSING_INPUT)
    return hits[0]
