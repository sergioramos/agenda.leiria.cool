"""Offline regression battery for the crawler's normalisation + dedupe layer.
Every case here is a real bug that happened (or was caught in review).

  py crawler/test_pipeline.py     -> exit 0 when all pass
"""
import sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import core
import extract

ok = fail = 0
def t(label, got, want):
    global ok, fail
    good = got == want
    ok, fail = ok + good, fail + (not good)
    print(('PASS ' if good else 'FAIL '), label, '' if good else f' got={got!r} want={want!r}')

# titles
t('roman words', core.clean_title('GUERRA CIVIL EM CONCERTO'), 'Guerra Civil em Concerto')
t('roman numerals kept', core.clean_title('FESTIVAL DE ÓRGÃO XIV'), 'Festival de Órgão XIV')
t('kpop', core.clean_title('AS GUERREIRAS DO K-POP | TRIBUTO'), 'As Guerreiras do K-Pop – Tributo')

# _nt accent folding
t('nt accents', core._nt('Mosteiro dos Jerónimos'), core._nt('Mosteiro dos Jeronimos'))
t('nt cedilha', core._nt('Exposição'), 'exposicao')

# date + time join (events were coming out all-day because time was lost)
t('join date+time', extract._join_dt('2026-06-12', '21:00'), '2026-06-12T21:00')
t('join pads hour', extract._join_dt('2026-06-12', '9:30'), '2026-06-12T09:30')
t('join no time', extract._join_dt('2026-06-12', ''), '2026-06-12')
t('join bad time', extract._join_dt('2026-06-12', '25:00'), '2026-06-12')
t('join time overrides date-time', extract._join_dt('2026-06-12T20:00', '21:00'), '2026-06-12T21:00')
t('parse keeps time', core.parse_dt('2026-06-12T21:00')[1:], (True, '2026-06-12T21:00'))
t('parse no time', core.parse_dt('2026-06-12')[1], False)

# price_text from the model (bare number must parse — detect_price needed a €)
t('price bare number', core.parse_price('12')['text'], '€12')
t('price euro sign', core.parse_price('€15')['text'], '€15')
t('price range', core.parse_price('12-20')['text'], '€12–20')
t('price range "a"', core.parse_price('de 10 a 25 euros')['text'], '€10–25')
t('price free word', core.parse_price('entrada livre')['is_free'], True)
t('price empty', core.parse_price('')['text'], '')
t('price min set', core.parse_price('8,50')['min'], 8.5)

# scan_price: € on either side + ranges, from a whole event page
t('scan after-sign', core.scan_price('Bilhetes a 28€')['text'], '€28')
t('scan range a', core.scan_price('dos 28€ aos 40€')['text'], '€28–40')
t('scan ignores year', core.scan_price('edição de 2026'), None)
t('scan no free keyword', core.scan_price('newsletter grátis', allow_free=False), None)

# event-page scrape: JSON-LD price/image/time wins; og:image fallback; skip logo
_LD = ('<script type="application/ld+json">{"@type":"Event",'
       '"startDate":"2026-06-12T21:00:00","image":["https://x.pt/poster.jpg"],'
       '"offers":{"@type":"AggregateOffer","lowPrice":"28","highPrice":"40"}}</script>')
_si = core.scrape_event_page(_LD, 'https://x.pt/e')
t('scrape ld price', _si.get('price', {}).get('text'), '€28–40')
t('scrape ld image', _si.get('image'), 'https://x.pt/poster.jpg')
t('scrape ld time', _si.get('start_time'), '21:00')
t('scrape logo skipped', core.scrape_event_page('<meta property="og:image" content="https://x.pt/logo.svg">', 'https://x.pt/e').get('image'), None)

# descriptions
t('desc title prefix dropped', core.clean_description('Fado ao Vivo — noite de fado com jantar', 'Fado ao Vivo', 'A Severa'),
  'Noite de fado com jantar.')
t('desc venue suffix dropped', core.clean_description('Noite de fado com jantar — A Severa.', 'Fado ao Vivo', 'A Severa'),
  'Noite de fado com jantar.')
t('desc equal to title -> empty', core.clean_description('Fado ao Vivo', 'Fado ao Vivo', 'A Severa'), '')
t('desc html stripped', core.clean_description('<p>Concerto  de\nverão</p>', 'X', 'Y'), 'Concerto de verão.')
t('desc keeps period', core.clean_description('Já termina com ponto.', 'X', 'Y'), 'Já termina com ponto.')

# site_key robustness
t('site_key bad ipv6', core.site_key('https://[lisboa'), 'https://[lisboa')
t('site_key normal', core.site_key('https://www.zedosbois.org/'), 'zedosbois.org')

# resolve_url hardening
t('url junk words', core.resolve_url('ver bilhetes', 'https://venue.pt'), None)
t('url bracket copy', core.resolve_url('[https://outro.pt/ev/1]', 'https://venue.pt'), 'https://outro.pt/ev/1')
t('url bad bracket', core.resolve_url('//[x', 'https://venue.pt'), None)
t('url scheme junk', core.resolve_url('http://[bad', 'https://venue.pt'), None)
t('url relative', core.resolve_url('programa/ev-1', 'https://venue.pt/'), 'https://venue.pt/programa/ev-1')

# looks_like_date — new forms + venue safety
for name, want in [('31 Jul–9 Aug 2026', True), ('2026 dates TBC', True),
                   ('2026 across two June weekends (20, 21, 27, 28)', True),
                   ('19–22 Nov 2026', True), ('RDA 69', False), ('Bar 106', False),
                   ('SEM', False), ('Eka', False), ('Clube 2026?', False)]:
    t(f'date? {name!r}', core.looks_like_date(name), want)

# resolve_venue — ambiguous/generic rejected, strong matches kept
idx = core.venues_index(core.load_sources()['sources'])
t('venue generic Teatro', core.resolve_venue('Teatro', idx), None)
t('venue ambiguous Museu Nacional', core.resolve_venue('Museu Nacional', idx), None)
r = core.resolve_venue('Galeria Zé dos Bois', idx)
t('venue zdb', r and r['id'], 'zdb-galeria-ze-dos-bois')
r = core.resolve_venue('Coliseu dos Recreios', idx)
t('venue coliseu', r and r['name'], 'Coliseu dos Recreios')
r = core.resolve_venue('Mosteiro dos Jeronimos', idx)  # unaccented input
t('venue unaccented hit', bool(r), True)

# html_to_text: one bad anchor must not kill the rest
html = ('<html><body><nav>Menu principal</nav><main>'
        '<a href="/ev/1">Concerto especial</a> <a href="http://[">x broken</a> '
        '<a href="/ev/2">Outra noite legal</a></main></body></html>')
sink = set()
txt = core.html_to_text(html, 5000, base_url='https://v.pt', keep_links=True, link_sink=sink)
t('links survive bad anchor', ('[https://v.pt/ev/1]' in txt and '[https://v.pt/ev/2]' in txt), True)
t('nav still removed', 'Menu principal' not in txt, True)
t('link sink filled', sink, {'https://v.pt/ev/1', 'https://v.pt/ev/2'})

# ---- dedupe behaviour ----
AGG = {'id': 'agendalx', 'name': 'AgendaLX', 'website': 'https://agendalx.pt', 'topic': 'guides', 'categories': []}
HOT = {'id': 'hot-clube', 'name': 'Hot Clube de Portugal', 'website': 'https://hcp.pt', 'topic': 'music', 'categories': []}
COL = {'id': 'coliseu', 'name': 'Coliseu dos Recreios', 'website': 'https://coliseulisboa.com', 'topic': 'music', 'categories': []}
SRCS = [AGG, HOT, COL]
mon, wend = date(2026, 6, 8), date(2026, 6, 14)

def ev(src, title, venue=None, start='2026-06-12T21:00', sd=date(2026, 6, 12), ed=None,
       has_time=True, url=None, desc='', price=None):
    return core.make_event(title=title, source=src, topic='music', mon=mon, window_end=wend,
                           start_d=sd, end_d=ed, has_time=has_time, start_iso=start,
                           price=price or {'is_free': False, 'min': None, 'currency': 'EUR', 'text': ''},
                           url=url, description=desc, language=['pt'], categories=[],
                           venue_name=venue)

# same agenda page, same title, two extracted venues -> keep both
a = ev(AGG, 'Concerto de Natal', venue='Igreja de São Roque')
b = ev(AGG, 'Concerto de Natal', venue='Sé de Lisboa')
t('agenda page two venues kept', len(core.dedupe([a, b], SRCS)), 2)

# same agenda page, same title, no venue extracted (generic) -> merge
a = ev(AGG, 'Concerto de Natal')
b = ev(AGG, 'Concerto de Natal')
t('agenda generic merges', len(core.dedupe([a, b], SRCS)), 1)

# venue copy + aggregator copy with DIFFERENT resolved venue -> keep both
a = ev(HOT, 'Jam Session')
b = ev(AGG, 'Jam Session', venue='B.Leza', start='2026-06-12', has_time=False)
t('jam sessions both kept', len(core.dedupe([a, b], SRCS)), 2)

# venue copy + aggregator relist (generic label) -> merge, venue copy survives
a = ev(COL, 'As Guerreiras do K-Pop – Tributo')
b = ev(AGG, 'As Guerreiras do K-Pop – Tributo 2026')
out = core.dedupe([a, b], SRCS)
t('kpop merged', len(out), 1)
t('kpop venue survivor', out[0]['venue'], 'Coliseu dos Recreios')

# aggregator copy that matches the venue copy by extracted venue -> merge + url fill
a = ev(COL, 'Noite de Fado Maior')
b = ev(AGG, 'Noite de Fado Maior', venue='Coliseu dos Recreios', url='https://agendalx.pt/ev/fado-maior')
out = core.dedupe([a, b], SRCS)
t('venue-matched agg merged', len(out), 1)
t('url filled from agg copy', out[0]['url'], 'https://agendalx.pt/ev/fado-maior')

# absorb keeps the multi-day run when the single-day copy wins on score
a = ev(HOT, 'Exposição Tesouros da Ásia', start='2026-06-09', sd=date(2026, 6, 9),
       ed=date(2026, 6, 14), has_time=False)
b = ev(HOT, 'Exposição Tesouros da Ásia', start='2026-06-09T10:00', sd=date(2026, 6, 9),
       desc='Visita guiada às 10h, bilhetes no local.', price={'is_free': False, 'min': 6, 'currency': 'EUR', 'text': '€6'})
out = core.dedupe([a, b], SRCS)
t('exhibition merged', len(out), 1)
t('full run kept', (out[0]['end'], len(out[0]['days']), out[0]['ongoing']), ('2026-06-14', 6, True))
t('time kept too', out[0]['all_day'], False)

# junk-venue copy loses to a proper one
a = ev(HOT, 'Aulas de Swing', venue='24–28 Sep 2026')
b = ev(HOT, 'Aulas de Swing', venue=None, url='https://hcp.pt/aulas')
out = core.dedupe([a, b], SRCS)
t('junk venue loses', out[0]['venue'], 'Hot Clube de Portugal')

print(f'\n{ok} passed, {fail} failed')
sys.exit(1 if fail else 0)
