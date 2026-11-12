#!/usr/bin/env python3
"""Local files -> offline review CLI / standalone escaped HTML. No network code."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import sys
from datetime import datetime, timezone

from nms_adapter import FeedError, assert_public_source
from notam_review import AIRPORTS, KINDS, build_review

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 8*1024*1024


def literal(value):
    # JSON quoting escapes terminal control/bidi characters; do not emit raw ANSI.
    return json.dumps(value,ensure_ascii=True,indent=2)


def load_json(path):
    if str(path).startswith(('http:','https:','//','\\\\')):
        raise FeedError('REVIEW_LOCAL_FILES_ONLY')
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES:
        raise FeedError('REVIEW_INPUT_TOO_LARGE')
    def unique(pairs):
        result = {}
        for key,value in pairs:
            if key in result:
                raise FeedError('REVIEW_DUPLICATE_JSON_KEY')
            result[key]=value
        return result
    def invalid_constant(_):
        raise FeedError('REVIEW_INVALID_JSON')
    try:
        data = json.loads(raw,object_pairs_hook=unique,parse_constant=invalid_constant)
    except (ValueError,UnicodeError):
        raise FeedError('REVIEW_INVALID_JSON') from None
    # Reject credential-looking input rather than emitting a partially redacted
    # notice. No process environment variables are read by this offline tool.
    assert_public_source(data)
    return data, hashlib.sha256(raw).hexdigest()


def render_text(report):
    lines = ['OFFLINE NOTAM INTERPRETATION REVIEW - v0.9',
             'DATA: '+report['data_kind'].upper()+' (never verified FAA data)',
             report['notice'], 'Review at UTC: '+report['review_at_utc'], 'Input SHA256: '+report.get('input_sha256','not supplied'), '',
             'ALL 32 RUNWAY ENDS - COVERAGE ALWAYS UNKNOWN']
    for row in report['runway_rows']:
        lines.append(f"{row['airport']} {row['runway_end']:3} | UNKNOWN | candidate reviews: {', '.join(row['candidate_reviews']) or 'none; no availability inference'}")
    for notice in report['notices']:
        lines.extend(['', 'REVIEW '+notice['review_key'], literal({k:v for k,v in notice.items() if k!='original_feature'})])
    lines.extend(['', 'PRESERVED ORIGINAL INPUT / PROVENANCE / UNKNOWN FIELDS', literal(report['original_input'])])
    return '\n'.join(lines)+'\n'


def render_html(report):
    esc = html.escape
    block = lambda value:'<pre>'+esc(literal(value))+'</pre>'
    rows = ''.join('<tr class="runway-row"><td>'+esc(row['airport'])+'</td><td>'+esc(row['runway_end'])+'</td><td>UNKNOWN</td><td>'+(' '.join('<a href="#'+esc(key,quote=True)+'">'+esc(key)+'</a>' for key in row['candidate_reviews']) or 'No candidate; no availability inference')+'</td></tr>' for row in report['runway_rows'])
    articles = []
    for notice in report['notices']:
        details = {k:v for k,v in notice.items() if k not in ('original_feature','original_text')}
        articles.append('<article id="'+notice['review_key']+'"><h2>'+notice['review_key']+' | '+esc(notice['proposed_category'])+' | '+esc(notice['validity_phase'])+'</h2><p class="warning">Coverage: UNKNOWN. Candidate only, not a runway availability determination.</p><h3>Original NOTAM text (JSON-escaped)</h3>'+block(notice['original_text'])+'<h3>ID, airport context, UTC times, schedules, proposals, evidence and warnings</h3>'+block(details)+'<details><summary>Preserved original notice, translations and unrecognized fields</summary>'+block(notice['original_feature'])+'</details></article>')
    label = 'SYNTHETIC EXAMPLES ONLY - NOT REAL FAA NOTICES' if report['data_kind']=='synthetic' else 'USER-SUPPLIED SANITIZED EXAMPLES - AUTHENTICITY AND SEMANTICS UNVERIFIED'
    return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'"><meta name="referrer" content="no-referrer"><title>Niltsi v0.9 offline NOTAM review</title><style>body{color-scheme:dark;background:#0b1520;color:#e8f1f7;font:14px/1.5 system-ui;max-width:1200px;margin:auto;padding:24px}article,section{background:#142333;border:1px solid #2c465b;border-radius:9px;padding:16px;margin:16px 0}.warning{color:#f3ca80}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#10202e;padding:12px}table{width:100%;border-collapse:collapse}td,th{padding:6px;text-align:left;border-bottom:1px solid #2c465b}a,summary{color:#78baf1}summary{cursor:pointer}</style></head><body><h1>Niltsi v0.9 offline NOTAM interpretation review</h1><p class="warning">'+label+'</p><p>'+esc(report['notice'])+'</p><p>Review at UTC: '+esc(report['review_at_utc'])+'</p><p>Input SHA256: '+esc(report.get('input_sha256','not supplied'))+'</p><section><h2>All 32 runway ends</h2><table><thead><tr><th>Airport</th><th>Runway</th><th>Coverage</th><th>Proposals for review</th></tr></thead><tbody>'+rows+'</tbody></table></section>'+''.join(articles)+'<section><details><summary>Runway inventory provenance</summary>'+block(report['inventory_provenance'])+'</details></section><section><details><summary>Complete original input and provenance</summary>'+block(report['original_input'])+'</details></section></body></html>'


def main(argv=None):
    parser = argparse.ArgumentParser(description='OFFLINE ONLY: review sanitized local FAA examples; never changes dashboard coverage or makes requests.')
    parser.add_argument('input',type=Path,help='Local observed-envelope JSON or review bundle; never a URL')
    parser.add_argument('--airport',choices=AIRPORTS,help='Required query context for a single FAA response')
    parser.add_argument('--kind',choices=KINDS,help='Required for a single response; never labels examples as verified')
    parser.add_argument('--at',help='Review instant with explicit UTC/offset; defaults to current UTC')
    parser.add_argument('--json',action='store_true',help='Print full machine-readable review JSON')
    parser.add_argument('--html',type=Path,help='Write standalone offline HTML to a NEW local file (no scripts or external assets)')
    args = parser.parse_args(argv)
    try:
        document, digest = load_json(args.input)
        inventory, _ = load_json(ROOT/'data/runways.json')
        report = build_review(document,inventory,args.at or datetime.now(timezone.utc).isoformat(),args.kind,args.airport)
        report['input_sha256'] = digest
        if args.html:
            destination = args.html.resolve()
            # Review output must never overwrite source, dashboard, or feeds.
            if str(args.html).startswith(('//','\\\\')) or destination.suffix.lower()!='.html':
                raise FeedError('REVIEW_LOCAL_HTML_ONLY')
            if destination == args.input.resolve() or destination.parent == ROOT or ROOT/'data' in destination.parents:
                raise FeedError('REVIEW_OUTPUT_PROTECTED')
            rendered = render_html(report)
            destination.parent.mkdir(parents=True,exist_ok=True)
            with destination.open('x',encoding='utf-8') as stream:
                stream.write(rendered)
            print('Offline HTML review written. Coverage remains UNKNOWN; open the file locally.')
        else:
            print(literal(report) if args.json else render_text(report),end='\n' if args.json else '')
        return 0
    except FeedError as exc:
        codes = {'SCHEMA_MISMATCH','INVALID_VALIDITY','SENSITIVE_RESPONSE','REVIEW_KIND_REQUIRED','REVIEW_KIND_CONFLICT','REVIEW_AIRPORT_CONFLICT','REVIEW_AIRPORT_REQUIRED','REVIEW_INPUT_UNSUPPORTED','REVIEW_INVENTORY_INVALID','REVIEW_LOCAL_FILES_ONLY','REVIEW_INPUT_TOO_LARGE','REVIEW_DUPLICATE_JSON_KEY','REVIEW_INVALID_JSON','REVIEW_LOCAL_HTML_ONLY','REVIEW_OUTPUT_PROTECTED'}
        print('Offline review rejected: '+(str(exc) if str(exc) in codes else 'VALIDATION_FAILURE')+'. No source or dashboard data changed.',file=sys.stderr)
        return 1
    except (OSError,ValueError,TypeError,KeyError,RecursionError):
        print('Offline review failed: check local input format, explicit time, inventory and NEW output path. Details omitted to avoid exposing input.',file=sys.stderr)
        return 1


if __name__=='__main__':
    sys.exit(main())
