#!/usr/bin/env python3
"""Build verified FAA NASR runway-end true bearings for Niltsi's configured airports."""
import csv
import io
import json
import sys
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

AIRPORTS = {'ORD', 'LAX', 'JFK'}
BASE = date(2026, 10, 1)  # Known FAA NASR effective cycle
OUT = Path('data/runways.json')

def cycle_dates(today):
    elapsed = (today - BASE).days // 28
    for n in (elapsed, elapsed - 1):
        yield BASE + timedelta(days=28 * n)

def fetch_archive(cycle):
    url = f'https://nfdc.faa.gov/webContent/28DaySub/extra/{cycle:%d_%b_%Y}_APT_CSV.zip'
    request = urllib.request.Request(url, headers={'User-Agent':'Niltsi/0.7 runway-reference updater', 'Accept':'application/zip'})
    with urllib.request.urlopen(request, timeout=90) as response:
        raw = response.read()
    return zipfile.ZipFile(io.BytesIO(raw)), url

def get_table(archive, filename):
    matches = [n for n in archive.namelist() if n.upper().endswith(filename.upper()) and not n.endswith('/')]
    if len(matches) != 1:
        raise ValueError(f'Expected one {filename}, found {matches}')
    with archive.open(matches[0]) as stream:
        data = stream.read().decode('utf-8-sig', errors='replace')
    return list(csv.DictReader(io.StringIO(data)))

def generate(archive, url, cycle):
    rows = get_table(archive, 'APT_RWY_END.csv')
    if not rows or not {'ARPT_ID','RWY_END_ID','TRUE_ALIGNMENT','RWY_ID'}.issubset(rows[0]):
        raise ValueError('FAA runway-end schema not recognized; refusing to publish')
    airports = {code: [] for code in AIRPORTS}
    seen = {code: set() for code in AIRPORTS}
    for row in rows:
        code = (row.get('ARPT_ID') or '').strip().upper()
        if code not in airports:
            continue
        runway = (row.get('RWY_END_ID') or '').strip().upper()
        bearing = (row.get('TRUE_ALIGNMENT') or '').strip()
        if not runway or not bearing:
            continue  # Never invent an alignment
        try:
            degrees = float(bearing)
        except ValueError:
            raise ValueError(f'Invalid FAA true bearing {code} {runway}: {bearing!r}')
        if not 0 <= degrees <= 360 or runway in seen[code]:
            raise ValueError(f'Invalid or duplicate runway end: {code} {runway}')
        seen[code].add(runway)
        airports[code].append({'id': runway, 'true_heading': degrees % 360, 'pair': (row.get('RWY_ID') or '').strip()})
    # Do not silently publish an incomplete airport inventory.
    expected = {'ORD':16, 'LAX':8, 'JFK':8}
    for code, count in expected.items():
        if len(airports[code]) != count:
            raise ValueError(f'{code}: expected {count} FAA runway ends with headings, found {len(airports[code])}')
    for code in airports:
        airports[code].sort(key=lambda x:(int(x['id'][:2]) if x['id'][:2].isdigit() else 99, x['id']))
    return {'source':'FAA NASR 28-Day Subscription — APT_RWY_END.csv', 'source_url':url, 'effective_date':cycle.isoformat(), 'generated_utc':datetime.now(timezone.utc).isoformat(), 'heading_reference':'TRUE', 'airports':airports}

def main():
    failures=[]
    for cycle in cycle_dates(datetime.now(timezone.utc).date()):
        try:
            archive,url=fetch_archive(cycle)
            data=generate(archive,url,cycle)
            OUT.parent.mkdir(parents=True,exist_ok=True)
            OUT.write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')
            print(f'Published FAA runway data effective {cycle}: '+', '.join(f'{k}={len(v)}' for k,v in data['airports'].items()))
            return
        except Exception as exc:
            failures.append(f'{cycle}: {exc}')
    sys.exit('No valid FAA runway archive available. Existing data untouched. '+ ' | '.join(failures))

if __name__=='__main__': main()
