#!/usr/bin/env python3
"""Collect public TWSE/TPEx closes without user holdings, secrets or Firecrawl.

Official sources first. If the official daily market snapshots are late or unreachable,
Yahoo historical daily bars may fill symbols recorded in the previous snapshot.
A date mismatch always rejects the quote. Writes nothing if no price is verified.
"""
import datetime as dt
import json
import pathlib
import re
import sys
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import requests

TPE = ZoneInfo('Asia/Taipei')
ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / 'prices'
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; PublicTWQuoteCollector/1.0)',
           'Accept': 'application/json, text/plain, */*'}
SOURCES = {
    'twse': 'https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL',
    'tpex': 'https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes',
}


def date_from_market(value):
    """Normalize Gregorian/ROC compact dates, or slash dates, to ISO."""
    s = re.sub(r'[^0-9]', '', str(value or ''))
    if len(s) == 7:  # ROC 1151027
        year = int(s[:3]) + 1911
        month, day = int(s[3:5]), int(s[5:7])
    elif len(s) == 8:  # Gregorian 20261027
        year, month, day = int(s[:4]), int(s[4:6]), int(s[6:8])
    else:
        return None
    try:
        return dt.date(year, month, day).isoformat()
    except ValueError:
        return None


def price(value):
    raw = str(value or '').strip().replace(',', '')
    try:
        n = Decimal(raw)
        if n.is_finite() and n > 0:
            return str(n)
    except (InvalidOperation, ValueError):
        pass
    return None


def parse(rows, market, target):
    """Only exact-day official closes are accepted."""
    if not isinstance(rows, list):
        return {}
    out = {}
    for obj in rows:
        if not isinstance(obj, dict):
            continue
        if market == 'twse':
            code = str(obj.get('Code', '')).strip()
            d = obj.get('Date')
            close = obj.get('ClosingPrice')
        else:
            code = str(obj.get('SecuritiesCompanyCode', '')).strip()
            d = obj.get('Date')
            close = obj.get('Close')
        if not re.fullmatch(r'[0-9A-Z]{4,10}', code):
            continue
        if date_from_market(d) != target:
            continue
        p = price(close)
        if p is None:
            continue
        out[code] = {'close': p, 'market': market, 'source': market.upper() + '_OpenAPI'}
    return out


def fetch_official(session, market, target):
    url = SOURCES[market]
    try:
        reply = session.get(url, headers=HEADERS, timeout=20)
        reply.raise_for_status()
        return parse(reply.json(), market, target)
    except (requests.RequestException, ValueError) as exc:
        print(f'{market} official failed: {exc}', file=sys.stderr)
        return {}



MIRROR_URL = (
    'https://raw.githubusercontent.com/u159753123-G/'
    'codex-taiwan-market-data/main/data/market/'
    'codex_stock_prices_latest.json'
)


def parse_community_mirror(payload, target):
    """Supplementary public TWSE/TPEx snapshot. Never accept stale or 'previous' rows.

    The third-party JSON is NOT an official TWSE response and is labelled as such.
    Official source always overrides these values.
    """
    if not isinstance(payload, dict) or payload.get('tradeDate') != target:
        return {}
    markets = payload.get('markets') or {}
    symbols = payload.get('symbols') or {}
    good = {}
    for code, item in symbols.items():
        if not isinstance(item, dict) or not re.fullmatch(r'[0-9A-Z]{4,10}', str(code)):
            continue
        upstream = str(item.get('market', '')).upper()
        if upstream not in ('TWSE', 'TPEX'):
            continue
        meta = markets.get('TWSE' if upstream == 'TWSE' else 'TPEx') or {}
        if (meta.get('tradeDate') != target or meta.get('dataStatus') != 'fresh'
                or meta.get('fetchStatus') != 'success'):
            continue
        if item.get('tradeDate') != target or item.get('status') != 'fresh':
            continue
        if str(item.get('source', '')).upper() not in ('TWSE', 'TPEX'):
            continue
        p = price(item.get('close'))
        if p is not None:
            good[code] = {'close': p,
                          'market': 'twse' if upstream == 'TWSE' else 'tpex',
                          'source': 'CommunityMirror_' + upstream}
    return good


def fetch_community_mirror(session, target):
    """Read a GitHub Actions-published full-market snapshot, never older dates."""
    try:
        r = session.get(MIRROR_URL, headers=HEADERS, timeout=18)
        r.raise_for_status()
        return parse_community_mirror(r.json(), target)
    except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
        print(f'GitHub public market mirror unavailable: {exc}', file=sys.stderr)
        return {}


def prior_symbols():
    latest = OUT / 'latest.json'
    if not latest.exists():
        return {}
    try:
        j = json.loads(latest.read_text(encoding='utf-8'))
        return {k: v.get('market') for k, v in j.get('quotes', {}).items()}
    except (OSError, ValueError, AttributeError):
        return {}


def yahoo_daily_close(session, symbol, market, target):
    # Fallback ONLY for symbols already known from previously saved market data.
    suffix = '.TWO' if market == 'tpex' else '.TW'
    start = dt.datetime.fromisoformat(target).replace(tzinfo=TPE) - dt.timedelta(days=9)
    end = start + dt.timedelta(days=13)
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}{suffix}'
    try:
        response = session.get(url, params={'period1': int(start.timestamp()),
             'period2': int(end.timestamp()), 'interval': '1d'}, headers=HEADERS, timeout=12)
        response.raise_for_status()
        j = response.json()['chart']['result'][0]
        stamps = j.get('timestamp') or []
        closes = j['indicators']['quote'][0]['close']
        for stamp, val in zip(stamps, closes):
            stamp_day = dt.datetime.fromtimestamp(stamp, TPE).date().isoformat()
            if stamp_day == target and price(val):
                return {'close': price(val), 'market': market, 'source': 'Yahoo_Daily_Historical'}
    except (requests.RequestException, ValueError, TypeError, IndexError, KeyError):
        pass
    return None


def collect(target, session=None, yahoo_limit=250):
    session = session or requests.Session()
    by_market = {m: fetch_official(session, m, target) for m in SOURCES}
    quotes = {}
    for group in by_market.values():
        quotes.update(group)

    # Secondary GitHub community mirror: distinct service, dates and freshness verified.
    mirrored = fetch_community_mirror(session, target)
    mirror_added = 0
    for code, value in mirrored.items():
        if code not in quotes:
            quotes[code] = value
            mirror_added += 1

    # Historical Yahoo bars are the final fallback. Avoid uncontrolled
    # multi-thousand-ticker Yahoo requests; this only covers known symbols.
    prior = prior_symbols()
    missing = [(code, m) for code, m in prior.items()
               if code not in quotes and m in SOURCES]
    if missing:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=5) as pool:
            values = list(pool.map(
                lambda a: yahoo_daily_close(session, *a, target),
                missing[:yahoo_limit]))
        for (code, market), result in zip(missing[:yahoo_limit], values):
            if result:
                quotes[code] = result
    return {'date': target, 'quotes': quotes,
            'counts': {'TWSE_official': len(by_market['twse']),
                       'TPEx_official': len(by_market['tpex']),
                       'Community_Mirror': mirror_added,
                       'Yahoo_fallback': sum(
                           v['source'] == 'Yahoo_Daily_Historical'
                           for v in quotes.values()),
                       'total': len(quotes)},
            'sources': {**SOURCES, 'community_mirror': MIRROR_URL}}


def main():
    now = dt.datetime.now(TPE)
    target = sys.argv[1] if len(sys.argv) > 1 else now.date().isoformat()
    dt.date.fromisoformat(target)
    if target == now.date().isoformat() and now.time() < dt.time(13, 35):
        print('Too early for official daily close', file=sys.stderr)
        return 2
    if dt.date.fromisoformat(target).weekday() > 4:
        print('Weekend: no update')
        return 0
    j = collect(target)
    count = j['counts']['total']
    if not count:
        print(f'No verified {target} close: leave previous data untouched', file=sys.stderr)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    body = json.dumps(j, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n'
    (OUT / f'{target}.json').write_text(body, encoding='utf-8')
    # Never overwrite latest with an older date. GitHub actions may finish out of order.
    old = prior_symbols()  # read before overwriting latest
    old_day = None
    try:
        old_day = json.loads((OUT / 'latest.json').read_text(encoding='utf-8')).get('date')
    except (OSError, ValueError, AttributeError):
        pass
    if not old_day or target >= old_day:
        (OUT / 'latest.json').write_text(body, encoding='utf-8')
    print(json.dumps({'date': target, 'counts': j['counts']}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
