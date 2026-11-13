"""Single supervised MEM capture. No scheduler, retries, redirects, or publication."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
from datetime import datetime, timezone
import urllib.error
import urllib.parse
import urllib.request
import uuid

from normalize_notam_snapshot import normalize

AUTH_URL = 'https://api-nms.aim.faa.gov/v1/auth/token'
DATA_URL = 'https://api-nms.aim.faa.gov/nmsapi/v1/notams?location=KMEM'
QUARANTINE_FLOOR = datetime.fromisoformat('2026-11-13T07:44:03.558+00:00')
REPOSITORY = Path(__file__).resolve().parents[1]


class CaptureError(Exception):
    """Only fixed, non-sensitive diagnostic codes leave this module."""


def utc_now():
    return datetime.now(timezone.utc)


def timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None:
            raise ValueError()
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        raise CaptureError('INVALID_RESERVATION_TIME') from None


def check_reservation(reservation, now):
    if not isinstance(reservation, dict):
        raise CaptureError('INVALID_RESERVATION')
    required = {'schema_version': 1, 'ledger': 'SPA-26', 'state': 'RESERVED',
                'airport': 'KMEM', 'collector': 'mem-budget',
                'auth_attempt_limit': 1, 'data_attempt_limit': 1}
    if any(reservation.get(key) != value for key, value in required.items()):
        raise CaptureError('INVALID_RESERVATION')
    try:
        uuid.UUID(reservation['reservation_id'])
    except (KeyError, TypeError, ValueError, AttributeError):
        raise CaptureError('INVALID_RESERVATION_ID') from None
    not_before = timestamp(reservation.get('not_before_utc'))
    reserved = timestamp(reservation.get('reserved_utc'))
    expires = timestamp(reservation.get('expires_utc'))
    if not_before < QUARANTINE_FLOOR or reserved < not_before or expires <= reserved:
        raise CaptureError('INVALID_RESERVATION_WINDOW')
    if now < not_before or now < reserved:
        raise CaptureError('QUARANTINE_OR_RESERVATION_NOT_YET_ELIGIBLE')
    if now >= expires:
        raise CaptureError('RESERVATION_EXPIRED')
    value = reservation.get('output_dir')
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise CaptureError('PRIVATE_ABSOLUTE_OUTPUT_REQUIRED')
    output = Path(value).resolve()
    if output == REPOSITORY or REPOSITORY in output.parents:
        raise CaptureError('OUTPUT_MUST_BE_OUTSIDE_REPOSITORY')
    return output


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CaptureError('REDIRECT_BLOCKED')


def send_once(request, limit):
    """One urllib request, with default proxy/CA settings; never a retry loop."""
    if request.full_url not in (AUTH_URL, DATA_URL):
        raise CaptureError('UNAPPROVED_ENDPOINT')
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=30) as response:
            body = response.read(limit + 1)
            if len(body) > limit:
                raise CaptureError('RESPONSE_TOO_LARGE')
            return response.status, dict(response.headers), body
    except urllib.error.HTTPError as error:
        # Do not read or disclose error bodies, locations, credentials or headers.
        return error.code, {}, b''
    except CaptureError:
        raise
    except Exception:
        raise CaptureError('TRANSPORT_FAILED_ATTEMPT_CONSUMED') from None


def private_write(path, content):
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def receipt_write(output, receipt):
    temporary = output / 'receipt.next'
    private_write(temporary, json.dumps(receipt, indent=2).encode())
    os.replace(temporary, output / 'receipt.json')
    # Persist the directory entry before the next network attempt.
    descriptor = os.open(output, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def capture(reservation, *, env=None, send=send_once, clock=utc_now):
    output = check_reservation(reservation, clock())
    env = os.environ if env is None else env
    client_id, secret = env.get('FAA_NMS_CLIENT_ID'), env.get('FAA_NMS_CLIENT_SECRET')
    if not client_id or not secret:
        raise CaptureError('CREDENTIALS_MISSING')
    try:
        # A used or failed attempt directory is never overwritten or reused.
        output.mkdir(mode=0o700, parents=False, exist_ok=False)
    except FileExistsError:
        raise CaptureError('ATTEMPT_DIRECTORY_EXISTS_DO_NOT_RETRY') from None
    receipt = {'ledger': 'SPA-26', 'reservation_id': reservation['reservation_id'],
               'auth_attempts': 0, 'data_attempts': 0, 'state': 'RESERVED'}
    try:
        receipt['auth_attempts'] = 1
        receipt['state'] = 'AUTH_ATTEMPT_RESERVED'
        receipt_write(output, receipt)
        credentials = base64.b64encode((client_id + ':' + secret).encode()).decode()
        request = urllib.request.Request(AUTH_URL, data=b'grant_type=client_credentials',
                  headers={'Authorization': 'Basic ' + credentials,
                           'Content-Type': 'application/x-www-form-urlencoded'}, method='POST')
        status, _, body = send(request, 65536)
        receipt["auth_http_status"] = status
        if status != 200:
            raise CaptureError('AUTH_HTTP_FAILURE')
        try:
            token = json.loads(body)['access_token']
            if not isinstance(token, str) or not token or '\n' in token or '\r' in token:
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise CaptureError('AUTH_RESPONSE_INVALID') from None
        check_reservation(reservation, clock())
        receipt['data_attempts'] = 1
        receipt['state'] = 'DATA_ATTEMPT_RESERVED'
        receipt_write(output, receipt)
        request = urllib.request.Request(DATA_URL, headers={
            'Authorization': 'Bearer ' + token, 'nmsResponseFormat': 'GEOJSON',
            'Accept': 'application/json'}, method='GET')
        status, headers, body = send(request, 8 * 1024 * 1024)
        retrieved = clock().isoformat()
        receipt.update(http_status=status, retrieved_utc=retrieved,
                       query_context={'location': 'KMEM'})
        request_id = next((v for k, v in headers.items() if k.lower() == 'x-request-id'), '')
        if isinstance(request_id, str) and re.fullmatch(r'[0-9a-fA-F-]{36}', request_id):
            receipt['request_id'] = request_id
        if status != 200:
            raise CaptureError('DATA_HTTP_FAILURE')
        private_write(output / 'response.json', body)
        receipt['sha256'] = hashlib.sha256(body).hexdigest()
        try:
            payload = json.loads(body)
            normalized = normalize(payload, airport='KMEM', retrieved_utc=retrieved,
                                   query_context={'location': 'KMEM'})
        except (ValueError, TypeError, KeyError):
            raise CaptureError('INLINE_RESPONSE_OR_NORMALIZATION_REJECTED') from None
        private_write(output / 'normalized.private.json', json.dumps(normalized, ensure_ascii=False).encode())
        receipt.update(state='CAPTURED_UNVERIFIED', record_count=len(payload['data']['geojson']))
        receipt_write(output, receipt)
        return receipt
    except Exception as error:
        code = str(error) if isinstance(error, CaptureError) else 'LOCAL_FAILURE_ATTEMPT_CONSUMED'
        receipt.update(state='STOPPED_DO_NOT_RETRY', error_code=code)
        receipt_write(output, receipt)
        raise CaptureError(code) from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reservation', required=True, help='private local reservation recorded and read back in SPA-26')
    parser.add_argument('--execute', action='store_true', help='explicit one-shot execution; never use during quarantine')
    args = parser.parse_args()
    try:
        reservation = json.loads(Path(args.reservation).read_text())
        check_reservation(reservation, utc_now())
        if not args.execute:
            print('Reservation shape/time eligible. No requests made; no Linear verification performed by this script.')
            return
        receipt = capture(reservation)
        print(json.dumps(receipt))
    except CaptureError as error:
        print('Capture stopped: ' + str(error))
        raise SystemExit(1) from None
    except Exception:
        print('Capture stopped: LOCAL_INPUT_OR_STORAGE_FAILURE. Do not retry a possibly consumed reservation.')
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
