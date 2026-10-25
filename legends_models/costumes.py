# SPDX-License-Identifier: MIT
"""Per-costume display data for the launcher's chooser (scan --json `costume_info`).

Each tier entry of a hero lists its costumes as {id, label, author, credit, source_url?, preview?}:
  id       what `--costume hero=tier:ID` takes (MUA/XML2 actor ID, or the community costume name)
  label    a short display name: the game's own costume name for MUA1/MUA2/XML2 models ("Astonishing",
           "Modern", "Story character model" / "Dark Psylocke" for story-character models; costume_names.py), the
           costume name for community skins ("Ultimate XML1")
  description  (game models, optional) what the costume looks like, where research described it
  author   the person credited for the costume (community skins), or '' for a model from a game the player owns
  credit   one plain line for the page ("by BaconWizard17 (marvelmods.com)" / "from your own copy of MUA1")
  preview  absolute path of a small PNG preview when one is already on this PC (the author's character-select
           portrait from an extracted community archive); omitted otherwise. Never downloaded for a scan.
"""
from __future__ import annotations

from pathlib import Path

from . import community, costume_names

GAME_CREDIT = {'mua1': 'from your own copy of Marvel: Ultimate Alliance',
               'mua2': 'from your own copy of Marvel: Ultimate Alliance 2',
               'xml2': "X-Men Legends II's own model (already in the port)",
               'sharpen': "the port's own textures, sharpened on this PC"}

# What a game costume looks like, where the research described it (the label is the game's own name).
DESCRIPTIONS = {('wolverine', 'mua1', '0303'): 'Classic yellow/blue',
                ('wolverine', 'mua1', '0301'): 'White shirt and jeans',
                ('wolverine', 'mua1', '0302'): 'Brown/yellow',
                ('nightcrawler', 'mua1', '4701'): 'Classic look (MUA1 story character)',
                ('phoenix', 'mua1', '12301'): 'Jean Grey as MUA1 story character'}
TIER_WORD = {'mua1': 'MUA1', 'mua2': 'MUA2', 'xml2': 'XML2'}


def _slug(s):
    return s.lower().replace(' ', '-')


def community_preview(port_work, hero_key, costume):
    """The author's character-select portrait PNG of an already extracted archive, if any (no download)."""
    if port_work is None:
        return None
    folder = Path(port_work) / 'community' / 'extracted' / _slug(community.KEY_TO_NAME[hero_key]) / _slug(costume)
    if not (folder / '.verified').is_file():
        return None
    hits = sorted(p for p in community.platform_root(folder).rglob('*.png')
                  if p.name.lower().endswith('(character select portrait).png'))
    return str(hits[0]) if hits else None


def tier_author(tier):
    return community.manifest()['author'] if tier == 'community' else ''


def game_label(hero, tier, costume, names=None):
    """Display name of a game model: the game's own name (player's install, else the shipped table)."""
    if tier == 'sharpen':
        return 'Sharpened original'
    label = costume_names.label(hero.name, tier, costume, names)
    return label or f'{TIER_WORD.get(tier, tier)} model {costume}'


def info(hero, tier, ids, port_work=None, data=None, names=None):
    """[{id, label, author, credit, description?, source_url?, preview?}] for these costume IDs of one hero/tier.

    names: costume_names.from_installs(...) of the scanned installs (optional; the shipped table otherwise)."""
    out = []
    if tier == 'community':
        data = data or community.manifest()
        author = data['author']
        for c in ids:
            row = dict(id=c, label=c, author=author, credit=f'by {author} (marvelmods.com)')
            try:
                rec = community.entry(hero.key, c, data)
                row.update(source_url=rec['post'], bytes=rec['bytes'])
            except Exception:  # noqa: BLE001 - a costume without an archive record still gets a label
                pass
            p = community_preview(port_work, hero.key, c)
            if p:
                row['preview'] = p
            out.append(row)
        return out
    for c in ids:
        row = dict(id=c, label=game_label(hero, tier, c, names), author='', credit=GAME_CREDIT.get(tier, ''))
        desc = DESCRIPTIONS.get((hero.key, tier, c))
        if desc:
            row['description'] = desc
        out.append(row)
    return out
