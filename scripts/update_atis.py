#!/usr/bin/env python3
"""Fetch atis.info D-ATIS snapshots. Informational/development use only."""
import datetime as dt
import json
import os
import pathlib
import urllib.request

AIRPORTS = ('KORD', 'KLAX', 'KJFK')
OUTPUT = pathlib.Path('data/atis.json')
URL = 'https://atis.info/api/'

def main():
    results = {}
    errors = {}
    for airport in AIRPORTS:
        try:
            req = urllib.request.Request(URL + airport, headers={'User-Agent': 'Niltsi/0.8 (independent aviation data prototype)', 'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=20) as response:
                records = json.load(response)
            if isinstance(records, dict):
                records = [records]
            if not isinstance(records, list):
                raise ValueError('Expected array of ATIS records')
            valid = []
            for item in records:
                if not isinstance(item, dict) or item.get('airport', '').upper() != airport:
                    continue
                if item.get('type') not in ('combined', 'arr', 'dep'):
                    continue
                if not isinstance(item.get('datis'), str) or not item['datis'].strip():
                    continue
                valid.append({key: item.get(key) for key in ('airport', 'type', 'code', 'datis', 'time', 'updatedAt')})
            if not valid:
                raise ValueError('No valid ATIS reports')
            results[airport] = valid
        except Exception as exc:
            errors[airport] = str(exc)
    # Fail rather than overwrite good published data with an incomplete snapshot.
    if errors:
        raise RuntimeError(f'ATIS retrieval incomplete; existing file retained: {errors}')
    payload = {'retrieved_utc': dt.datetime.now(dt.timezone.utc).isoformat().replace('+00:00', 'Z'),
               'source': 'https://atis.info/api', 'airports': results,
               'notice': 'atis.info: Do not use for real world flight planning or navigation.'}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    os.replace(temporary, OUTPUT)
    print('Published ATIS snapshot for:', ', '.join(AIRPORTS))

if __name__ == '__main__':
    main()
