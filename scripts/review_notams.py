#!/usr/bin/env python3
"""Local files -> offline review CLI / standalone escaped HTML. No network code."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import sys
from datetime import datetime, timezone

from notam_review_helpers import FeedError, assert_public_source
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
    lines = ['SPATCHIN OFFLINE NOTAM INTERPRETATION REVIEW - v0.9',
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
    """Present existing review results; never change or reclassify them."""
    esc = html.escape
    block = lambda value: '<pre class="diagnostic">'+esc(literal(value))+'</pre>'
    summaries = {
        'FULL_RUNWAY_CLOSURE': 'The text appears to describe a whole-runway closure.',
        'DIRECTIONAL_RESTRICTION': 'The text appears to restrict landing or takeoff on a specific runway end.',
        'PARTIAL_RESTRICTION': 'The text appears to describe a partial runway restriction between named taxiways, not a whole-runway closure.',
        'EQUIPMENT_OUTAGE': 'The text appears to describe a runway equipment or lighting outage, not a confirmed runway closure.',
        'CONDITIONAL_RESTRICTION': 'The text appears to describe a conditional or scheduled restriction. Its conditions require review.',
        'TAXIWAY_NOTICE': 'The text appears to concern a taxiway. A runway mention does not establish a runway closure.',
        'AMBIGUOUS': 'The text cannot be interpreted confidently. No runway restriction is established.',
    }
    phases = {
        'WITHIN_REPORTED_WINDOW': 'The review time falls within the reported time window. This does not confirm that the notice is active.',
        'FUTURE': 'The reported time window begins after the review time; no current restriction is established.',
        'EXPIRED': 'The reported time window ends before or at the review time. Lifecycle and cancellation status remain unverified.',
        'UNKNOWN': 'Timing cannot be determined confidently from the reported values. Permanent, estimated, compact or invalid times need review.',
    }

    def highlighted(notice):
        text = notice.get('original_text')
        if not isinstance(text, str) or not text:
            return 'Original NOTAM text is unavailable. Inspect the preserved source in Technical details.'
        spans = []
        for evidence in notice.get('evidence', []):
            a, b = evidence.get('start'), evidence.get('end')
            if (type(a) is int and type(b) is int and 0 <= a < b <= len(text)
                    and text[a:b] == evidence.get('quote')):
                spans.append((a, b))
        merged = []
        for a, b in sorted(spans):
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        parts, cursor = [], 0
        for a, b in merged:
            parts.extend((esc(text[cursor:a]), '<mark>'+esc(text[a:b])+'</mark>'))
            cursor = b
        parts.append(esc(text[cursor:]))
        return ''.join(parts)

    articles = []
    numbers = {notice['review_key']: i for i, notice in enumerate(report['notices'], 1)}
    for i, notice in enumerate(report['notices'], 1):
        ends = notice.get('proposed_runway_ends', [])
        association = ('Proposed runway ends: '+', '.join(ends)+'. Runway association is unverified.'
                       if ends else 'Runway association cannot be determined from this notice. No runway ends are assigned.')
        if not ends:
            reason = next((item.get('reason') for item in notice.get('evidence', [])
                           if item.get('rule') == 'BASELINE_CLASSIFIER'), '')
            association += ' '+{
                'UNKNOWN_RUNWAY': 'A referenced runway was not found in the local reference inventory.',
                'INCONSISTENT_RUNWAY_PAIR': 'The stated runway ends do not match a pair in the local inventory.',
                'MULTIPLE_RUNWAY_CLAUSES': 'Multiple runway clauses prevent a confident association.',
                'UNVERIFIED_SUBJECT_SCOPE': 'The scope of the runway reference is unclear.',
                'NO_EXPLICIT_RUNWAY_SUBJECT': 'No explicit runway subject was identified.',
            }.get(reason, 'The text is unsupported, ambiguous, or does not identify a runway restriction.')
        timing = phases.get(notice.get('validity_phase'), phases['UNKNOWN'])
        warnings = []
        codes = notice.get('warnings', [])
        if 'SCHEDULE_UNINTERPRETED' in codes:
            warnings.append('A schedule is present but has not been evaluated; the reported start and end do not establish active hours.')
        if 'CONDITIONAL_LANGUAGE_UNRESOLVED' in codes:
            warnings.append('Conditional language is unresolved; the restriction may not apply in all circumstances.')
        if any('CANCELLATION_OR_REPLACEMENT' in code for code in codes):
            warnings.append('Cancellation or replacement references need review. The notice is not confirmed active or cancelled.')
        start = notice.get('effective_start_utc') or 'Cannot determine UTC start; inspect the reported source value in Technical details.'
        end = notice.get('effective_end_utc') or 'Cannot determine UTC end; inspect the reported source value in Technical details.'
        articles.append(
            '<article id="'+esc(notice['review_key'], quote=True)+'">'
            '<header><h2>Notice '+str(i)+' · '+esc(notice['airport_context'])+'</h2>'
            '<p class="coverage">Coverage: UNKNOWN · Lifecycle: unverified</p></header>'
            '<h3>Original NOTAM text</h3><pre class="original-notam">'+highlighted(notice)+'</pre>'
            '<p class="hint">Highlighted words are source evidence for the proposal, not confirmation of runway status.</p>'
            '<section class="interpretation"><h3>Proposed interpretation — unverified</h3><p>'
            +esc(summaries.get(notice['proposed_category'], summaries['AMBIGUOUS']))+'</p>'
            '<p>No closure or restriction is confirmed. Coverage remains UNKNOWN.</p></section>'
            '<dl><dt>Associated airport</dt><dd>'+esc(notice['airport_context'])+' (supplied context; association unverified)</dd>'
            '<dt>Runway association</dt><dd>'+esc(association)+'</dd>'
            '<dt>Reported effective start (UTC)</dt><dd>'+esc(start)+'</dd>'
            '<dt>Reported effective end (UTC)</dt><dd>'+esc(end)+'</dd></dl>'
            '<p class="timing">'+esc(timing)+'</p>'
            +''.join('<p class="warning">'+esc(warning)+'</p>' for warning in warnings)
            +'<details class="technical"><summary>Technical details</summary>'
            '<p>Preserved source, provenance, raw JSON, evidence offsets and internal review diagnostics.</p>'
            +block(notice)+'</details></article>')
    rows = ''.join(
        '<tr class="runway-row"><td>'+esc(row['airport'])+'</td><td>'+esc(row['runway_end'])
        +'</td><td>UNKNOWN</td><td>'
        +(' '.join('<a href="#'+esc(key, quote=True)+'">Notice '+str(numbers[key])+'</a>'
                  for key in row['candidate_reviews']) or 'No proposal; no availability inference')
        +'</td></tr>' for row in report['runway_rows'])
    label = ('SYNTHETIC EXAMPLES ONLY - NOT REAL FAA NOTICES' if report['data_kind'] == 'synthetic'
             else 'USER-SUPPLIED SANITIZED EXAMPLES - AUTHENTICITY AND SEMANTICS UNVERIFIED')
    diagnostics = {key: value for key, value in report.items() if key != 'notices'}
    style = """
    *{box-sizing:border-box}body{overflow-wrap:anywhere;color-scheme:dark;background:#0b1520;
    color:#e8f1f7;font:16px/1.6 system-ui;max-width:1060px;margin:auto;padding:32px 24px}
    h1{font-size:clamp(25px,4vw,36px);line-height:1.25}h2{margin:0;font-size:22px}
    h3{font-size:16px;margin:20px 0 8px}article,.index{background:#142333;border:1px solid #2c465b;
    border-radius:10px;padding:24px;margin:24px 0}.coverage,.warning{color:#f3ca80}
    .coverage{margin:6px 0}.hint{color:#a9becf;font-size:13px;margin-top:8px}
    pre{white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word}
    .original-notam{background:#0d1c29;border-left:3px solid #78baf1;padding:18px;font-size:16px}
    mark{background:#f3ca80;color:#111827;padding:1px 0}.interpretation{padding:1px 18px;
    background:#1b3042;border-radius:7px}.interpretation h3{color:#a9d6f5}
    dl{display:grid;grid-template-columns:220px minmax(0,1fr);gap:10px 20px}dt{color:#a9becf}dd{margin:0}
    .technical{border-top:1px solid #2c465b;padding-top:14px;margin-top:20px;font-size:13px}
    .diagnostic{background:#10202e;padding:12px}a,summary{color:#78baf1}summary{cursor:pointer}
    table{width:100%;table-layout:fixed;border-collapse:collapse;font-size:14px}
    td,th{overflow-wrap:anywhere;padding:8px;text-align:left;border-bottom:1px solid #2c465b}
    @media(max-width:600px){body{padding:20px 12px}article,.index{padding:16px}dl{display:block}
    dt{margin-top:14px}dd{margin-top:3px}td,th{padding:5px;font-size:12px}.original-notam{padding:12px}}
    """
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">'
        '<meta name="referrer" content="no-referrer"><title>Spatchin v0.9 offline NOTAM review</title>'
        '<style>'+style+'</style></head><body><h1>Spatchin v0.9 offline NOTAM interpretation review</h1>'
        '<p class="warning">'+label+'</p><p>Coverage: UNKNOWN throughout. All interpretations are unverified proposals, '
        'not confirmed runway status. Missing closure information never establishes availability.</p>'
        '<p>Review time (UTC): '+esc(report['review_at_utc'])+' · '+str(len(report['notices']))+' notices</p>'
        +''.join(articles)+'<section class="index"><details><summary>Runway review index · '
        +str(len(report['runway_rows']))+' ends · Coverage UNKNOWN</summary><table><thead><tr>'
        '<th>Airport</th><th>Runway</th><th>Coverage</th><th>Proposals for review</th></tr></thead><tbody>'
        +rows+'</tbody></table></details></section><details class="technical"><summary>Technical details</summary>'
        '<p>Input hash, runway inventory provenance, original input and report metadata.</p>'
        +block(diagnostics)+'</details></body></html>')


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
