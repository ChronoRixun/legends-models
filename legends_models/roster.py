# SPDX-License-Identifier: MIT
"""Port heroes and what each source tier can supply for them.

Facts only (actor IDs, animation databases, recipe status from the research
workspaces). No game asset is described beyond its file name and ID.

Recipe status, per tier and character:
  accepted  full gate passed and owner-accepted (numeric + offline + in-game)
  numeric   six-clip numeric gate passed in research; visual/in-game review pending
  failed    known to fail the gate with the current fitter (kept for reporting)
  none      the source game has no model for this character
"""
from __future__ import annotations

from dataclasses import dataclass, field

TIERS = ('mua2', 'mua1', 'xml2', 'community', 'sharpen')
TIER_NAMES = {'mua2': 'MUA2 model', 'mua1': 'MUA1 model', 'xml2': 'XML2 model',
              'community': 'Community pack (BaconWizard17)', 'sharpen': 'Sharpened original', 'stock': 'Stock'}
USABLE = ('accepted', 'numeric')


@dataclass(frozen=True)
class Recipe:
    tier: str
    source: str            # MUA/XML2 actor ID, or community costume name
    status: str
    note: str = ''
    alternatives: tuple = ()   # other costume IDs / names the engine accepts
    steps: tuple = ()          # engine steps beyond the default (documented, see engines/)
    accepted_sources: tuple = ()  # costumes whose output passed the FULL gate (owner-accepted)


@dataclass(frozen=True)
class Hero:
    key: str
    name: str
    skin: str              # port stock skin actor ID (Actors/<skin>.IGB)
    animation_db: str      # port animation database stem
    recipes: dict = field(default_factory=dict)
    select_id: str = ''     # character-select portrait id when it differs from the skin id (Professor X: 15101)
    villain: bool = False   # boss/enemy actor of the port (no playable slot); same skin-swap mechanics

    def recipe(self, tier):
        return self.recipes.get(tier)


def _h(key, name, skin, db, *recipes, villain=False, select_id=''):
    return Hero(key, name, skin, db, {r.tier: r for r in recipes}, select_id, villain)


R = Recipe
HEROES = [
    _h('beast', 'Beast', '14501', 'x1_05_beast',
       R('mua1', '', 'none', 'not in MUA1'),
       R('xml2', '0501', 'numeric', 'XML2 NPC donor; action retake pending'),
       R('community', 'Classic', 'numeric', 'safe candidate; native review pending', ('Classic',))),
    _h('colossus', 'Colossus', '14901', 'x1_09_colossus',
       R('mua2', '', 'none', 'MUA2 has Colossus; no converter recipe yet'),
       R('mua1', '7801', 'numeric', 'verified from the 2016 archives; sole fit applied by the engine'),
       R('xml2', '0901', 'numeric', 'hero donor'),
       R('community', 'Ultimate XML1', 'numeric', 'chain-refined safe candidate', ('Original', 'Ultimate XML1'),
         ('chain_refine',))),
    _h('cyclops', 'Cyclops', '14101', 'x1_01_cyclops',
       R('mua1', '', 'none', 'MUA1 has Dark Cyclops only; no recipe'),
       R('xml2', '0101', 'numeric', 'hero donor'),
       R('community', 'Ultimate XML1', 'numeric', 'chain-refined safe candidate', ('Classic', 'Ultimate XML1'),
         ('chain_refine',))),
    _h('frost', 'Emma Frost', '15501', '15_emmafrost',
       R('xml2', '9001', 'numeric', 'NPC dress, envelope fit; power retake pending'),
       R('community', 'Ultimate XML1', 'numeric', 'chain-refined safe candidate', ('Hellfire Club', 'Ultimate XML1'),
         ('chain_refine',))),
    _h('gambit', 'Gambit', '15301', 'x1_13_gambit',
       R('mua2', '0601', 'failed', 'coat lining crosses the outer coat; with shells coupled the refine has not yet reached stock stretch/chain AND zero layer crossings (models-cloth REPORT)'),
       R('xml2', '1301', 'numeric', 'mesh-only 0.998 scale'),
       R('community', 'Ultimate XML1', 'numeric', 'safe candidate', ('Original', 'Ultimate XML1'))),
    _h('iceman', 'Iceman', '14801', 'x1_08_iceman',
       R('mua1', '0801', 'numeric', 'numeric pass, native review pending'),
       R('xml2', '0801', 'numeric', 'mesh-only 0.998 scale'),
       R('community', 'Ultimate XML1', 'numeric', 'chain-refined safe candidate', ('Original', 'Ultimate XML1'),
         ('chain_refine',))),
    _h('jubilee', 'Jubilee', '15601', '16_jubilee',
       R('xml2', '', 'none', 'no independent XML2 donor (1601 is Banshee)'),
       R('community', 'Ultimate XML1', 'numeric', 'localized repair (repair_jubilee)', ('90s', 'Ultimate XML1'),
         ('repair_jubilee',))),
    _h('magma', 'Magma', '15801', '18_magma',
       R('xml2', '', 'none', 'no independent XML2 donor (1801 is Bishop)'),
       R('community', 'Ultimate XML1', 'numeric', 'authored seam repair (repair_magma)', ('Original', 'Ultimate XML1'),
         ('chain_refine', 'repair_magma'))),
    _h('nightcrawler', 'Nightcrawler', '14601', 'x1_06_nightcrawler',
       R('mua1', '4701', 'numeric', 'legacy 256 px NPC model; stock tail kept'),
       R('xml2', '0603', 'numeric', 'tail review pending', ('0601', '0603')),
       R('community', 'Ultimate XML1', 'numeric', 'chain-refined safe candidate', ('Original', 'Ultimate XML1'),
         ('chain_refine',))),
    _h('phoenix', 'Jean Grey (Phoenix)', '14201', '02_phoenix',
       R('mua1', '12301', 'numeric', 'long hair: loose-geometry falloff onto the back (layers.py); chain 8.36 vs stock 8.56'),
       R('xml2', '0201', 'numeric', 'vertex-envelope fit'),
       R('community', 'Ultimate XML1', 'numeric', 'chain-refined safe candidate', ('Phoenix', 'Ultimate XML1'),
         ('chain_refine',))),
    _h('profxastral', 'Professor X (astral)', '15104', '11_profxastral',
       R('mua1', '', 'none', 'MUA1 Professor X is the chair model; no recipe'),
       R('xml2', '1104', 'numeric', 'astral NPC donor'),
       R('community', 'Astral', 'numeric', 'chain-refined, mip-fixed; native 4-pose pass', ('Astral', 'Ultimate XML1'),
         ('chain_refine',)), select_id='15101'),
    _h('psylocke', 'Psylocke', '15201', '12_psylocke',
       R('mua1', '3005', 'numeric', 'Dark Psylocke NPC 3005 (7801 is Colossus, refused)'),
       R('xml2', '', 'none', 'no independent XML2 donor (1201 is Sunfire)'),
       R('community', 'Original', 'numeric', 'safe candidate', ('Original', 'Ultimate XML1'))),
    _h('rogue', 'Rogue', '14701', 'x1_07_rogue',
       R('xml2', '0703', 'numeric', 'replaces rejected 0701', ('0701', '0703')),
       R('community', 'Ultimate XML1', 'numeric', 'chain-refined safe candidate', ('Original', 'Ultimate XML1'),
         ('chain_refine',))),
    _h('storm', 'Storm', '14401', 'x1_04_storm',
       R('mua1', '0401', 'numeric', 'numeric pass with finesse1 clips'),
       R('xml2', '0401', 'numeric', 'cape review pending'),
       R('community', 'Original', 'numeric', 'RGBA8 texture path (rgba8_probe)', ('Original', 'Ultimate XML1'),
         ('rgba8',))),
    _h('wolverine', 'Wolverine', '14301', 'x1_03_wolverine',
       R('mua2', '0301', 'accepted', 'full gate pass 2026-10-24', accepted_sources=('0301',)),
       # Costumes: 0301 white shirt/jeans (its seam-safe fit is owner-accepted), 0302 brown/yellow,
       # 0303 classic yellow/blue = the default for the port's look (coordinator 2026-10-24). All three
       # pass the numeric gate in mua-models2 round 3.
       R('mua1', '0303', 'numeric', '0303 classic yellow/blue default; 0301 shirt/jeans (owner-accepted fit), 0302 brown/yellow',
         ('0301', '0302', '0303'), accepted_sources=('0301',)),
       R('xml2', '0301', 'numeric', 'claw review pending'),
       R('community', 'Original', 'numeric', 'chain-refined (mask fins) safe candidate', ('Original', 'Ultimate XML1'),
         ('chain_refine',))),
    # Bosses/villains: only the community tier (and the texture-only tier below) exists for them.
    _h('magneto', 'Magneto', '16501', 'x1_25_magneto',
       R('community', 'Ultimate XML1', 'numeric', 'chain-refined; native close-ups pass, red cape renders from the converted mesh',
         ('Classic', 'Ultimate XML1'), ('chain_refine',)), villain=True),
    _h('juggernaut', 'Juggernaut', '17401', 'x1_34_juggernaut',
       R('community', 'Classic', 'numeric', 'head/hood scaled to the port skeleton (repair_juggernaut_head), stock head/helmet segments hidden',
         ('Classic',), ('repair_juggernaut',)), villain=True),
    _h('sabretooth', 'Sabretooth', '16901', 'x1_29_sabretooth',
       R('community', 'Classic', 'numeric', 'safe candidate', ('Classic',)), villain=True),
    _h('toad', 'Toad', '17301', 'x1_33_toad',
       R('community', 'Classic', 'numeric', 'chain-refined; offline gate pass, only the scripted in-game pose captured', ('Classic',), ('chain_refine',)), villain=True),
    _h('pyro', 'Pyro', '17201', '32_pyro',
       R('community', 'Classic', 'numeric', 'chain-refined safe candidate', ('Classic',), ('chain_refine',)), villain=True),
]
# Every hero can take the texture-only tier.
HEROES = [Hero(h.key, h.name, h.skin, h.animation_db,
               {**h.recipes, 'sharpen': Recipe('sharpen', h.skin, 'numeric',
                                                'texture-only: 4x Lanczos + unsharp, mesh unchanged; loaded in game')},
               h.select_id, h.villain)
          for h in HEROES]
BY_KEY = {h.key: h for h in HEROES}
ALIASES = {'emma': 'frost', 'emmafrost': 'frost', 'jean': 'phoenix', 'jeangrey': 'phoenix', 'profx': 'profxastral',
           'professorx': 'profxastral', 'xavier': 'profxastral'}
# MUA1 converter character names (vendor/mua1/characters.py) differ for one hero.
MUA1_NAMES = {'phoenix': 'jean'}


def hero(name):
    key = ''.join(c for c in name.lower() if c.isalnum())
    key = ALIASES.get(key, key)
    if key not in BY_KEY:
        raise KeyError(f'unknown hero {name!r}; known: {", ".join(BY_KEY)}')
    return BY_KEY[key]
