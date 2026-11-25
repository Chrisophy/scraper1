# -*- coding: utf-8 -*-
# Frei Haus
# zusatzmetall | IT('s) Possible
# 2026.11.25
import base64
import gzip
import hashlib
import html
import importlib.util
import inspect
import json
import os
import re
import sys
import tempfile
import threading
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, unquote, urlsplit

from resources.lib import multiquest, log

SITE_ID       = 'freihaus'
SITE_NAME     = 'Frei Haus'
SITE_DOMAIN   = 'raw.githubusercontent.com'
TYPE          = 'both'
GLOBAL_SEARCH = True

REPO = 'Chrisophy/findall'
ZWEIG = 'daten'
PRUEF_STUNDEN = 6
SEITE = 100
NEU_TAGE = 7
MAX_SUCHE = 200
_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
       '(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36')

_LOCK = threading.RLock()
_D = {'index': None, 'geprueft': 0.0}
_LISTEN = {}
_IDS = {}
_HEU = {}
_NORM = {}
_ERSATZ = {}
_MODS = {}
_VERF = {'t': 0.0, 'ok': set()}
_MARKUP = re.compile(r'\[/?(?:B|I|U|LIGHT|UPPERCASE|LOWERCASE|CAPITALIZE|COLOR[^\]]*|CR)\]', re.I)
_SUCHTITEL = re.compile(r'^\W*(?:suche|suchen|search|globale suche|suche nach)\b', re.I)
_OHNE = {'search', 'do_search', 'clear_cache', 'nothing', 'settings', 'ms_settings', 'toggle'}
_ARTIKEL = re.compile(r'^(?:der|die|das|the|a|an|ein|eine|le|la|les|el)\s+')
_ART = {'f': 'filme', 's': 'serien', 'l': 'live'}


def _repo():
    try:
        import xbmcaddon
        v = (xbmcaddon.Addon().getSetting('freikatalog.repo') or '').strip().strip('/')
        if v.count('/') == 1:
            return v
    except Exception:
        pass
    return REPO


def _basis():
    return 'https://raw.githubusercontent.com/%s/%s/katalog/' % (_repo(), ZWEIG)


def _ordner():
    p = ''
    try:
        import xbmcaddon
        import xbmcvfs
        p = xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo('profile'))
    except Exception:
        p = ''
    if not p:
        p = tempfile.gettempdir()
    p = os.path.join(p, 'freihaus')
    try:
        os.makedirs(p, exist_ok=True)
    except Exception:
        p = os.path.join(tempfile.gettempdir(), 'freihaus')
        os.makedirs(p, exist_ok=True)
    return p


def _pfad(name):
    return os.path.join(_ordner(), name)


def _hinweis(text):
    try:
        import xbmcgui
        xbmcgui.Dialog().notification(SITE_NAME, text, xbmcgui.NOTIFICATION_INFO, 3000, False)
    except Exception:
        pass


def _sauber(t):
    if t is None:
        return ''
    s = re.sub(r'\[CR\]', ' ', str(t), flags=re.I)
    return re.sub(r'\s+', ' ', html.unescape(_MARKUP.sub('', s))).strip()


def _falten(t):
    s = str(t or '').lower()
    for a, b in (('ä', 'ae'), ('ö', 'oe'), ('ü', 'ue'), ('ß', 'ss')):
        s = s.replace(a, b)
    s = unicodedata.normalize('NFKD', s)
    return ''.join(c for c in s if not unicodedata.combining(c))


def _norm(t):
    s = _falten(_sauber(t))
    s = re.sub(r'\[[^\]]*\]|\([^)]*\)', ' ', s).strip()
    s = _ARTIKEL.sub('', s)
    n = re.sub(r'[^a-z0-9]+', '', s)
    return n or re.sub(r'\s+', '', _falten(_sauber(t)))


def _heu(t):
    return re.sub(r'[^a-z0-9]+', '', _falten(_sauber(t)))


def _zahl(n):
    return '{:,}'.format(int(n or 0)).replace(',', '.')


def _int(v):
    try:
        return int(float(v or 0))
    except Exception:
        return 0


def _lies_datei(p):
    try:
        with open(p, 'rb') as f:
            return f.read()
    except Exception:
        return None


def _schreib_datei(p, daten):
    try:
        tmp = p + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(daten)
        os.replace(tmp, p)
        return True
    except Exception:
        return False


def _hole(name, timeout=30):
    try:
        r = multiquest.get(_basis() + name, headers={'User-Agent': _UA, 'Cache-Control': 'no-cache'}, timeout=timeout)
        if r.status_code == 200 and r.content:
            return r.content
        log.log('[Frei Haus] %s HTTP %s' % (name, r.status_code), log.LOGWARNING)
    except Exception as e:
        log.log('[Frei Haus] %s: %s' % (name, e), log.LOGWARNING)
    return None


def _index(erzwingen=False):
    with _LOCK:
        p = _pfad('index.json')
        if _D['index'] is None:
            roh = _lies_datei(p)
            if roh:
                try:
                    _D['index'] = json.loads(roh.decode('utf-8'))
                except Exception:
                    _D['index'] = None
            try:
                _D['geprueft'] = os.path.getmtime(p)
            except Exception:
                _D['geprueft'] = 0.0
        warte = PRUEF_STUNDEN * 3600 if _D['index'] else 60
        if erzwingen or time.time() - _D['geprueft'] > warte:
            roh = _hole('index.json', 20)
            _D['geprueft'] = time.time()
            if roh:
                try:
                    neu = json.loads(roh.decode('utf-8'))
                    if isinstance(neu, dict) and neu.get('dateien'):
                        _D['index'] = neu
                        _schreib_datei(p, roh)
                except Exception:
                    pass
            try:
                if os.path.exists(p):
                    os.utime(p, None)
            except Exception:
                pass
        return _D['index'] or {}


def _liste(art):
    idx = _index()
    sha = ((idx.get('dateien') or {}).get(art) or {}).get('sha') or ''
    with _LOCK:
        c = _LISTEN.get(art)
        if c is not None and (c[0] == sha or not sha):
            return c[1]
        p = _pfad(art + '.json.gz')
        ps = _pfad(art + '.sha')
        roh = None
        merk = (_lies_datei(ps) or b'').decode('ascii', 'ignore').strip()
        if sha and merk == sha:
            roh = _lies_datei(p)
        if roh is None and sha:
            roh = _hole(art + '.json.gz', 60)
            if roh:
                _schreib_datei(p, roh)
                _schreib_datei(ps, sha.encode('ascii'))
        if roh is None:
            roh = _lies_datei(p)
        daten = []
        if roh:
            try:
                daten = json.loads(gzip.decompress(roh).decode('utf-8'))
            except Exception as e:
                log.log('[Frei Haus] %s defekt: %s' % (art, e), log.LOGWARNING)
                daten = []
        _LISTEN[art] = (sha, daten)
        _IDS.pop(art, None)
        _HEU.pop(art, None)
        _NORM.pop(art, None)
        return daten


def _nach_id(art):
    liste = _liste(art)
    with _LOCK:
        c = _IDS.get(art)
        if c is None or c[0] is not liste:
            c = (liste, {e.get('i'): e for e in liste})
            _IDS[art] = c
        return c[1]


def _heuhaufen(art):
    liste = _liste(art)
    with _LOCK:
        c = _HEU.get(art)
        if c is None or c[0] is not liste:
            c = (liste, [_heu(e.get('t')) for e in liste])
            _HEU[art] = c
        return c[1]


def _normen(art):
    liste = _liste(art)
    with _LOCK:
        c = _NORM.get(art)
        if c is None or c[0] is not liste:
            c = (liste, [_norm(e.get('t')) for e in liste])
            _NORM[art] = c
        return c[1]


def _quellen():
    return (_index().get('quellen') or {})


def _qname(sid):
    return (_quellen().get(sid) or {}).get('n') or sid


def _stamm(sid):
    return (_quellen().get(sid) or {}).get('f') or sid


def _registry():
    return sys.modules.get('engine.registry')


def _modul_suchen(sid):
    reg = _registry()
    if reg is not None:
        try:
            p = getattr(reg, 'REGISTRY', {}).get(sid)
            if p is not None:
                if getattr(p, 'enabled', True) is False:
                    return None, True
                if getattr(p, 'module', None) is not None:
                    return p.module, True
        except Exception:
            pass
    stamm = _stamm(sid)
    m = sys.modules.get('ms_scraper_' + stamm)
    if m is not None and m is not sys.modules.get(__name__):
        return m, True
    for m in list(sys.modules.values()):
        try:
            if m is not None and getattr(m, 'SITE_ID', None) == sid and sid != SITE_ID and callable(getattr(m, 'get_hosters', None)):
                return m, True
        except Exception:
            continue
    return None, False


def _datei_von(sid):
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), _stamm(sid) + '.py')
        return p if os.path.isfile(p) else ''
    except Exception:
        return ''


def _vorhanden(sid):
    m, sicher = _modul_suchen(sid)
    if m is not None:
        return True
    if sicher:
        return False
    return bool(_datei_von(sid))


def _anbieter(sid):
    with _LOCK:
        c = _MODS.get(sid)
        if c is not None and time.time() - c[1] < 60:
            return c[0]
    m, sicher = _modul_suchen(sid)
    if m is None and not sicher:
        p = _datei_von(sid)
        if p:
            try:
                stamm = _stamm(sid)
                spec = importlib.util.spec_from_file_location('ms_scraper_' + stamm, p)
                m = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(m)
                sys.modules['ms_scraper_' + stamm] = m
            except Exception as e:
                log.log('[Frei Haus] %s nicht ladbar: %s' % (sid, e), log.LOGWARNING)
                m = None
    with _LOCK:
        _MODS[sid] = (m, time.time())
    return m


def _verfuegbar():
    with _LOCK:
        if time.time() - _VERF['t'] < 60:
            return _VERF['ok']
    ok = set()
    for sid in _quellen():
        try:
            if sid != SITE_ID and _vorhanden(sid):
                ok.add(sid)
        except Exception:
            continue
    with _LOCK:
        _VERF['t'], _VERF['ok'] = time.time(), ok
    return ok


def _nutzbare(e, ok=None):
    ok = _verfuegbar() if ok is None else ok
    return [q for q in e.get('q') or [] if q and (q[0] in ok or (len(q) > 3 and q[3]))]


def _gefiltert(liste):
    ok = _verfuegbar()
    return [e for e in liste if _nutzbare(e, ok)]


def _packen(d):
    roh = json.dumps(d, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return base64.urlsafe_b64encode(roh).decode('ascii').rstrip('=')


def _entpacken(s):
    s = str(s or '')
    s += '=' * (-len(s) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(s.encode('ascii')).decode('utf-8'))
    except Exception:
        return {}


def _ordner_item(titel, url, plot='', poster=''):
    return {'title': titel, 'url': url, 'plot': plot, 'poster': poster, 'is_playable': False, 'next_func': 'load'}


def _quellen_text(qs):
    namen = []
    for q in qs:
        n = _qname(q[0])
        if n not in namen:
            namen.append(n)
    return 'Quellen: ' + ', '.join(namen) if namen else ''


def _film_item(e, ok):
    qs = _nutzbare(e, ok)
    if not qs:
        return None
    plot = e.get('p') or ''
    qt = _quellen_text(qs)
    it = {'title': e.get('t') or '', 'url': 'f:' + str(e.get('i')), 'year': str(e.get('j') or ''),
          'plot': (plot + '\n\n' + qt) if plot else qt, 'poster': e.get('b') or '', 'fanart': e.get('f') or e.get('b') or '',
          'mediatype': 'movie', 'is_playable': True, 'next_func': 'get_hosters'}
    if e.get('g'):
        it['genre'] = ', '.join(e['g'])
    if e.get('d'):
        it['duration'] = e['d']
    return it


def _serie_item(e, ok):
    qs = _nutzbare(e, ok)
    if not qs:
        return None
    plot = e.get('p') or ''
    qt = _quellen_text(qs)
    it = {'title': e.get('t') or '', 'url': 's:' + str(e.get('i')), 'year': str(e.get('j') or ''),
          'plot': (plot + '\n\n' + qt) if plot else qt, 'poster': e.get('b') or '', 'fanart': e.get('f') or e.get('b') or '',
          'mediatype': 'tvshow', 'is_playable': False, 'next_func': 'load'}
    if e.get('g'):
        it['genre'] = ', '.join(e['g'])
    return it


def _live_item(e, ok):
    qs = _nutzbare(e, ok)
    if not qs:
        return None
    return {'title': e.get('t') or '', 'url': 'l:' + str(e.get('i')), 'plot': _quellen_text(qs),
            'poster': e.get('b') or '', 'icon': e.get('b') or '', 'mediatype': 'video', 'is_playable': True, 'next_func': 'get_hosters'}


def _item(art, e, ok):
    if art == 'filme':
        return _film_item(e, ok)
    if art == 'serien':
        return _serie_item(e, ok)
    return _live_item(e, ok)


def _seite(art, liste, basis, seite):
    ok = _verfuegbar()
    gesamt = len(liste)
    start = max(0, seite) * SEITE
    out = []
    for e in liste[start:start + SEITE]:
        it = _item(art, e, ok)
        if it:
            out.append(it)
    if start + SEITE < gesamt:
        seiten = (gesamt + SEITE - 1) // SEITE
        out.append(_ordner_item('[B]Nächste Seite »[/B]  (%d von %d)' % (seite + 2, seiten), '%s:%d' % (basis, seite + 1),
                                '%s weitere Einträge' % _zahl(gesamt - start - SEITE)))
    if not out:
        out.append(_ordner_item('Keine Einträge', 'stand', 'Für diese Auswahl gibt es gerade nichts.'))
    return out


def _buchstabe(t):
    s = re.sub(r'^[^a-z0-9]+', '', _falten(_sauber(t)))
    if not s:
        return '#'
    c = s[0]
    if c.isdigit():
        return '0-9'
    return c.upper() if 'a' <= c <= 'z' else '#'


def _stand_text():
    idx = _index()
    st = idx.get('stand') or ''
    try:
        d = datetime.strptime(st, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc).astimezone()
        return d.strftime('%d.%m.%Y %H:%M')
    except Exception:
        return st or 'unbekannt'


def _haupt():
    idx = _index()
    if not idx or not idx.get('dateien'):
        return [_ordner_item('[B]Katalog nicht erreichbar[/B] – erneut versuchen', 'neu_laden',
                             'Die Liste konnte nicht geladen werden. Internet prüfen oder später erneut versuchen.\nQuelle: ' + _basis())]
    z = idx.get('zahlen') or {}
    return [
        _ordner_item('Neu (letzte %d Tage)' % NEU_TAGE, 'neu:0', 'Filme und Serien, die neu im Katalog sind.'),
        _ordner_item('Filme', 'filme', '%s Filme aus allen freien Quellen.' % _zahl(z.get('filme'))),
        _ordner_item('Serien', 'serien', '%s Serien aus allen freien Quellen.' % _zahl(z.get('serien'))),
        _ordner_item('Live TV', 'live', '%s Live-Sender.' % _zahl(z.get('live'))),
        {'title': 'Suche', 'url': '', 'plot': 'Sucht sofort im Katalog, ohne Abfragen.', 'is_playable': False, 'next_func': 'search'},
        _ordner_item('Katalog-Stand: %s' % _stand_text(), 'stand', 'Zeigt, welche Quellen heute geliefert haben, und lädt bei Bedarf neu.'),
    ]


def _untermenue(art):
    k = 'f' if art == 'filme' else 's'
    wort = 'Filme' if art == 'filme' else 'Serien'
    out = [_ordner_item('Alle %s A–Z' % wort, 'b:%s' % k),
           _ordner_item('Nach Genre', 'g:%s' % k)]
    if art == 'filme':
        out.append(_ordner_item('Nach Jahrzehnt', 'j:f'))
    out.append(_ordner_item('Nach Quelle', 'q:%s' % k))
    return out


def _zaehlen(liste, schluessel):
    z = {}
    for e in liste:
        for k in schluessel(e):
            z[k] = z.get(k, 0) + 1
    return z


def _buchstaben(k):
    art = _ART[k]
    liste = _gefiltert(_liste(art))
    z = _zaehlen(liste, lambda e: [_buchstabe(e.get('t'))])
    reihe = ['0-9'] + [chr(c) for c in range(65, 91)] + ['#']
    return [_ordner_item('%s  (%s)' % (b, _zahl(z[b])), 'b:%s:%s:0' % (k, quote(b, safe=''))) for b in reihe if z.get(b)]


def _genres(k):
    art = _ART[k]
    liste = _gefiltert(_liste(art))
    z = _zaehlen(liste, lambda e: e.get('g') or ['Ohne Genre'])
    namen = sorted([g for g in z if g != 'Ohne Genre'], key=_falten) + (['Ohne Genre'] if z.get('Ohne Genre') else [])
    return [_ordner_item('%s  (%s)' % (g, _zahl(z[g])), 'g:%s:%s:0' % (k, quote(g, safe=''))) for g in namen]


def _jahrzehnt(e):
    j = _int(e.get('j'))
    return '%d' % (j // 10 * 10) if j else 'ohne'


def _jahrzehnte():
    liste = _gefiltert(_liste('filme'))
    z = _zaehlen(liste, lambda e: [_jahrzehnt(e)])
    namen = sorted([d for d in z if d != 'ohne'], reverse=True) + (['ohne'] if z.get('ohne') else [])
    return [_ordner_item('%s  (%s)' % ('Ohne Jahr' if d == 'ohne' else d + 'er', _zahl(z[d])), 'j:f:%s:0' % d) for d in namen]


def _quellenliste(k):
    art = _ART[k]
    ok = _verfuegbar()
    z = {}
    for e in _liste(art):
        for sid in {q[0] for q in _nutzbare(e, ok)}:
            z[sid] = z.get(sid, 0) + 1
    reihen = sorted(z, key=lambda s: ((_quellen().get(s) or {}).get('prio', 99), _falten(_qname(s))))
    out = [_ordner_item('%s  (%s)' % (_qname(s), _zahl(z[s])), 'q:%s:%s:0' % (k, quote(s, safe=''))) for s in reihen]
    if k == 'l':
        anzahl = sum(1 for e in _liste('live') if _nutzbare(e, ok))
        out.insert(0, _ordner_item('Alle Sender  (%s)' % _zahl(anzahl), 'q:l:*:0'))
    return out


def _gefiltert_nach(k, art_filter, wert):
    art = _ART[k]
    liste = _gefiltert(_liste(art))
    if art_filter == 'b':
        return [e for e in liste if _buchstabe(e.get('t')) == wert]
    if art_filter == 'g':
        return [e for e in liste if wert in (e.get('g') or ['Ohne Genre'])]
    if art_filter == 'j':
        out = [e for e in liste if _jahrzehnt(e) == wert]
        out.sort(key=lambda e: (-_int(e.get('j')), _falten(e.get('t'))))
        return out
    if art_filter == 'q':
        if wert == '*':
            return liste
        ok = _verfuegbar()
        return [e for e in liste if any(q[0] == wert for q in _nutzbare(e, ok))]
    return liste


def _neu(seite):
    grenze = (datetime.now() - timedelta(days=NEU_TAGE)).strftime('%Y%m%d')
    ok = _verfuegbar()
    treffer = []
    for art in ('filme', 'serien'):
        for e in _liste(art):
            n = str(e.get('n') or '0')
            if n != '0' and n >= grenze and _nutzbare(e, ok):
                treffer.append((n, art, e))
    treffer.sort(key=lambda x: (-_int(x[0]), _falten(x[2].get('t'))))
    if not treffer:
        return [_ordner_item('Zurzeit nichts Neues', 'stand', 'In den letzten %d Tagen kam nichts dazu (der erste Lauf zählt nicht als neu).' % NEU_TAGE)]
    start = max(0, seite) * SEITE
    out = []
    for n, art, e in treffer[start:start + SEITE]:
        it = _item(art, e, ok)
        if it:
            if art == 'serien':
                it['title'] = '%s  [Serie]' % it['title']
            out.append(it)
    if start + SEITE < len(treffer):
        out.append(_ordner_item('[B]Nächste Seite »[/B]', 'neu:%d' % (seite + 1)))
    return out


def _stand():
    idx = _index()
    ok = _verfuegbar()
    out = [_ordner_item('[B]Jetzt neu laden[/B]', 'neu_laden', 'Holt sofort den neuesten Stand.')]
    namen = {'ok': 'ok', 'teil': 'teilweise', 'alt': 'alter Stand', 'fehler': 'fehlgeschlagen'}
    for sid, b in sorted((idx.get('quellen') or {}).items(), key=lambda x: x[1].get('prio', 99)):
        teile = []
        for k, w in (('filme', 'Filme'), ('serien', 'Serien'), ('live', 'Live')):
            if b.get(k):
                teile.append('%s %s' % (_zahl(b[k]), w))
        status = namen.get(b.get('status'), b.get('status') or '?')
        fehlt = '' if sid in ok else '  [Scraper fehlt]'
        plot = 'Status: %s\nStand der Daten: %s\n%s' % (status, b.get('stand') or '-', b.get('hinweis') or '')
        if sid not in ok:
            plot += '\nOhne den Scraper „%s“ werden diese Titel nicht angezeigt (außer direkten Mediathek-Links).' % (b.get('f') or sid)
        out.append(_ordner_item('%s: %s · %s%s' % (b.get('n') or sid, status, ', '.join(teile) or 'leer', fehlt), 'stand', plot.strip()))
    out.append(_ordner_item('Katalog vom %s' % _stand_text(), 'stand', 'Quelle: ' + _basis()))
    return out


def _kwargs(fn, item, query=''):
    item = item or {}
    params = {}
    for k, v in item.items():
        if isinstance(v, (str, int, float)) and not isinstance(v, bool) and k != 'plot':
            params[k] = str(v)
    if isinstance(item.get('params'), dict):
        params.update(item['params'])
    verf = {'url': '' if item.get('url') is None else str(item.get('url')), 'params': params,
            'title': _sauber(item.get('title') or ''), 'year': item.get('year') or '',
            'season': _int(item.get('season')), 'episode': _int(item.get('episode')),
            'imdb': item.get('imdb') or '', 'tmdb': item.get('tmdb') or '', 'query': query}
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return verf
    var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    return {k: v for k, v in verf.items() if var_kw or k in sig.parameters}


def _ruf(sid, func, item):
    m = _anbieter(sid)
    if m is None:
        raise RuntimeError('Scraper %s fehlt' % sid)
    fn = getattr(m, func or 'get_hosters', None)
    if not callable(fn):
        raise RuntimeError('%s.%s fehlt' % (sid, func))
    return fn(**_kwargs(fn, item))


def _art(it):
    nf = str(it.get('next_func') or '').strip()
    titel = _sauber(it.get('title') or it.get('label') or '')
    if nf in _OHNE:
        return 'x'
    if nf == 'get_hosters':
        return 'spiel'
    if 'next_func' not in it and 'is_playable' not in it:
        return 'spiel'
    if not nf and it.get('is_playable'):
        return 'spiel'
    if not it.get('is_playable') and _SUCHTITEL.match(titel):
        return 'x'
    return 'ordner'


def _verpacken(sid, res, eltern=None):
    out = []
    if isinstance(res, dict):
        res = res.get('items') or []
    for it in list(res or []):
        if not isinstance(it, dict):
            continue
        a = _art(it)
        if a == 'x':
            continue
        roh = {k: v for k, v in it.items() if isinstance(v, (str, int, float)) and not isinstance(v, bool) and k != 'plot'}
        if isinstance(it.get('params'), dict):
            roh['params'] = it['params']
        for k in ('tvshowtitle', 'parent_title'):
            if eltern and eltern.get(k) and k not in roh:
                roh[k] = eltern[k]
        func = 'get_hosters' if a == 'spiel' else (str(it.get('next_func') or '').strip() or 'load')
        neu = {k: v for k, v in it.items() if k not in ('url', 'next_func', 'params', 'is_playable', 'provider_id', 'provider')}
        neu['url'] = 'pv:' + _packen({'s': sid, 'f': func, 'i': roh})
        neu['next_func'] = 'get_hosters' if a == 'spiel' else 'load'
        neu['is_playable'] = a == 'spiel'
        out.append(neu)
    return out


def _ersatz(sid, titel, jahr, art):
    n = _norm(titel)
    if not n:
        return None
    schl = (sid, n, _int(jahr), art)
    with _LOCK:
        if schl in _ERSATZ:
            return _ERSATZ[schl]
    treffer = None
    m = _anbieter(sid)
    fn = getattr(m, 'search', None) if m is not None else None
    if callable(fn):
        try:
            res = fn(**_kwargs(fn, {'title': '', 'url': ''}, query=_sauber(titel)))
        except Exception as ex:
            log.log('[Frei Haus] Ersatzsuche %s: %s' % (sid, ex), log.LOGWARNING)
            res = []
        if isinstance(res, dict):
            res = res.get('items') or []
        j = _int(jahr)
        beste = []
        for it in list(res or []):
            if not isinstance(it, dict) or _norm(it.get('title') or '') != n:
                continue
            a = _art(it)
            mt = str(it.get('mediatype') or '').lower()
            if art == 'serie' and (a != 'ordner' or mt in ('season', 'episode', 'movie')):
                continue
            if art == 'film' and (a != 'spiel' or mt not in ('movie', '')):
                continue
            ij = _int(it.get('year'))
            if j and ij and abs(ij - j) > 1:
                continue
            rang = (0 if mt in ('tvshow', 'movie') else 1, 0 if (j and ij) else 1)
            beste.append((rang, it))
        if beste:
            beste.sort(key=lambda x: x[0])
            treffer = beste[0][1]
    with _LOCK:
        _ERSATZ[schl] = treffer
    if treffer:
        log.log('[Frei Haus] Ersatz für "%s" bei %s: %s' % (titel, sid, treffer.get('url')))
    return treffer


def _oeffnen(sid, func, item, eltern):
    fehler = None
    try:
        items = _verpacken(sid, _ruf(sid, func, item), eltern)
    except Exception as ex:
        log.log('[Frei Haus] %s.%s: %s' % (sid, func, ex), log.LOGWARNING)
        items, fehler = [], ex
    if not items and str(item.get('mediatype') or '') == 'tvshow' and item.get('title'):
        alt = _ersatz(sid, item.get('title'), item.get('year'), 'serie')
        if alt:
            try:
                items = _verpacken(sid, _ruf(sid, str(alt.get('next_func') or '').strip() or 'load', alt), eltern)
            except Exception as ex:
                log.log('[Frei Haus] Ersatz %s: %s' % (sid, ex), log.LOGWARNING)
                items, fehler = [], ex
    if items:
        return items
    if fehler is not None:
        return [_ordner_item('%s antwortet gerade nicht' % _qname(sid), 'stand', str(fehler)[:300])]
    return [_ordner_item('Bei %s gerade keine Folgen' % _qname(sid), 'stand',
                         'Der Anbieter liefert dazu im Moment nichts. Der Titel ist eventuell in deiner Region nicht verfügbar '
                         'oder wurde entfernt.')]


def _serie(i):
    e = _nach_id('serien').get(i)
    if not e:
        return [_ordner_item('Serie nicht mehr im Katalog', 'stand')]
    qs = _nutzbare(e)
    eltern = {'tvshowtitle': e.get('t') or '', 'parent_title': e.get('t') or ''}
    items = []
    for q in qs:
        item = {'title': e.get('t') or '', 'url': q[1], 'params': q[2] or {}, 'year': str(e.get('j') or ''),
                'poster': e.get('b') or '', 'mediatype': 'tvshow'}
        item.update(eltern)
        items.append((q, item))
    if not items:
        return [_ordner_item('Kein passender Scraper installiert', 'stand')]
    if len(items) == 1:
        q, item = items[0]
        return _oeffnen(q[0], q[4] or 'load', item, eltern)
    out = []
    for q, item in items:
        out.append({'title': 'Bei %s' % _qname(q[0]), 'url': 'pv:' + _packen({'s': q[0], 'f': q[4] or 'load', 'i': item}),
                    'plot': e.get('p') or '', 'poster': e.get('b') or '', 'fanart': e.get('f') or e.get('b') or '',
                    'mediatype': 'tvshow', 'is_playable': False, 'next_func': 'load'})
    return out


def _weiter(code):
    d = _entpacken(code)
    sid, func, item = d.get('s'), d.get('f') or 'load', d.get('i') or {}
    if not sid:
        return []
    eltern = {k: item.get(k) for k in ('tvshowtitle', 'parent_title') if item.get(k)}
    return _oeffnen(sid, func, item, eltern)


def load(url='', params=None):
    url = str(url or '')
    try:
        if url == 'neu_laden':
            with _LOCK:
                _LISTEN.clear()
                _IDS.clear()
                _HEU.clear()
                _NORM.clear()
                _ERSATZ.clear()
                _MODS.clear()
                _VERF['t'] = 0.0
            idx = _index(True)
            _hinweis('Katalog vom %s geladen' % _stand_text() if idx else 'Katalog nicht erreichbar')
            return _haupt()
        if not url:
            return _haupt()
        if url == 'filme':
            return _untermenue('filme')
        if url == 'serien':
            return _untermenue('serien')
        if url == 'live':
            return _quellenliste('l')
        if url == 'stand':
            return _stand()
        if url.startswith('pv:'):
            return _weiter(url[3:])
        if url.startswith('s:'):
            return _serie(url[2:])
        if url.startswith('neu:'):
            return _neu(_int(url[4:]))
        teile = url.split(':')
        if len(teile) == 2 and teile[0] in ('b', 'g', 'q', 'j') and teile[1] in _ART:
            return {'b': _buchstaben, 'g': _genres, 'q': _quellenliste}.get(teile[0], lambda k: _jahrzehnte())(teile[1])
        if len(teile) == 4 and teile[0] in ('b', 'g', 'q', 'j') and teile[1] in _ART:
            wert = unquote(teile[2])
            return _seite(_ART[teile[1]], _gefiltert_nach(teile[1], teile[0], wert), ':'.join(teile[:3]), _int(teile[3]))
    except Exception as e:
        log.log('[Frei Haus] load(%s): %s' % (url[:80], e), log.LOGERROR)
        return [_ordner_item('Fehler: %s' % e, 'stand')]
    return _haupt()


def search(query='', params=None, url=''):
    q = _heu(query)
    if len(q) < 2:
        return []
    ok = _verfuegbar()
    treffer = []
    for art in ('filme', 'serien'):
        liste = _liste(art)
        heu = _heuhaufen(art)
        for e, h in zip(liste, heu):
            if q in h:
                rang = 0 if h == q else (1 if h.startswith(q) else 2)
                treffer.append((rang, _falten(e.get('t')), art, e))
    treffer.sort(key=lambda x: (x[0], x[1]))
    out = []
    for rang, _, art, e in treffer:
        it = _item(art, e, ok)
        if it:
            if art == 'serien':
                it['title'] = '%s  [Serie]' % it['title']
            out.append(it)
        if len(out) >= MAX_SUCHE:
            break
    return out


def _origin(domain):
    d = (domain or '').strip()
    if not d:
        return ''
    if not d.startswith('http'):
        d = 'https://' + d
    p = urlsplit(d)
    return '%s://%s' % (p.scheme, p.netloc) if p.netloc else ''


def _als_dict(h):
    if isinstance(h, dict):
        return dict(h)
    if isinstance(h, (list, tuple)):
        if len(h) < 2:
            return None
        d = {'name': h[0], 'url': h[1]}
        for v in h[2:]:
            if isinstance(v, bool):
                d['direct'] = d.get('direct') or v
            elif isinstance(v, dict):
                d['drm'] = v
        if len(h) > 3 and isinstance(h[3], str):
            d['quality'] = h[3]
        if len(h) > 4 and isinstance(h[4], str):
            d['lang'] = h[4]
        if len(h) > 5 and isinstance(h[5], str):
            d['source'] = h[5]
        return d
    if isinstance(h, str) and h.strip():
        return {'name': 'Stream', 'url': h}
    return None


def _drm_anpassen(drm, origin):
    drm = dict(drm)
    sh = dict(drm.get('stream_headers') or {}) if isinstance(drm.get('stream_headers'), dict) else {}
    sh.setdefault('Origin', origin)
    sh.setdefault('Referer', origin + '/')
    drm['stream_headers'] = sh
    if drm.get('exact_license_headers'):
        return drm
    std = [('User-Agent', _UA), ('Origin', origin), ('Referer', origin + '/')]
    lk = drm.get('license_key')
    lic = (drm.get('drm_license') or drm.get('license_url') or '').strip()
    if isinstance(lk, str) and lk.strip() and ('|' in lk or lk.strip().startswith('http')):
        teile = lk.split('|')
        while len(teile) < 2:
            teile.append('')
        da = {p.partition('=')[0].strip().lower() for p in teile[1].split('&') if p.strip()}
        extra = ['%s=%s' % (k, quote(v, safe='')) for k, v in std if k.lower() not in da]
        teile[1] = '&'.join(x for x in [teile[1]] + extra if x)
        drm['license_key'] = '|'.join(teile)
        drm['exact_license_headers'] = True
    elif lic and not (isinstance(lk, str) and lk.strip()):
        drm['license_key'] = '%s|%s|R{SSM}|' % (lic, '&'.join('%s=%s' % (k, quote(v, safe='')) for k, v in std))
        drm['exact_license_headers'] = True
    return drm


def _hoster_fix(roh, m, name):
    if isinstance(roh, dict):
        roh = roh.get('hosters') or roh.get('items') or [roh]
    origin = _origin(getattr(m, 'SITE_DOMAIN', '') if m is not None else '')
    out = []
    for h in list(roh or []):
        d = _als_dict(h)
        if not d or not d.get('url'):
            continue
        if not d.get('source'):
            d['source'] = name
        if origin and isinstance(d.get('drm'), dict):
            d['drm'] = _drm_anpassen(d['drm'], origin)
        out.append(d)
    return out


def _hoster_aus(e, season=0, episode=0, film=True):
    ok = _verfuegbar()
    out = []
    for q in _nutzbare(e, ok):
        sid, key, pa = q[0], q[1], q[2] or {}
        dl = q[3] if len(q) > 3 else ''
        nf = (q[4] if len(q) > 4 else '') or 'get_hosters'
        name = _qname(sid)
        m = _anbieter(sid) if sid in ok else None
        hs = []
        if m is not None:
            item = {'title': e.get('t') or '', 'url': key, 'params': pa, 'year': str(e.get('j') or ''),
                    'season': season, 'episode': episode}
            try:
                hs = _hoster_fix(_ruf(sid, nf, item), m, name)
            except Exception as ex:
                log.log('[Frei Haus] Hoster %s: %s' % (sid, ex), log.LOGWARNING)
                hs = []
            if not hs and film and not dl:
                alt = _ersatz(sid, e.get('t') or '', e.get('j'), 'film')
                if alt:
                    try:
                        hs = _hoster_fix(_ruf(sid, 'get_hosters', alt), m, name)
                    except Exception as ex:
                        log.log('[Frei Haus] Ersatz-Hoster %s: %s' % (sid, ex), log.LOGWARNING)
                        hs = []
        if not hs and dl:
            hs = [{'name': name, 'url': dl, 'direct': True, 'quality': 'HD', 'lang': 'de', 'source': name}]
        out.extend(hs)
        if any(not h.get('drm') for h in out):
            break
    return out


def _scout(title, year):
    n = _norm(title)
    if not n:
        return []
    j = _int(year)
    ok = _verfuegbar()
    for e, en in zip(_liste('filme'), _normen('filme')):
        if en != n:
            continue
        ej = _int(e.get('j'))
        if j and ej and abs(ej - j) > 1:
            continue
        if _nutzbare(e, ok):
            return _hoster_aus(e)
    return []


def get_hosters(title='', year='', season=0, episode=0, imdb='', tmdb='', url='', params=None):
    url = str(url or '')
    try:
        if url.startswith('pv:'):
            d = _entpacken(url[3:])
            sid = d.get('s')
            if not sid:
                return []
            m = _anbieter(sid)
            item = dict(d.get('i') or {})
            if d.get('f') and d['f'] != 'get_hosters':
                return []
            return _hoster_fix(_ruf(sid, 'get_hosters', item), m, _qname(sid))
        if url.startswith('f:'):
            e = _nach_id('filme').get(url[2:])
            return _hoster_aus(e) if e else []
        if url.startswith('l:'):
            e = _nach_id('live').get(url[2:])
            return _hoster_aus(e, film=False) if e else []
        if url.startswith('s:'):
            return []
        if not url and title and not _int(season):
            return _scout(title, year)
    except Exception as e:
        log.log('[Frei Haus] get_hosters: %s' % e, log.LOGERROR)
    return []
