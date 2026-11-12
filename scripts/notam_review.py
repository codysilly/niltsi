"""Offline interpretation proposals only. Never imported by the live updater.

Uses the existing conservative classifier as a baseline, with separate review
categories/evidence. No proposal establishes lifecycle, completeness, or availability.
"""
import copy
import re
from datetime import datetime

from nms_adapter import FeedError, at_path, utc
from notam_rules import RUNWAY, SUBJECT, classify, normalize

AIRPORTS = ('KORD', 'KLAX', 'KJFK')
KINDS = ('synthetic', 'sanitized-real-unverified')
SCHEDULE = re.compile(r'\b(?:DLY|DAILY|MON(?:DAY)?|TUE(?:SDAY)?|WED(?:NESDAY)?|THU(?:RSDAY)?|FRI(?:DAY)?|SAT(?:URDAY)?|SUN(?:DAY)?|SR|SS|SUNRISE|SUNSET)\b', re.I)
CONDITIONAL = re.compile(r'\b(?:EXC|EXCEPT|PPR|PRIOR|PERMISSION|WHEN|IF|UNLESS|MAY|POSS|SUBJ)\b', re.I)
LIFECYCLE = re.compile(r'\b(?:NOTAMC|NOTAMR|CANCEL(?:LED|ED|S|LATION)?|REPLAC(?:ED|ES|EMENT))\b', re.I)
EQUIPMENT = r'(?:PAPI|VASI|HIRL|MIRL|REIL|RCLL|TDZL|ILS|ALS|LIGHTS?|LIGHTING)'


def schedules(value, path='properties.coreNOTAMData'):
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = path+'.'+key
            if 'schedule' in key.lower() or key.lower() in ('timesheet', 'timesheets'):
                if child not in (None, '', [], {}):
                    found.append({'path':child_path, 'value':copy.deepcopy(child)})
            elif isinstance(child, (dict, list)):
                found.extend(schedules(child, child_path))
    elif isinstance(value, list):
        for i, child in enumerate(value):
            found.extend(schedules(child, f'{path}[{i}]'))
    return found


def validity(start, end, at):
    try:
        start_utc, end_utc = utc(start), utc(end)
        a, b, current = (datetime.fromisoformat(t) for t in (start_utc, end_utc, at))
        if b <= a:
            return start_utc, end_utc, 'UNKNOWN'
        return start_utc, end_utc, 'FUTURE' if current < a else 'EXPIRED' if current >= b else 'WITHIN_REPORTED_WINDOW'
    except FeedError:
        def maybe(value):
            try:
                return utc(value)
            except FeedError:
                return None
        return maybe(start), maybe(end), 'UNKNOWN'


def lifecycle_hints(notam, text):
    hints = []
    def walk(node, path):
        if isinstance(node,dict):
            for key,value in node.items():
                normalized = re.sub('[^a-z]', '', key.lower())
                field = path+'.'+key
                if any(token in normalized for token in ('cancel', 'replac', 'previousnotam', 'referencednotam')) and value not in (None, '', [], {}):
                    hints.append({'field':field, 'value':copy.deepcopy(value)})
                elif normalized in ('type','status') and isinstance(value,str) and (value.upper() in ('C','R') or LIFECYCLE.search(value)):
                    hints.append({'field':field, 'value':value})
                if isinstance(value,(dict,list)):
                    walk(value,field)
        elif isinstance(node,list):
            for i,value in enumerate(node):
                walk(value,f'{path}[{i}]')
    walk(notam,'properties.coreNOTAMData')
    for match in LIFECYCLE.finditer(text):
        hints.append({'field':'text', 'quote':match[0], 'start':match.start(), 'end':match.end()})
    return hints


def interpret(feature, airport, inventory, at, index, provenance):
    try:
        core = at_path(feature, 'properties.coreNOTAMData')
        notam = at_path(core, 'notam')
        if not isinstance(notam, dict):
            raise FeedError('SCHEMA_MISMATCH')
    except FeedError:
        core, notam = {}, {}
    original = notam.get('text')
    text = original if isinstance(original, str) else ''
    normalized = ' '.join(text.upper().split())
    fields = schedules(core)
    schedule_tokens = [{'quote':m[0], 'start':m.start(), 'end':m.end()} for m in SCHEDULE.finditer(text)]
    hints = lifecycle_hints(core, text)
    start, end, phase = validity(notam.get('effectiveStart'), notam.get('effectiveEnd'), at)
    baseline = classify({'text':text, 'airport':airport, 'schedule':None, 'end_kind':'fixed'}, inventory)
    evidence = []
    warnings = ['LIFECYCLE_UNVERIFIED', 'AIRPORT_CONTEXT_REQUIRES_REVIEW', 'COVERAGE_AND_PAGINATION_UNVERIFIED']
    category, ends, candidate = 'AMBIGUOUS', [], 'UNKNOWN'
    match = re.search(SUBJECT.pattern, text, re.I | re.S)
    normalized_match = SUBJECT.search(normalized)
    known = {r['id']:r['pair'].split('/') for r in inventory}
    ids = []
    if match:
        evidence.append({'rule':'RUNWAY_SUBJECT', 'field':'notam.text', 'quote':match[1], 'start':match.start(1), 'end':match.end(1)})
        ids = [normalize(v.upper()) for v in re.findall(RUNWAY, match[1], re.I)]
    # Existing scope/pair/unknown-runway protections apply to additional review
    # categories too. A partial restriction must not escape those guards.
    blocked_scope = baseline['reason'] in ('MULTIPLE_RUNWAY_CLAUSES','NO_EXPLICIT_RUNWAY_SUBJECT','UNKNOWN_RUNWAY','INCONSISTENT_RUNWAY_PAIR','UNVERIFIED_SUBJECT_SCOPE')
    tail = normalized_match[2] if normalized_match else ''
    if not blocked_scope and ids and set(ids) <= set(known):
        paired = sorted({end for id_ in ids for end in known[id_]})
        if baseline['classification'] == 'closure':
            category, ends, candidate = 'FULL_RUNWAY_CLOSURE', baseline['runway_ends'], 'CLOSED'
        elif baseline['classification'] == 'restriction':
            category = 'EQUIPMENT_OUTAGE' if baseline['reason']=='RUNWAY_EQUIPMENT_RESTRICTION' else 'DIRECTIONAL_RESTRICTION'
            ends, candidate = baseline['runway_ends'], 'NOTAM RESTRICTION'
        elif re.fullmatch(r'(?:CLSD|CLOSED)\s+(?:BTN|BETWEEN)\s+(?:TWY|TAXIWAY)\s+[A-Z0-9]+\s+AND\s+(?:TWY|TAXIWAY)\s+[A-Z0-9]+[.]?', tail):
            category, ends, candidate = 'PARTIAL_RESTRICTION', paired, 'NOTAM RESTRICTION'
            warnings.append('PARTIAL_EXTENT_REQUIRES_REVIEW')
        elif re.fullmatch(EQUIPMENT+r'\s+(?:CLSD|CLOSED|OTS|U/S|UNSERVICEABLE)[.]?', tail):
            category, ends, candidate = 'EQUIPMENT_OUTAGE', sorted(set(ids)), 'NOTAM RESTRICTION'
        elif re.match(r'(?:CLSD|CLOSED)\b', tail) and (CONDITIONAL.search(text) or schedule_tokens):
            category, ends = 'CONDITIONAL_RESTRICTION', paired
        if category != 'AMBIGUOUS' and match:
            evidence.append({'rule':category, 'field':'notam.text', 'quote':match[2], 'start':match.start(2), 'end':match.end(2)})
            for id_ in sorted(set(ids)):
                evidence.append({'rule':'INVENTORY_PAIR' if len(ends)>len(set(ids)) else 'EXPLICIT_END', 'runway':id_, 'inventory_pair':'/'.join(known[id_])})
    if re.search(r'\b(?:TWY|TAXIWAY)\b', normalized[:normalized_match.start()] if normalized_match else normalized) and category=='AMBIGUOUS':
        category = 'TAXIWAY_NOTICE'
        warnings.append('RUNWAY_MENTION_IS_NOT_RUNWAY_CLOSURE')
    if re.search(r'\bNOT\s+(?:CLSD|CLOSED)\b', normalized):
        category, ends, candidate = 'AMBIGUOUS', [], 'UNKNOWN'
        warnings.append('NEGATED_CLOSURE_TEXT')
    if fields or schedule_tokens:
        warnings.append('SCHEDULE_UNINTERPRETED')
        candidate = 'UNKNOWN'
    conditions = [{'quote':m[0], 'start':m.start(), 'end':m.end()} for m in CONDITIONAL.finditer(text)]
    if conditions:
        warnings.append('CONDITIONAL_LANGUAGE_UNRESOLVED')
        candidate = 'UNKNOWN'
    if hints:
        warnings.append('CANCELLATION_OR_REPLACEMENT_UNCERTAIN')
        candidate = 'UNKNOWN'
    if re.search(r'\b\d{10}-\d{10}(?:EST)?\b', normalized):
        warnings.append('EMBEDDED_VALIDITY_UNINTERPRETED')
        candidate = 'UNKNOWN'
    if phase != 'WITHIN_REPORTED_WINDOW':
        candidate = 'UNKNOWN'
        warnings.append('VALIDITY_'+phase)
    if category == 'AMBIGUOUS':
        warnings.append('UNSUPPORTED_OR_AMBIGUOUS_TEXT')
    evidence.append({'rule':'BASELINE_CLASSIFIER', 'reason':baseline['reason'], 'classification':baseline['classification'], 'proposed_ends':baseline['runway_ends']})
    evidence.append({'rule':'STRUCTURED_VALIDITY_ONLY', 'start_utc':start, 'end_utc':end, 'review_at_utc':at, 'phase':phase})
    return {
        'review_key':f'{airport}-{index}', 'id':copy.deepcopy(notam.get('id')), 'number':copy.deepcopy(notam.get('number')),
        'airport_context':airport, 'original_text':copy.deepcopy(original),
        'effective_start_utc':start, 'effective_end_utc':end,
        'original_start':copy.deepcopy(notam.get('effectiveStart')), 'original_end':copy.deepcopy(notam.get('effectiveEnd')),
        'validity_phase':phase, 'schedule_fields':fields, 'schedule_text_evidence':schedule_tokens,
        'conditional_evidence':conditions, 'lifecycle_hints':hints,
        'proposed_category':category, 'proposed_runway_ends':ends,
        'candidate_state_assuming_lifecycle_confirmed':candidate,
        'coverage_state':'UNKNOWN', 'lifecycle':'UNKNOWN', 'warnings':warnings,
        'evidence':evidence, 'original_feature':copy.deepcopy(feature), 'provenance':copy.deepcopy(provenance),
    }


def build_review(document, inventory_document, at, kind=None, airport=None):
    at = utc(at)
    if not isinstance(document, dict):
        raise FeedError('REVIEW_INPUT_UNSUPPORTED')
    declared = document.get('data_kind')
    if kind and declared and kind != declared:
        raise FeedError('REVIEW_KIND_CONFLICT')
    kind = kind or declared
    if kind not in KINDS:
        raise FeedError('REVIEW_KIND_REQUIRED')
    if kind != 'synthetic' and 'SYNTHETIC' in str(document.get('fixture_notice','')).upper():
        raise FeedError('REVIEW_KIND_CONFLICT')
    if document.get('review_input_version') == 1:
        if airport is not None:
            raise FeedError('REVIEW_AIRPORT_CONFLICT')
        entries = document.get('airports')
        if not isinstance(entries, dict) or not set(entries) <= set(AIRPORTS):
            raise FeedError('REVIEW_AIRPORT_REQUIRED')
    else:
        if airport not in AIRPORTS:
            raise FeedError('REVIEW_AIRPORT_REQUIRED')
        entries = {airport:{'response':document, 'provenance':{'airport_context':'User-supplied; unverified'}}}
    notices = []
    for code, entry in entries.items():
        if not isinstance(entry,dict):
            raise FeedError('REVIEW_INPUT_UNSUPPORTED')
        response = entry.get('response')
        if not isinstance(response,dict) or not isinstance(response.get('status'),str):
            raise FeedError('SCHEMA_MISMATCH')
        features = at_path(response,'data.geojson')
        if not isinstance(features,list):
            raise FeedError('SCHEMA_MISMATCH')
        for index, feature in enumerate(features):
            notices.append(interpret(feature,code,inventory_document['airports'][code[1:]],at,index,entry.get('provenance')))
    def strings(value):
        if isinstance(value,str):
            return [value]
        if isinstance(value,dict):
            return [s for child in value.values() for s in strings(child)]
        if isinstance(value,list):
            return [s for child in value for s in strings(child)]
        return []
    for source in notices:
        references = {value for hint in source['lifecycle_hints'] for value in strings(hint.get('value'))}
        for target in notices:
            if source is not target and target['airport_context']==source['airport_context'] and isinstance(target['id'],str) and target['id'] in references:
                target['candidate_state_assuming_lifecycle_confirmed'] = 'UNKNOWN'
                target['warnings'].append('RELATED_CANCELLATION_OR_REPLACEMENT_UNCERTAIN')
                target['evidence'].append({'rule':'UNVERIFIED_SOURCE_REFERENCE', 'related_review_key':source['review_key'], 'referenced_id':target['id']})
    rows = []
    for code in AIRPORTS:
        ends = inventory_document['airports'][code[1:]]
        if len(ends) != (16 if code=='KORD' else 8) or len({r['id'] for r in ends}) != len(ends):
            raise FeedError('REVIEW_INVENTORY_INVALID')
        for end in ends:
            rows.append({'airport':code, 'runway_end':end['id'], 'pair':end['pair'], 'coverage_state':'UNKNOWN',
                         'candidate_reviews':[n['review_key'] for n in notices if n['airport_context']==code and end['id'] in n['proposed_runway_ends']]})
    return {'review_version':1, 'data_kind':kind, 'review_at_utc':at, 'coverage_state':'UNKNOWN',
            'notice':'OFFLINE REVIEW ONLY. Candidates are not operational states. Missing closure information never establishes availability.',
            'inventory_provenance':{k:v for k,v in inventory_document.items() if k!='airports'},
            'runway_rows':rows, 'notices':notices, 'original_input':copy.deepcopy(document)}
