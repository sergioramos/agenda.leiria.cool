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
# chrome words must match as whole path/filename tokens, not coincidental substrings
t('img dialogo kept', core._good_img('https://x.pt/up/em-dialogo-com-lourdes.jpg'), True)
t('img catalogo kept', core._good_img('https://x.pt/up/catalogo-2026.jpg'), True)
t('img banners dir kept', core._good_img('https://images.xceed.me/events/banners/solomun-square.jpg?w=1920'), True)
t('img logo file rejected', core._good_img('https://x.pt/assets/logo.png'), False)
t('img favicon rejected', core._good_img('https://x.pt/favicon.ico'), False)
t('img drupal default-path kept', core._good_img('https://maat.pt/sites/default/files/2026-05/poster.jpg'), True)
t('img default file rejected', core._good_img('https://x.pt/img/default-cover.jpg'), False)
t('img logos dir rejected', core._good_img('https://x.pt/img/logos/patrimonio.png'), False)
t('img catalogos kept', core._good_img('https://x.pt/uploads/catalogos-2026.jpg'), True)
# content-image fallback: first same-site media/upload image, skipping logos
t('content-img picks media poster',
  core._content_image('<img src="/images/logo.png"><img src="https://s.pt/media/9972/poster.jpg?x=1">', 'https://s.pt/agenda/x'),
  'https://s.pt/media/9972/poster.jpg?x=1')
t('content-img none when only logo', core._content_image('<img src="/img/logo.png">', 'https://s.pt/x'), None)
# Elastic Beanstalk origin host (bad TLS) in og:image -> swap to the page's public host
t('eb host swapped', core._canonical_img(
    'https://visit-lisboa.eu-west-1.elasticbeanstalk.com/rails/active_storage/blobs/redirect/AbC/p.jpg',
    'https://visitlisboa.com/en/events/x'),
  'https://visitlisboa.com/rails/active_storage/blobs/redirect/AbC/p.jpg')
t('normal host untouched', core._canonical_img('https://images.xceed.me/events/banners/x.jpg', 'https://xceed.me/e'),
  'https://images.xceed.me/events/banners/x.jpg')

# venue recovery: read the real place from an event page's JSON-LD location, then
# canonicalise it against the venue directory (Visit Lisboa et al. need this)
_LDV = ('<script type="application/ld+json">{"@type":"Event","name":"X",'
        '"startDate":"2026-06-12T21:00","location":{"@type":"Place","name":"Casa Fernando Pessoa"}}</script>')
t('scrape ld venue', core.scrape_event_page(_LDV, 'https://x.pt/e').get('venue'), 'Casa Fernando Pessoa')
_VG = {core._nt('Casa Fernando Pessoa'): {'name': 'Casa Fernando Pessoa',
       'neighbourhood': 'Campo de Ourique', 'zone': 'city', 'lat': 38.71, 'lng': -9.16}}
t('canon venue name', core.canonical_venue('casa fernando pessoa', _VG, {})['venue'], 'Casa Fernando Pessoa')
t('canon venue neigh', core.canonical_venue('casa fernando pessoa', _VG, {})['neighbourhood'], 'Campo de Ourique')
t('canon venue unknown kept', core.canonical_venue('Albuquerque Foundation', _VG, {})['venue'], 'Albuquerque Foundation')
t('canon venue date none', core.canonical_venue('19–22 Nov 2026', _VG, {}), None)

# canonicalize_venues: exact match only (after stripping a sub-room/paren suffix);
# a loose substring must NEVER relabel a venue (the 'Lisboa' -> '@esnlisboa' trap)
_VG2 = {core._nt('Culturgest'): {'name': 'Culturgest', 'neighbourhood': 'Avenidas Novas', 'zone': 'city'},
        core._nt('@esnlisboa'): {'name': '@esnlisboa', 'neighbourhood': None, 'zone': None}}
_evs = [{'venue': 'Culturgest (auditoriums)'},      # paren suffix -> exact 'Culturgest'
        {'venue': '8 Marvila | Armazém 15-16', 'lat': None, 'lng': None},  # not in dir -> unchanged
        {'venue': 'Lisboa'},                          # must NOT become '@esnlisboa'
        {'venue': 'Culturgest'}]                      # already canonical -> unchanged
_n = core.canonicalize_venues(_evs, _VG2, {})
t('canon var fixed count', _n, 1)
t('canon var suffix->canon', _evs[0]['venue'], 'Culturgest')
t('canon var neigh filled', _evs[0].get('neighbourhood'), 'Avenidas Novas')
t('canon var no fuzzy', _evs[2]['venue'], 'Lisboa')
t('canon var already-canon untouched', _evs[3]['venue'], 'Culturgest')

# collapse_daily_runs: an exhibition returned one-per-day -> one ongoing span
_runs = [{'title': 'Expo X', 'venue': 'CCB', 'start': '2026-06-23', 'end': '2026-06-23'},
         {'title': 'Expo X', 'venue': 'CCB', 'start': '2026-06-24', 'end': '2026-06-24'},
         {'title': 'Expo X', 'venue': 'CCB', 'start': '2026-06-25', 'end': '2026-06-25'},
         {'title': 'Concerto Y', 'venue': 'CCB', 'start': '2026-06-23', 'end': '2026-06-23'}]
_col = core.collapse_daily_runs(_runs)
t('collapse run count', len(_col), 2)
_ex = [e for e in _col if e['title'] == 'Expo X'][0]
t('collapse span dates', (_ex['start'], _ex['end'], _ex['ongoing']), ('2026-06-23', '2026-06-25', True))
# overlapping spans of the same exhibition (two sources) -> one
t('collapse overlapping spans',
  len(core.collapse_daily_runs([{'title': 'E', 'venue': 'V', 'start': '2026-05-24', 'end': '2026-12-13'},
                                {'title': 'E', 'venue': 'V', 'start': '2026-06-28', 'end': '2026-12-13'}])), 1)
# a series with real gaps between dates stays separate
t('collapse keeps gapped series',
  len(core.collapse_daily_runs([{'title': 'S', 'venue': 'V', 'start': '2026-06-23', 'end': '2026-06-23'},
                                {'title': 'S', 'venue': 'V', 'start': '2026-06-27', 'end': '2026-06-27'}])), 2)

# canonicalize_venue_coords: same cell, similar names unify; a different venue stays
_vc = [{'venue': 'MACAM', 'lat': 38.7005, 'lng': -9.1831, 'title': 'A'},
       {'venue': 'MACAM - Museu de Arte Contemporânea Armando Martins', 'lat': 38.7005, 'lng': -9.1831, 'title': 'B'},
       {'venue': 'Museu de São Roque', 'lat': 38.7005, 'lng': -9.1831, 'title': 'C'}]
core.canonicalize_venue_coords(_vc)
t('coord-canon unifies macam', _vc[0]['venue'], 'MACAM - Museu de Arte Contemporânea Armando Martins')
t('coord-canon keeps distinct venue', _vc[2]['venue'], 'Museu de São Roque')

# title_core: strip trailing year/date so a festival's per-day titles group
t('title_core year', core.title_core("Rock in Rio Lisboa 2026"), core.title_core("Rock in Rio Lisboa"))
t('title_core date', core.title_core("Rock in Rio Lisboa – 27 de Junho"), core.title_core("Rock in Rio Lisboa"))
t('title_core keeps name', core.title_core("Carolina Estrela Trio"), 'carolinaestrelatrio')

# apply_venue_aliases: variant -> canonical + fills coords
_al = {core._nt('MAC/CCB'): ('Centro Cultural de Belém', 38.696, -9.208)}
_ae = [{'venue': 'MAC/CCB', 'lat': None, 'lng': None}]
core.apply_venue_aliases(_ae, _al)
t('alias rewrites venue', _ae[0]['venue'], 'Centro Cultural de Belém')
t('alias fills coords', round(_ae[0]['lat'], 3), 38.696)

# EventON microdata: per-event name/url/image; a non-image content attr is dropped
_EVO = ('<div data-event_id="1" itemscope itemtype="http://schema.org/Event">'
        '<div class="evo_event_schema" style="display:none">'
        '<a itemprop="url" href="https://hcp.pt/events/quinteto/"></a>'
        '<span itemprop="name">Quinteto Maria Joao Leite</span>'
        '<meta itemprop="image" content="https://hcp.pt/wp-content/uploads/2026/06/q.jpg"></div></div>'
        '<div class="evo_event_schema" style="display:none">'
        '<a itemprop="url" href="https://hcp.pt/events/escola/"></a>'
        '<span itemprop="name">Escola de Jazz</span>'
        '<meta itemprop="image" content="https://hcp.pt/events/escola/"></div></div>')
_md = core.eventon_events(_EVO)
t('eventon count', len(_md), 2)
t('eventon image kept', _md[0]['image'], 'https://hcp.pt/wp-content/uploads/2026/06/q.jpg')
t('eventon url', _md[0]['url'], 'https://hcp.pt/events/quinteto/')
t('eventon non-image dropped', _md[1]['image'], None)
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

# a title contained in another at the SAME venue/date is one event (series prefix)
_a = ev(AGG, 'Carolina Estrela Trio', venue='MACAM')
_b = ev(AGG, 'Jovens Talentos - Hot Clube > Carolina Estrela Trio', venue='MACAM')
t('containment merge same venue', len(core.dedupe([_a, _b], SRCS)), 1)
# ... but contained titles at DIFFERENT venues stay separate
_c = ev(AGG, 'Carolina Estrela Trio', venue='MACAM')
_d = ev(AGG, 'Jovens Talentos - Hot Clube > Carolina Estrela Trio', venue='Hot Clube')
t('containment keeps diff venue', len(core.dedupe([_c, _d], SRCS)), 2)

# pass 4: an ongoing run listed by 2 sources with different start dates at the
# same coordinate, contained titles -> one card (Designing Sustainable Futures)
_p4a = ev(AGG, 'Designing Sustainable Futures', venue='Pavilhao', start='2026-06-12', sd=date(2026, 6, 12), ed=date(2026, 7, 31))
_p4b = ev(COL, 'Exposicao Designing Sustainable Futures', venue='Pavilhao', start='2026-06-13', sd=date(2026, 6, 13), ed=date(2026, 7, 31))
_p4a['lat'], _p4a['lng'] = _p4b['lat'], _p4b['lng'] = 38.766, -9.095
t('pass4 cross-date same-coord merge', len(core.dedupe([_p4a, _p4b], SRCS)), 1)
# different coordinates (different places) stay separate even with contained titles
_p4c = ev(AGG, 'Designing Sustainable Futures', venue='A', start='2026-06-12', sd=date(2026, 6, 12), ed=date(2026, 7, 31))
_p4d = ev(COL, 'Exposicao Designing Sustainable Futures', venue='B', start='2026-06-13', sd=date(2026, 6, 13), ed=date(2026, 7, 31))
_p4c['lat'], _p4c['lng'] = 38.70, -9.10
_p4d['lat'], _p4d['lng'] = 38.75, -9.15
t('pass4 diff coord kept', len(core.dedupe([_p4c, _p4d], SRCS)), 2)

# on merge, a higher-authority source (ticketing) wins the topic over an
# aggregator's loose tag (exhibition wrongly tagged "learning")
TKT = {'id': 'ticketline', 'name': 'Ticketline', 'website': 'https://ticketline.pt', 'provider': 'ticketline', 'topic': 'guides', 'categories': []}
_tk = ev(TKT, 'Big Exhibition Title Here', venue='Pav'); _tk['topic'] = 'art'
_ag = ev(AGG, 'Big Exhibition Title Here', venue='Pav'); _ag['topic'] = 'learning'
t('topic from higher-authority source', core.dedupe([_tk, _ag], SRCS + [TKT])[0]['topic'], 'art')

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
# topic from the VENUE, not just the title (the music-bucket cleanup)
t('topic venue museum', connectors.map_topic('MAATuridades', 'MAAT'), 'art')
t('topic venue cinemateca', connectors.map_topic('Realizadoras Britânicas', 'Cinemateca'), 'film')
t('topic attraction oceanario', connectors.map_topic('Lisbon Oceanarium', 'Oceanário de Lisboa'), 'tours')
t('topic attraction hop-on', connectors.map_topic('Hop-on Hop-off Bus: unlimited travel', ''), 'tours')
t('topic visita orientada', connectors.map_topic('Visita Orientada', 'Casa Fernando Pessoa'), 'tours')
t('topic museum->art', connectors.map_topic('Museu Bordalo Pinheiro', 'Museu Bordalo Pinheiro'), 'art')
t('topic real concert stays music', connectors.map_topic('BBNO$', 'LAV - Lisboa Ao Vivo', default='music'), 'music')

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
# a clamped long run (start==mon, short tail) keeps an explicit ongoing=True...
_clamp = {'id': 'c1', 'title': 'Expo', 'start': '2026-06-22', 'end': '2026-06-26', 'days': [], 'ongoing': True}
t('reframe keeps explicit ongoing', core.reframe_window([_clamp], date(2026, 6, 22), date(2026, 6, 28))[0]['ongoing'], True)
# ...but a genuine single-day event is not marked ongoing
_short = {'id': 'c2', 'title': 'Show', 'start': '2026-06-25', 'end': '2026-06-25', 'days': [], 'ongoing': False}
t('reframe single-day not ongoing', core.reframe_window([_short], date(2026, 6, 22), date(2026, 6, 28))[0]['ongoing'], False)

# make_event ongoing override: a short run clamped to the window keeps em-curso
_oS = {'id': 's', 'name': 'S', 'website': 'https://s.pt', 'topic': 'art', 'categories': [1]}
_kw = dict(title='Expo', source=_oS, topic='art', mon=date(2026, 6, 22), window_end=date(2026, 9, 5),
           start_d=date(2026, 6, 22), end_d=date(2026, 6, 25), has_time=False, start_iso='2026-06-22',
           price={'is_free': False, 'min': None, 'currency': 'EUR', 'text': ''}, url=None,
           description='', language=['pt'], categories=[1])
t('make_event ongoing auto false', core.make_event(**_kw)['ongoing'], False)
t('make_event ongoing override', core.make_event(**_kw, ongoing=True)['ongoing'], True)

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

# ---- final AI review pass (deterministic parts; AI call mocked) ----
import review_events as _rv
_rv_tax = {"topics": [{"id": "art", "label": "Arte", "categories": [1]},
                      {"id": "music", "label": "Musica", "categories": [2]}]}
_rv_cfg = {"ai": {"model_cheap": "x", "review_confidence": 0.85, "enabled": True}}
def _mk(i, title, venue, start, end, topic):
    return {"id": i, "title": title, "venue": venue, "start": start, "end": end,
            "topic": topic, "days": ["mon"], "ongoing": True, "price": {}}
# candidate clustering links the two same-event copies, leaves the unrelated one
_evset = [_mk("a", "Designing Sustainable Futures", "ULisboa", "2026-06-22", "2026-07-31", "music"),
          _mk("b", "Exposição Designing Sustainable Futures", "Pavilhão", "2026-06-23", "2026-07-31", "art"),
          _mk("c", "Concerto X", "Y", "2026-06-24", "2026-06-24", "music")]
t('review clusters the residue', [[e['id'] for e in g] for g in _rv.candidate_clusters(_evset)], [['a', 'b']])
_orig_jc = extract.json_call
# apply ALL merges (no confidence gate) and record each reversibly
extract.json_call = lambda *a, **k: {"clusters": [{"cluster": 0, "merge": [
    {"members": [0, 1], "canonical": 1, "topic": "art", "confidence": 0.6}], "flags": []}]}
_kept, _chg, _st = _rv.review([dict(e) for e in _evset], "deepseek", None, _rv_cfg, _rv_tax, extract.CostTracker(1.0))
t('review applies all merges', sorted(e['id'] for e in _kept), ['b', 'c'])
t('review records removed for revert', _chg[0]['removed'][0]['id'], 'a')
t('review canonical keeps art', next(e['topic'] for e in _kept if e['id'] == 'b'), 'art')
t('review ai_ok true on success', _st['ai_ok'], True)
# a failed model call surfaces as ai_ok=False, not a silent "0 changes"
extract.json_call = lambda *a, **k: None
t('review ai_ok false on call failure',
  _rv.review([dict(e) for e in _evset], "deepseek", None, _rv_cfg, _rv_tax, extract.CostTracker(1.0))[2]['ai_ok'], False)
extract.json_call = lambda *a, **k: {"clusters": [{"cluster": 0, "merge": [
    {"members": [0, 1], "canonical": 1, "topic": "art", "confidence": 0.6}], "flags": []}]}
# a reverted signature is skipped (never re-merged)
_sig = _rv._cluster_sig([_evset[0], _evset[1]])
_k2, _c2, _s2 = _rv.review([dict(e) for e in _evset], "deepseek", None, _rv_cfg, _rv_tax, extract.CostTracker(1.0), {_sig})
t('review skips overridden sig', len(_k2), 3)
extract.json_call = _orig_jc

# headless-browser fallback host matching (the fetch itself needs Chromium)
import browser_fetch as _bf
_bfcfg = {"crawl": {"browser_hosts": ["tickettailor.com"]}}
t('needs_browser matches host', _bf.needs_browser('https://app.tickettailor.com/events/x/1', _bfcfg), True)
t('needs_browser ignores others', _bf.needs_browser('https://www.agendalx.pt/e', _bfcfg), False)

# geo-filter: drop out-of-area events (foreign / far-PT), keep greater-Lisbon ones
t('out-of-area: foreign venue', extract._out_of_area('De Kosterij, Leeuwarden', 'A Coldplay Candlelight'), True)
t('out-of-area: far-PT venue', extract._out_of_area('Igreja da Misericordia - Odemira', 'Miso String Quartet'), True)
t('out-of-area: title city', extract._out_of_area('Various Venues', 'Nottingham Cocktail Week 2026'), True)
t('out-of-area: keeps Lisbon', extract._out_of_area('Hot Clube de Portugal', 'Carolina Estrela Trio'), False)
t('out-of-area: keeps AML town', extract._out_of_area('Casino Estoril', 'Legado 2026'), False)
t('out-of-area: no substring trip', extract._out_of_area('Galeria Foco', 'Concerto'), False)

# Meetup connector: parse events from the embedded __NEXT_DATA__ (online ones dropped)
_MU_HTML = ('<script id="__NEXT_DATA__" type="application/json">{"p":{"events":['
            '{"__typename":"Event","id":"1","title":"Open Coffee Lisbon","dateTime":"2026-06-25T19:00:00+01:00",'
            '"eventUrl":"https://www.meetup.com/g/events/1/","eventType":"PHYSICAL","isOnline":false,'
            '"venue":{"name":"Defuse","city":"Lisbon","country":"PT"},'
            '"featuredEventPhoto":{"highResUrl":"https://secure.meetupstatic.com/photos/event/highres_1.jpeg"},'
            '"group":{"name":"Tech Lisbon"}},'
            '{"__typename":"Event","id":"2","title":"Online Webinar","dateTime":"2026-06-26T19:00:00+01:00",'
            '"eventUrl":"https://www.meetup.com/g/events/2/","eventType":"ONLINE","isOnline":true,'
            '"venue":{},"group":{"name":"X"}}]}}</script>')
_mu_orig = connectors._get_text
connectors._get_text = lambda *a, **k: _MU_HTML
_mu_c = next(c for c in connectors.CONNECTORS if c["id"] == "meetup")
_mu_evs, _mu_st = connectors._meetup(None, {"crawl": {}}, _mu_c,
                                     connectors._src(_mu_c, {"learning": 2, "guides": 1}),
                                     date(2026, 6, 22), date(2026, 9, 1), {}, {}, 0)
connectors._get_text = _mu_orig
t('meetup parses in-person only', len(_mu_evs), 1)
t('meetup event title', _mu_evs[0]['title'], 'Open Coffee Lisbon')
t('meetup keeps poster', bool(_mu_evs[0].get('image')), True)
t('meetup registered in fetchers', 'meetup' in connectors._FETCHERS, True)

print(f'\n{ok} passed, {fail} failed')
sys.exit(1 if fail else 0)
