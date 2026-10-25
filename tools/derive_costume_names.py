"""Write legends_models/data/costume-names.json from the games' own stat tables (read-only).

usage: py -3.12 tools/derive_costume_names.py --mua1 <MUA1 install> [--mua1-2006 <loose Data root>]
                                              --mua2 <MUA2 install> --xml2 <X-Men Legends II install>
Only the IDs the roster offers are written (name, character, kind and which file named them).
When two MUA1 editions are given they must agree.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from legends_models import costume_names, roster  # noqa: E402

TIERS = ('mua1', 'mua2', 'xml2')
SOURCE = {'mua1': 'MUA1 data/herostat.engb (heroes), data/npcstat.engb (story NPCs)',
          'mua2': 'MUA2 data/stats/heroes/*.xmlb, data/stats/npcs/*.xmlb',
          'xml2': 'X-Men Legends II Data/herostat.engb, Data/npcstat.engb'}


def offered():
    out = {t: set() for t in TIERS}
    for h in roster.HEROES:
        for t in TIERS:
            r = h.recipe(t)
            if r and r.source:
                out[t] |= set(r.alternatives) | {r.source}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--mua1', type=Path, required=True)
    ap.add_argument('--mua1-2006', type=Path)
    ap.add_argument('--mua2', type=Path, required=True)
    ap.add_argument('--xml2', type=Path, required=True)
    ap.add_argument('--out', type=Path, default=costume_names.DATA)
    a = ap.parse_args(argv)
    read = {'mua1': costume_names.read_mua1(a.mua1), 'mua2': costume_names.read_mua2(a.mua2),
            'xml2': costume_names.read_xml2(a.xml2)}
    if a.mua1_2006:
        other = costume_names.read_mua1(a.mua1_2006)
        diff = sorted(k for k in set(other) | set(read['mua1']) if other.get(k) != read['mua1'].get(k))
        if diff:
            print('MUA1 editions disagree on', diff[:20], file=sys.stderr)
    names, missing = {}, []
    for t, ids in offered().items():
        names[t] = {}
        for c in sorted(ids):
            row = read[t].get(c)
            if row is None:
                missing.append(f'{t}:{c}')
            else:
                names[t][c] = row
    doc = dict(comment='Costume names from each game\'s own data; made by tools/derive_costume_names.py',
               sources=SOURCE, names=names)
    a.out.write_text(json.dumps(doc, indent=2, sort_keys=True) + '\n', encoding='utf-8', newline='\n')
    print(f'wrote {a.out}: ' + ', '.join(f'{t} {len(v)}' for t, v in names.items()))
    if missing:
        print('not named by the games:', ', '.join(missing), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
