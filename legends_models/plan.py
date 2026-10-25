# SPDX-License-Identifier: MIT
"""Per-character tier selection (pure; no file access).

Modes (spec-character-models.md, owner decision 2026-10-24):
  community  BaconWizard17 pack only (needs no other game)
  upgraded   per character the best of MUA2 > MUA1 > Community
  xml2       XML2-look fallback tier only
  sharpen    the port's own textures upscaled, mesh unchanged
A character whose tiers are all unusable stays stock (no file written for it).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import roster

MODES = {
    'community': ('community',),
    'upgraded': ('mua2', 'mua1', 'community'),
    'xml2': ('xml2',),
    'sharpen': ('sharpen',),
}
PACK_NAMES = {'community': 'Models-Community', 'upgraded': 'Models-Upgraded', 'xml2': 'Models-XML2-Look',
              'sharpen': 'Models-Sharpened'}


@dataclass
class Choice:
    tier: str
    source: str
    status: str
    accepted: bool
    reason: str


@dataclass
class Row:
    hero: str
    name: str
    skin: str
    chosen: Choice | None
    fallbacks: list = field(default_factory=list)   # later tiers tried by build if the chosen one fails the gate
    skipped: list = field(default_factory=list)     # (tier, reason)

    def as_dict(self):
        c = self.chosen
        return dict(hero=self.hero, name=self.name, skin=self.skin,
                    tier=c.tier if c else 'stock', source=c.source if c else None,
                    status=c.status if c else None, accepted=c.accepted if c else None,
                    reason=c.reason if c else '; '.join(f'{t}: {r}' for t, r in self.skipped) or 'no tier available',
                    fallbacks=[f.tier for f in self.fallbacks], skipped=[dict(tier=t, reason=r) for t, r in self.skipped])


def parse_costumes(items):
    """['wolverine=0303', 'storm=community:Ultimate XML1'] -> {(hero, tier|None): source}."""
    out = {}
    for item in items or ():
        if '=' not in item:
            raise ValueError(f'--costume expects hero=ID or hero=tier:ID, got {item!r}')
        hero, value = item.split('=', 1)
        key = roster.hero(hero.strip()).key
        tier = None
        if ':' in value and value.split(':', 1)[0] in roster.TIERS:
            tier, value = value.split(':', 1)
        out[(key, tier)] = value.strip()
    return out


def costume_for(hero, recipe, costumes):
    """The costume to build for this tier. A tier-qualified choice it does not offer is an error;
    an unqualified one (hero=ID) applies wherever it is offered, and returns None elsewhere."""
    tiered = costumes.get((hero.key, recipe.tier))
    want = tiered if tiered is not None else costumes.get((hero.key, None))
    if want is None:
        return recipe.source
    allowed = set(recipe.alternatives) | {recipe.source}
    match = next((a for a in allowed if a.casefold() == want.casefold()), None)
    if match is None:
        if tiered is None:
            return None
        raise ValueError(f'{hero.key}: costume {want!r} not offered by {recipe.tier} (choose from {sorted(allowed)})')
    return match


def make_plan(mode, available, heroes=None, costumes=None, strict=False, skip=()):
    """available(tier, hero, recipe, source) -> (ok, reason).
    strict: only costumes owner-accepted in research (Recipe.accepted_sources); otherwise accepted and
    numeric-pass recipes are candidates. The build re-runs the numeric gate on the player's own
    output either way, and reports owner acceptance per exact output hash (data/acceptance.json)."""
    if mode not in MODES:
        raise ValueError(f'unknown mode {mode!r}')
    costumes = costumes or {}
    rows = []
    for h in heroes or roster.HEROES:
        row = Row(h.key, h.name, h.skin, None)
        for tier in MODES[mode]:
            if tier in skip:
                row.skipped.append((tier, 'skipped (--skip-tier)'))
                continue
            r = h.recipe(tier)
            if r is None:
                row.skipped.append((tier, 'no recipe'))
                continue
            if r.status not in roster.USABLE:
                row.skipped.append((tier, f'{r.status}: {r.note}'))
                continue
            source = costume_for(h, r, costumes)
            if source is None:
                row.skipped.append((tier, 'requested costume not offered by this tier'))
                continue
            accepted = source in r.accepted_sources
            if strict and not accepted:
                row.skipped.append((tier, 'not owner-accepted (strict)'))
                continue
            ok, why = available(tier, h, r, source)
            if not ok:
                row.skipped.append((tier, why))
                continue
            choice = Choice(tier, source, r.status, accepted, why)
            if row.chosen is None:
                row.chosen = choice
            else:
                row.fallbacks.append(choice)
        rows.append(row)
    return rows


def table(rows):
    head = f'{"Hero":<22} {"Tier":<10} {"Source":<15} {"Status":<9} Reason / fallbacks'
    lines = [head, '-' * len(head)]
    for r in rows:
        d = r.as_dict()
        extra = d['reason'] + (f' (then: {", ".join(d["fallbacks"])})' if d['fallbacks'] else '')
        lines.append(f'{r.name:<22} {d["tier"]:<10} {str(d["source"] or "-"):<15} {str(d["status"] or "-"):<9} {extra}')
    return '\n'.join(lines)
