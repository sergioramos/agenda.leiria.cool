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
import connectors

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

# image quality: reject logos/banners/svg/data; reject the venue default; drop shared
t('good poster', core._good_img('https://x.pt/wp/poster.jpg'), True)
t('banner rejected', core._good_img('https://x.pt/img/banner-top.jpg'), False)
t('svg rejected', core._good_img('https://x.pt/brand.svg'), False)
t('default(listing) img rejected',
  core.scrape_event_page('<meta property="og:image" content="https://x.pt/cover.jpg">', 'https://x.pt/e', 'https://x.pt/cover.jpg').get('image'), None)
_share = [{'title': 'A', 'image': 'https://x/lg.png'}, {'title': 'B', 'image': 'https://x/lg.png'}, {'title': 'C', 'image': 'https://x/p.jpg'}]
core.drop_shared_images(_share)
t('shared image dropped', _share[0]['image'], None)
t('unique image kept', _share[2]['image'], 'https://x/p.jpg')

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

# ---- connectors: pure parsing helpers (no network) ----
t('topic music', connectors.map_topic('Concerto de fado ao vivo'), 'music')
t('topic art', connectors.map_topic('Exposição de pintura contemporânea'), 'art')
t('topic performance', connectors.map_topic('Espetáculo de teatro'), 'performance')
t('topic film', connectors.map_topic('Cinema ao ar livre'), 'film')
t('topic default', connectors.map_topic('Coisa indefinida', default='art'), 'art')

t('time 24h', connectors._time_from('21:00'), '21:00')
t('time h', connectors._time_from('às 21h'), '21:00')
t('time h30', connectors._time_from('21h30'), '21:30')
t('time none', connectors._time_from('entrada livre'), None)

t('strip html list', connectors._strip_html(['<p>Olá &amp; tal</p>']), 'Olá & tal')

# AgendaLX price: PHP-serialized value, free, unknown
t('alx price range', connectors._agendalx_price(
    {'price_cat': ['value'], 'price_val': ['a:1:{i:0;a:2:{s:5:"value";s:8:"8 € a 12";s:11:"description";s:1:"x";}}']})['text'], '€8–12')
t('alx price free', connectors._agendalx_price({'price_cat': ['free']})['is_free'], True)
t('alx price unknown', connectors._agendalx_price({'price_cat': ['unknown']})['text'], '')

# ---- persistent pool ----
_pool = {'events': {}, 'updated_at': None}
_e1 = {'id': 'aaa', 'title': 'X', 'start': '2026-06-25', 'end': None, 'source': 'agendalx'}
core.pool_upsert(_pool, [_e1], 'agendalx', '2026-06-21T06:00:00')
t('pool upsert', _pool['events']['aaa']['_first_seen'], '2026-06-21T06:00:00')
core.pool_upsert(_pool, [{**_e1, 'title': 'X2'}], 'agendalx', '2026-06-28T06:00:00')
t('pool refresh keeps first_seen', _pool['events']['aaa']['_first_seen'], '2026-06-21T06:00:00')
t('pool refresh updates data', _pool['events']['aaa']['title'], 'X2')
# expire: a past event drops, a future one stays
core.pool_upsert(_pool, [{'id': 'old', 'title': 'Past', 'start': '2026-06-01', 'end': '2026-06-02'}], 'ccb', 's')
removed = core.pool_expire(_pool, date(2026, 6, 22))
t('pool expires past', 'old' not in _pool['events'], True)
t('pool keeps future', 'aaa' in _pool['events'], True)
t('pool_events strips meta', all(not k.startswith('_') for e in core.pool_events(_pool) for k in e), True)

# ---- reframe_window: recompute days/ongoing from absolute dates ----
_span = {'id': 'r1', 'title': 'Expo', 'start': '2026-06-10', 'end': '2026-07-30', 'days': [], 'ongoing': False}
_after = {'id': 'r2', 'title': 'Future', 'start': '2026-08-01', 'end': None, 'days': [], 'ongoing': False}
_rf = core.reframe_window([_span, _after], date(2026, 6, 22), date(2026, 6, 28))
t('reframe drops out-of-window', [e['id'] for e in _rf], ['r1'])
t('reframe marks ongoing', _rf[0]['ongoing'], True)
t('reframe fills days', len(_rf[0]['days']), 7)

# ---- Phase 3: cross-source venue_key (coords) + source-priority merge ----
def evc(src, title, venue=None, lat=None, lng=None, url=None, price=None, lineup=None):
    e = ev(src, title, venue=venue, url=url, price=price)
    e['lat'], e['lng'] = lat, lng
    if lineup:
        e['lineup'] = lineup
    return e

t('coord key ~110m', core.event_coord_key({'lat': 38.7152, 'lng': -9.1448}), '38.715|-9.145')
t('coord key bad', core.event_coord_key({'lat': -37.0, 'lng': -64.0}), None)

# same coords + near title, DIFFERENT venue names/sources -> merge (venue_key win)
a = evc(HOT, 'Festival de Jazz', venue='Hot Clube de Portugal', lat=38.715, lng=-9.145)
b = evc(COL, 'Festival de Jazz', venue='Casa do Jazz', lat=38.7151, lng=-9.1452)
t('coord merge diff names', len(core.dedupe([a, b], SRCS)), 1)

# same title but DIFFERENT coords + names -> two different places, kept apart
a = evc(HOT, 'Concerto X', venue='Sala A', lat=38.71, lng=-9.14)
b = evc(COL, 'Concerto X', venue='Sala B', lat=38.72, lng=-9.16)
t('coord separation', len(core.dedupe([a, b], SRCS)), 2)

# links[] collects the aggregator's URL alongside the venue's own
a = evc(COL, 'Show', url='https://coliseu.pt/show')
b = ev(AGG, 'Show', venue='Coliseu dos Recreios', url='https://agendalx.pt/ev/show')
out = core.dedupe([a, b], SRCS)
t('merged to one', len(out), 1)
t('links carries agg url', out[0].get('links'), ['https://agendalx.pt/ev/show'])

# lineup carried from the copy that has it
a = evc(COL, 'Gig', url='https://coliseu.pt/gig')
b = ev(AGG, 'Gig', venue='Coliseu dos Recreios')
b['lineup'] = ['DJ Marfox', 'Nidia']
t('lineup carried', core.dedupe([a, b], SRCS)[0].get('lineup'), ['DJ Marfox', 'Nidia'])

# alias neighbourhood from address text
_aidx = core._alias_index(core.load_taxonomy())
t('alias neigh chiado', core.alias_neighbourhood('Rua Garrett, Chiado', _aidx)[0], 'Chiado')
t('alias neigh none', core.alias_neighbourhood('Rua Qualquer 5', _aidx)[0], None)
t('coord bbox ok', core.valid_lisbon_coord(38.72, -9.14), True)
t('coord bbox reject', core.valid_lisbon_coord(40.2, -8.4), False)

# ---- PT-wide JSON-LD listing (BOL): coord wins over the district locality ----
t('coord present', connectors._coord_present('38.72', '-9.14'), True)
t('coord absent', connectors._coord_present(None, None), False)
# in-bbox coord -> keep
t('listing lisbon coord', connectors._listing_in_lisbon(38.72, -9.14, 'lisboa'), True)
# out-of-area coord must NOT be rescued by the "Lisboa" district string (Torres Vedras)
t('listing tvedras drops', connectors._listing_in_lisbon(39.0902, -9.2589, 'lisboa'), False)
# no coord -> fall back to addressLocality
t('listing no-coord locality', connectors._listing_in_lisbon(None, None, 'lisboa'), True)
t('listing no-coord elsewhere', connectors._listing_in_lisbon(None, None, 'porto'), False)

# ---- JSON-LD offer pricing (per-offer, drop 0-fee, plausibility bound) ----
t('offer single', connectors._offers_price({'price': '20'})['text'], '€20')
t('offer lo-hi', connectors._offers_price({'lowPrice': '20', 'highPrice': '50'})['text'], '€20–50')
t('offer drops 0-fee', connectors._offers_price([{'price': '0'}, {'price': '25'}])['text'], '€25')
t('offer all-zero free', connectors._offers_price([{'price': '0'}, {'price': 0}])['is_free'], True)
t('offer bounds 9999', connectors._offers_price({'lowPrice': '20', 'highPrice': '9999'})['text'], '€20')
t('offer none', connectors._offers_price([])['text'], '')

# JSON-LD datetime: UTC 'Z' -> Europe/Lisbon (+1 summer), midnight = all-day
t('ld utc->lisbon', connectors._lisbon_dt('2026-08-22T14:00:00.000Z')[2], '2026-08-22T15:00')
t('ld offset kept', connectors._lisbon_dt('2026-06-24T22:00:00+01:00')[2], '2026-06-24T22:00')
t('ld midnight allday', connectors._lisbon_dt('2026-06-22T00:00:00.000')[1], False)

# ---- Ticketline microdata listing parser ----
_TL = ('<li itemscope itemtype="http://schema.org/Event">'
       '<a href="/evento/bbno-102837" itemprop="url">'
       '<div class="date" data-date="2026-06-24" itemprop="startDate" content="2026-06-24"></div>'
       '<img data-src-original="https://info.ticketline.pt/x/cartaz.jpg" itemprop="image"/>'
       '<div class="details"><p class="metadata categories">Música</p>'
       '<p class="title" itemprop="name">BBNO$</p>'
       '<p class="venues" itemprop="location">Lav - Lisboa Ao Vivo</p></div></a></li>')
_tlb = connectors._tl_blocks(_TL)
t('tl block parsed', len(_tlb), 1)
t('tl title', _tlb[0]['title'], 'BBNO$')
t('tl date', _tlb[0]['date'], '2026-06-24')
t('tl venue', _tlb[0]['venue'], 'Lav - Lisboa Ao Vivo')
t('tl url', _tlb[0]['url'], 'https://www.ticketline.pt/evento/bbno-102837')
t('tl far-town skip key', 'lourinha' in connectors._TL_FAR, True)

print(f'\n{ok} passed, {fail} failed')
sys.exit(1 if fail else 0)
