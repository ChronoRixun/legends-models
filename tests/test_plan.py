# SPDX-License-Identifier: MIT
import unittest

from legends_models import plan, roster


def all_available(tier, hero, recipe, source):
    return True, 'ok'


def only(*tiers):
    return lambda tier, hero, recipe, source: (tier in tiers, 'ok' if tier in tiers else 'not installed')


def row(rows, key):
    return next(r for r in rows if r.hero == key).as_dict()


class Plan(unittest.TestCase):
    def test_upgraded_prefers_mua2_then_mua1_then_community(self):
        rows = plan.make_plan('upgraded', all_available)
        w = row(rows, 'wolverine')
        self.assertEqual((w['tier'], w['source']), ('mua2', '0301'))
        self.assertEqual(w['fallbacks'], ['mua1', 'community'])
        self.assertEqual(row(rows, 'storm')['tier'], 'mua1')
        self.assertEqual(row(rows, 'beast')['tier'], 'community')

    def test_failed_and_missing_recipes_are_skipped(self):
        rows = plan.make_plan('upgraded', all_available)
        g = row(rows, 'gambit')          # MUA2 Gambit is a known visual failure
        self.assertEqual(g['tier'], 'community')
        self.assertTrue(any(s['tier'] == 'mua2' and s['reason'].startswith('failed') for s in g['skipped']))
        j = row(rows, 'phoenix')         # MUA1 Jean: loose-geometry rule passes the chain gate (layers.py)
        self.assertEqual((j['tier'], j['source']), ('mua1', '12301'))
        p = row(rows, 'profxastral')     # only the community pack is usable
        self.assertEqual(p['tier'], 'community')

    def test_without_other_games_upgraded_falls_to_community(self):
        rows = plan.make_plan('upgraded', only('community'))
        self.assertTrue(all(r.as_dict()['tier'] in ('community', 'stock') for r in rows))
        self.assertEqual(row(rows, 'wolverine')['tier'], 'community')

    def test_single_tier_modes(self):
        for mode, tier in [('community', 'community'), ('xml2', 'xml2'), ('sharpen', 'sharpen')]:
            rows = plan.make_plan(mode, all_available)
            self.assertTrue(all(r.as_dict()['tier'] in (tier, 'stock') for r in rows), mode)
        sharp = plan.make_plan('sharpen', all_available)
        self.assertEqual(sum(r.chosen is not None for r in sharp), len(roster.HEROES))
        x = plan.make_plan('xml2', all_available)
        self.assertEqual({r.hero for r in x if r.chosen is None}, {'jubilee', 'magma', 'psylocke', 'magneto', 'sabretooth', 'toad', 'pyro', 'juggernaut'})

    def test_strict_keeps_only_owner_accepted(self):
        rows = plan.make_plan('upgraded', all_available, strict=True)
        chosen = {r.hero: r.as_dict() for r in rows if r.chosen}
        self.assertEqual(set(chosen), {'wolverine'})
        self.assertTrue(chosen['wolverine']['accepted'])
        rows = plan.make_plan('upgraded', all_available, strict=True, skip=('mua2',),
                              costumes=plan.parse_costumes(['wolverine=mua1:0301']))
        self.assertEqual((row(rows, 'wolverine')['tier'], row(rows, 'wolverine')['source']), ('mua1', '0301'))

    def test_costumes(self):
        default = row(plan.make_plan('upgraded', all_available, skip=('mua2',)), 'wolverine')
        self.assertEqual(default['source'], '0303')                    # classic yellow/blue by default
        c = plan.parse_costumes(['wolverine=0302', 'Storm=community:Ultimate XML1', 'emma=community:Hellfire Club'])
        self.assertEqual(c[('wolverine', None)], '0302')
        self.assertEqual(c[('storm', 'community')], 'Ultimate XML1')
        rows = plan.make_plan('upgraded', all_available, costumes=c, skip=('mua2',))
        self.assertEqual(row(rows, 'wolverine')['source'], '0302')
        rows = plan.make_plan('community', all_available, costumes=c)
        self.assertEqual(row(rows, 'storm')['source'], 'Ultimate XML1')
        self.assertEqual(row(rows, 'frost')['source'], 'Hellfire Club')
        with self.assertRaises(ValueError):
            plan.make_plan('upgraded', all_available, costumes=plan.parse_costumes(['wolverine=mua1:9999']))
        with self.assertRaises(ValueError):
            plan.parse_costumes(['wolverine'])
        with self.assertRaises(KeyError):
            plan.parse_costumes(['deadpool=0101'])

    def test_availability_receives_the_chosen_costume(self):
        seen = []
        plan.make_plan('upgraded', lambda t, h, r, s: (seen.append((h.key, t, s)), (False, 'x'))[1],
                       heroes=[roster.hero('wolverine')], costumes=plan.parse_costumes(['wolverine=mua1:0303']))
        self.assertIn(('wolverine', 'mua1', '0303'), seen)

    def test_unknown_mode(self):
        with self.assertRaises(ValueError):
            plan.make_plan('best', all_available)

    def test_table_renders_every_hero(self):
        text = plan.table(plan.make_plan('upgraded', all_available))
        for h in roster.HEROES:
            self.assertIn(h.name, text)


class Roster(unittest.TestCase):
    def test_roster_is_consistent(self):
        self.assertEqual(len(roster.HEROES), 20)
        self.assertEqual(sum(h.villain for h in roster.HEROES), 5)
        self.assertEqual(len({h.skin for h in roster.HEROES}), 20)
        for h in roster.HEROES:
            self.assertIn('sharpen', h.recipes)
            for r in h.recipes.values():
                self.assertIn(r.status, ('accepted', 'numeric', 'failed', 'none'))
                self.assertTrue(set(r.accepted_sources) <= set(r.alternatives) | {r.source})
                if r.status != 'none' and r.tier != 'sharpen':
                    self.assertTrue(r.source)

    def test_aliases(self):
        self.assertIs(roster.hero('Emma'), roster.BY_KEY['frost'])
        self.assertIs(roster.hero('Jean Grey'), roster.BY_KEY['phoenix'])
        self.assertIs(roster.hero('professor x'), roster.BY_KEY['profxastral'])


if __name__ == '__main__':
    unittest.main()
