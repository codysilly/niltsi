"""Explicit local history import. Default is a read-only review; never contacts NMS."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from datetime import datetime, timezone, timedelta

from capture_storage import platform_storage
from nms_refresh import Store, SCHEMA, encode
from nms_refresh_contract import instant


class HistoryImportError(Exception):
    CODES = set('''IMPORT_INVALID_MANIFEST IMPORT_MISSING_FIELD IMPORT_UNKNOWN_FIELD
    IMPORT_IDENTITY_UNVERIFIED IMPORT_HISTORY_INCOMPLETE IMPORT_INVALID_TIME
    IMPORT_CONTRADICTION IMPORT_UNBOUNDED_UNCERTAINTY IMPORT_EVIDENCE_MISMATCH
    IMPORT_INVALID_SNAPSHOT IMPORT_TARGET_EXISTS IMPORT_INVALID_TARGET
    IMPORT_APPROVAL_MISMATCH IMPORT_STORAGE_FAILURE IMPORT_INTERRUPTED
    IMPORT_FINALIZATION_UNCERTAIN'''.split())

    def __init__(self, code):
        super().__init__(code if code in self.CODES else 'IMPORT_STORAGE_FAILURE')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def checked_object(value, required, optional=()):
    if not isinstance(value, dict):
        raise HistoryImportError('IMPORT_INVALID_MANIFEST')
    if not set(required) <= set(value):
        raise HistoryImportError('IMPORT_MISSING_FIELD')
    if set(value)-set(required)-set(optional):
        raise HistoryImportError('IMPORT_UNKNOWN_FIELD')


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    try:
        return json.loads(data, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except Exception:
        raise HistoryImportError('IMPORT_INVALID_MANIFEST') from None


def read_bounded(path, maximum):
    try:
        with Path(path).open('rb') as stream:
            data = stream.read(maximum+1)
        if len(data) > maximum:
            raise ValueError()
        return data
    except Exception:
        raise HistoryImportError('IMPORT_EVIDENCE_MISMATCH') from None


def utc(value):
    try:
        return instant(value)
    except Exception:
        raise HistoryImportError('IMPORT_INVALID_TIME') from None


def validate_snapshot(data):
    try:
        value = strict_json(data)
        utc(value['retrieved_utc'])
        if (not isinstance(value['airports'], dict) or 'KMEM' not in value['airports']
                or value.get('synthetic') is True or ('synthetic' in value and type(value['synthetic']) is not bool)):
            raise ValueError()
        for code, entry in value['airports'].items():
            if not re.fullmatch(r'[A-Z0-9]{3,4}', code) or not isinstance(entry['notices'], list):
                raise ValueError()
            for key in ['warning','completeness']:
                if key in entry and not isinstance(entry[key], str):
                    raise ValueError()
            for notice in entry['notices']:
                if not isinstance(notice, dict):
                    raise ValueError()
                for key in ['text','id','number','source','effectiveStart','effectiveEnd','retrieved_utc','scope']:
                    if notice.get(key) is not None and not isinstance(notice[key], str):
                        raise ValueError()
                if 'warnings' in notice and (not isinstance(notice['warnings'], list) or any(not isinstance(w,str) for w in notice['warnings'])):
                    raise ValueError()
                if 'formatted_texts' in notice:
                    if not isinstance(notice['formatted_texts'], list):raise ValueError()
                    for text in notice['formatted_texts']:
                        if not isinstance(text['text'], str) or any(key in text and not isinstance(text[key],str) for key in ['type','field']):
                            raise ValueError()
        return value
    except Exception:
        raise HistoryImportError('IMPORT_INVALID_SNAPSHOT') from None


def _review(manifest_path, target, *, clock, existing_target=False):
    """No writes: validate explicit history, byte hashes, identity and destination."""
    target = Path(target)
    if not existing_target and (target.exists() or target.is_symlink()):
        raise HistoryImportError('IMPORT_TARGET_EXISTS')
    if not target.is_absolute() or not target.parent.is_dir() or any((p/'.git').exists() for p in [target,*target.parents]):
        raise HistoryImportError('IMPORT_INVALID_TARGET')
    try:
        platform_storage().validate_location(target)
    except Exception:
        raise HistoryImportError('IMPORT_INVALID_TARGET') from None
    raw = read_bounded(manifest_path, 1024*1024)
    manifest = strict_json(raw)
    checked_object(manifest, ['schema_version','account','history','attempts','uncertainties','prior_snapshot'])
    if type(manifest['schema_version']) is not int or manifest['schema_version'] != 1:
        raise HistoryImportError('IMPORT_INVALID_MANIFEST')
    account = manifest['account']
    checked_object(account, ['logical_id','identity_reference','identity_confirmed','all_clients_quiesced'])
    if (not isinstance(account['logical_id'], str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}',account['logical_id'])
            or not isinstance(account['identity_reference'],str) or not account['identity_reference'].strip()
            or account['identity_confirmed'] is not True or account['all_clients_quiesced'] is not True):
        raise HistoryImportError('IMPORT_IDENTITY_UNVERIFIED')
    history = manifest['history']
    checked_object(history, ['inventory_complete','complete_through_utc','declaration_reference'])
    if history['inventory_complete'] is not True or not isinstance(history['declaration_reference'],str) or not history['declaration_reference'].strip():
        raise HistoryImportError('IMPORT_HISTORY_INCOMPLETE')
    through = utc(history['complete_through_utc'])
    if through > clock():
        raise HistoryImportError('IMPORT_INVALID_TIME')
    attempts, uncertainties = manifest['attempts'], manifest['uncertainties']
    if not isinstance(attempts,list) or not isinstance(uncertainties,list):
        raise HistoryImportError('IMPORT_INVALID_MANIFEST')
    ids=set(); evidence=[]; calls=0; untimed=0; rows=[]; uncertain_ids=set()
    stages={'auth','initial-aixm-soap','initial-classification-geojson','content','delta','other-data'}
    for index, attempt in enumerate(attempts):
        checked_object(attempt, ['id','kind','reserved_utc','last_io_utc','last_io_is_upper_bound','outcome','receipt','calls','known_call_count'])
        if (not isinstance(attempt['id'],str) or not attempt['id'] or attempt['id'] in ids
                or attempt['kind'] not in ('bulk','delta','other-data')
                or attempt['outcome'] not in ('succeeded','failed','uncertain')):
            raise HistoryImportError('IMPORT_CONTRADICTION')
        ids.add(attempt['id'])
        start, end = utc(attempt['reserved_utc']), utc(attempt['last_io_utc'])
        if start > end or end > through:
            raise HistoryImportError('IMPORT_CONTRADICTION')
        uncertain = attempt['outcome']=='uncertain'
        if type(attempt['last_io_is_upper_bound']) is not bool or (uncertain and not attempt['last_io_is_upper_bound']):
            raise HistoryImportError('IMPORT_UNBOUNDED_UNCERTAINTY')
        if attempt['last_io_is_upper_bound'] and not uncertain:
            raise HistoryImportError('IMPORT_CONTRADICTION')
        if uncertain:
            uncertain_ids.add(attempt['id'])
        receipt=attempt['receipt']; checked_object(receipt,['path','sha256'])
        if not Path(receipt['path']).is_absolute() or not re.fullmatch(r'[a-f0-9]{64}',receipt['sha256']):
            raise HistoryImportError('IMPORT_EVIDENCE_MISMATCH')
        receipt_bytes=read_bounded(receipt['path'],4*1024*1024)
        if digest(receipt_bytes)!=receipt['sha256']:
            raise HistoryImportError('IMPORT_EVIDENCE_MISMATCH')
        evidence.append({'index':index,'sha256':receipt['sha256']})
        if not isinstance(attempt['calls'],list) or type(attempt['known_call_count']) is not int:
            raise HistoryImportError('IMPORT_INVALID_MANIFEST')
        if attempt['known_call_count']!=len(attempt['calls']):
            raise HistoryImportError('IMPORT_CONTRADICTION')
        # This repository's SPA-26 receipt has defined pre-I/O auth/GET counters.
        # Other receipt formats remain hash-backed evidence for operator review;
        # their field names are not guessed into accounting semantics.
        try:
            receipt_value=strict_json(receipt_bytes)
        except HistoryImportError:
            receipt_value={}
        if isinstance(receipt_value,dict) and receipt_value.get('ledger')=='SPA-26':
            for field,number in [('auth_attempts',sum(c.get('stage')=='auth' for c in attempt['calls'] if isinstance(c,dict))),
                                 ('data_attempts',sum(c.get('stage')!='auth' for c in attempt['calls'] if isinstance(c,dict)))]:
                if type(receipt_value.get(field)) is not int or receipt_value[field]!=number:
                    raise HistoryImportError('IMPORT_CONTRADICTION')
        last=start
        for call in attempt['calls']:
            checked_object(call,['stage','at_utc','certainty'])
            if call['at_utc'] is None:
                if not uncertain or call['certainty']!='unknown-time' or call['stage'] not in stages:
                    raise HistoryImportError('IMPORT_UNBOUNDED_UNCERTAINTY')
                untimed+=1;calls+=1
                continue  # Retain a counted, explicitly unknown time; never fill one in.
            when=utc(call['at_utc'])
            if call['stage'] not in stages or call['certainty'] not in ('observed','reserved') or when<last or when>end:
                raise HistoryImportError('IMPORT_CONTRADICTION')
            last=when;calls+=1
        rows.append({'index':index,'kind':attempt['kind'],'reserved_utc':attempt['reserved_utc'],
                     'last_io_utc':attempt['last_io_utc'],'last_io_is_upper_bound':attempt['last_io_is_upper_bound'],
                     'outcome':attempt['outcome'],'known_calls':len(attempt['calls']),'receipt_sha256':receipt['sha256']})
    references=set()
    for item in uncertainties:
        checked_object(item,['attempt_id','code'])
        if (item['attempt_id'] not in uncertain_ids or item['attempt_id'] in references
                or item['code'] not in ('OUTCOME_UNKNOWN','TIMING_UNCERTAIN','ADDITIONAL_CALLS_UNKNOWN')):
            raise HistoryImportError('IMPORT_CONTRADICTION')
        references.add(item['attempt_id'])
    if references!=uncertain_ids:
        raise HistoryImportError('IMPORT_UNBOUNDED_UNCERTAINTY')
    prior=manifest['prior_snapshot']; checked_object(prior,['path','bytes','sha256','approved_for_display','origin_attempt_id'])
    if (not Path(prior['path']).is_absolute() or type(prior['bytes']) is not int
            or not 0<prior['bytes']<=64*1024*1024 or prior['approved_for_display'] is not True):
        raise HistoryImportError('IMPORT_INVALID_SNAPSHOT')
    snapshot=read_bounded(prior['path'],64*1024*1024)
    if len(snapshot)!=prior['bytes'] or digest(snapshot)!=prior['sha256']:
        raise HistoryImportError('IMPORT_EVIDENCE_MISMATCH')
    parsed=validate_snapshot(snapshot)
    if utc(parsed['retrieved_utc']) > clock():
        raise HistoryImportError('IMPORT_INVALID_SNAPSHOT')
    origin=next((a for a in attempts if a['id']==prior['origin_attempt_id']),None)
    if (origin is None or origin['outcome']=='failed' or utc(parsed['retrieved_utc'])<utc(origin['reserved_utc'])
            or utc(parsed['retrieved_utc'])>through or not any(c['stage']!='auth' for c in origin['calls'])):
        raise HistoryImportError('IMPORT_CONTRADICTION')
    approval=digest(encode({'manifest_sha256':digest(raw),'target':str(target.resolve()),
                            'snapshot_sha256':digest(snapshot),'receipts':evidence}))
    report={'approval_digest':approval,'manifest_sha256':digest(raw),'logical_account':account['logical_id'],
            'account_reference_sha256':digest(account['identity_reference'].encode()),
            'attempt_count':len(attempts),'known_call_count':calls,'uncertain_attempt_count':len(uncertain_ids),
            'known_calls_without_timestamps':untimed,
            'history_verified':not bool(uncertain_ids),'activation_hold':bool(uncertain_ids),
            'history_complete_through_utc':history['complete_through_utc'],'attempts':rows,
            'prior_snapshot_bytes':len(snapshot),'prior_snapshot_sha256':digest(snapshot),
            'minimum_primary_not_before_utc':max(utc(a['last_io_utc'])+timedelta(minutes=3) for a in attempts).isoformat(),
            'minimum_bulk_not_before_utc':max([utc(a['last_io_utc'])+timedelta(minutes=3) for a in attempts]+
                [utc(a['last_io_utc'])+timedelta(hours=24) for a in attempts if a['kind']=='bulk']).isoformat(),
            'baseline':False,'watermark':None,'writes_performed':False,'provider_requests_authorized':False}
    return report, manifest, raw, snapshot


def review(manifest_path, target, *, clock=lambda:datetime.now(timezone.utc), _existing_target=False):
    try:
        return _review(manifest_path,target,clock=clock,existing_target=_existing_target)
    except HistoryImportError:
        raise
    except Exception:
        raise HistoryImportError('IMPORT_INVALID_MANIFEST') from None


def import_history(manifest_path, target, *, approve, clock=lambda:datetime.now(timezone.utc), storage=None, checkpoint=None):
    """Fresh target only. No resume/reset; interruptions leave a blocked audit artifact."""
    report, manifest, raw, snapshot = review(manifest_path,target,clock=clock)
    if approve != report['approval_digest']:
        raise HistoryImportError('IMPORT_APPROVAL_MISMATCH')
    storage=storage or platform_storage();target=Path(target);db=None;created=False;finalizing=False
    def point(name):
        if checkpoint is not None:
            checkpoint(name)  # Test-only fault seam; no default hook or network.
    try:
        storage.create_attempt(target);created=True
        marker={'schema':1,'state':'PENDING','approval_digest':approve}
        storage.private_write(target/'import-status.json',encode(marker))
        storage.private_write(target/'import-manifest.private.json',raw)
        point('pending')
        for name in ['account.sqlite3','account.sqlite3-journal']:
            storage.private_write(target/name,b'')
        db=sqlite3.connect((target/'account.sqlite3').as_uri()+'?mode=rw',uri=True,isolation_level=None)
        if db.execute('PRAGMA journal_mode=PERSIST').fetchone()[0]!='persist':raise ValueError()
        db.execute('PRAGMA synchronous=EXTRA')
        db.executescript(SCHEMA)
        held=report['activation_hold']
        state={'schema':1,'account':manifest['account']['logical_id'],'history_verified':False,
               'history_complete_through':manifest['history']['complete_through_utc'],
               'scope':'all-classifications','airport':'KMEM','baseline':False,'watermark':None,
               'active':None,'recovery':'IMPORT_IN_PROGRESS','catalog':{},
               'published_b64':base64.b64encode(snapshot).decode(),'attempt_count':report['attempt_count'],
               'call_count':report['known_call_count'],'response_count':0,'import_digest':approve}
        db.execute('BEGIN IMMEDIATE')
        db.execute('INSERT INTO state VALUES (1,?)',(encode(state).decode(),))
        for attempt in manifest['attempts']:
            status='IMPORTED_UNCERTAIN' if attempt['outcome']=='uncertain' else 'IMPORTED_'+attempt['outcome'].upper()
            db.execute('INSERT INTO attempts VALUES (?,?,?,?,?,?)',(attempt['id'],attempt['kind'],
                attempt['reserved_utc'],attempt['last_io_utc'],status,'IMPORTED_UNCERTAINTY' if held else None))
            for index,call in enumerate(attempt['calls']):
                db.execute('INSERT INTO calls VALUES (?,?,?)',(attempt['id'],'imported-'+str(index)+'-'+call['stage'],
                           call['at_utc'] if call['at_utc'] is not None else 'UNVERIFIED_TIME'))
        db.execute('COMMIT');point('history-written')
        if db.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:raise ValueError()
        if db.execute('SELECT count(*) FROM attempts').fetchone()[0]!=report['attempt_count']:raise ValueError()
        if db.execute('SELECT count(*) FROM calls').fetchone()[0]!=report['known_call_count']:raise ValueError()
        state.update(history_verified=not held,recovery='IMPORTED_UNCERTAINTY' if held else None)
        db.execute('BEGIN IMMEDIATE');db.execute('UPDATE state SET document=? WHERE singleton=1',(encode(state).decode(),));db.execute('COMMIT')
        db.close();db=None
        for path in target.iterdir():storage.verify_private(path)
        # Re-read source hashes immediately before finalization; never edit evidence.
        second,_,_,_=review(manifest_path,target,clock=clock,_existing_target=True)
        if second['approval_digest']!=approve:
            raise HistoryImportError('IMPORT_EVIDENCE_MISMATCH')
        point('validated')
        marker['state']='HELD_UNCERTAINTY' if held else 'READY'
        storage.private_write(target/'import-status.next',encode(marker))
        finalizing=True
        storage.replace(target/'import-status.next',target/'import-status.json')
        if (target/'import-status.json').read_bytes()!=encode(marker):raise ValueError()
        point('finalized')
        if not held:
            store=Store(target,account=manifest['account']['logical_id'],storage=storage)
            if store.snapshot()!=snapshot:raise ValueError()
        return {**report,'writes_performed':True,'status':'IMPORTED_HELD' if held else 'IMPORTED_READY_NO_BASELINE'}
    except FileExistsError:
        raise HistoryImportError('IMPORT_TARGET_EXISTS') from None
    except HistoryImportError:
        raise
    except Exception:
        raise HistoryImportError('IMPORT_FINALIZATION_UNCERTAIN' if finalizing else
                                 'IMPORT_INTERRUPTED' if created else 'IMPORT_STORAGE_FAILURE') from None
    finally:
        if db is not None:
            if db.in_transaction:db.rollback()
            db.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',required=True);parser.add_argument('--target',required=True)
    parser.add_argument('--write',action='store_true');parser.add_argument('--approve')
    args=parser.parse_args()
    try:
        report,_,_,_=review(args.manifest,args.target)
        print(json.dumps(report,indent=2),flush=True)  # Review precedes the first write.
        if args.write:
            result=import_history(args.manifest,args.target,approve=args.approve)
            print(json.dumps({'status':result['status'],'activation_hold':result['activation_hold']}))
        elif args.approve:
            raise HistoryImportError('IMPORT_INVALID_MANIFEST')
    except HistoryImportError as error:
        print(str(error));raise SystemExit(1) from None
    except Exception:
        print('IMPORT_INVALID_MANIFEST');raise SystemExit(1) from None


if __name__=='__main__':main()
