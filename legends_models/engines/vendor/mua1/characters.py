"""Verified local source IDs and unchanged Legends Classic actor targets."""
CHARACTERS={
    'wolverine':('0302','14301','x1_03_wolverine.IGB'),
    'storm':('0401','14401','x1_04_storm.IGB'),
    'iceman':('0801','14801','x1_08_iceman.IGB'),
    'nightcrawler':('4701','14601','x1_06_nightcrawler.IGB'),
    'jean':('12301','14201','02_phoenix.IGB'),
    # 7801 is Colossus in the supplied 2006 and 2016 inputs. NPCSTAT identifies
    # the actual MUA Dark Psylocke skin as 3005. Never silently relabel Colossus.
    'psylocke':('3005','15201','12_psylocke.IGB'),
    'colossus':('7801','14901','x1_09_colossus.IGB'),
}
FIGHT_STYLES={
    'x1_03_wolverine.IGB':'fightstyle_hero.IGB',
    'x1_04_storm.IGB':'fightstyle_finesse1.IGB',
    'x1_08_iceman.IGB':'fightstyle_hero.IGB',
    'x1_06_nightcrawler.IGB':'fightstyle_finesse1.IGB',
    '02_phoenix.IGB':'fightstyle_finesse1.IGB',
    '12_psylocke.IGB':'fightstyle_finesse1.IGB',
    'x1_09_colossus.IGB':'fightstyle_wrestling.IGB',
}

def check_identity(character,source_id):
    if character=='psylocke' and source_id.startswith('78'):
        raise ValueError('Source identity mismatch: 7801 is Colossus; MUA NPCSTAT identifies Dark Psylocke as 3005')
