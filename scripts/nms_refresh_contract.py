"""Documented request preparation only. No network, environment or credential reads."""
import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
import re
from urllib.parse import urlencode, urlsplit

BASE = 'https://api-nms.aim.faa.gov/nmsapi'
AUTH = 'https://api-nms.aim.faa.gov/v1/auth/token'
CLASSIFICATIONS = {'INTERNATIONAL', 'MILITARY', 'LOCAL_MILITARY', 'DOMESTIC', 'FDC'}


class RefreshError(Exception):
    """Only fixed codes cross the collector boundary, never upstream exception text."""
    CODES = set('''INVALID_TIME INVALID_REQUEST INVALID_CREDENTIALS AUTH_FAILED HTTP_FAILED
    INVALID_RESPONSE INVALID_CONTENT_PATH CONTENT_EXPIRED DECODER_REQUIRED BASELINE_CUTOVER_REQUIRED
    BASELINE_REQUIRED DELTA_GAP CLOCK_ROLLBACK BUDGET_WAIT BUSY RECOVERY_REQUIRED
    HISTORY_OR_STORAGE_UNAVAILABLE PERSISTENCE_UNCERTAIN TRANSPORT_FAILED RESPONSE_TOO_LARGE
    CONFLICT INVALID_RECORD PUBLICATION_FAILED LOCAL_FAILURE'''.split())

    def __init__(self, code):
        super().__init__(code if code in self.CODES else 'LOCAL_FAILURE')


def instant(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None or result.utcoffset() != timedelta(0):
            raise ValueError()
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        raise RefreshError('INVALID_TIME') from None


def stamp(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise RefreshError('INVALID_TIME')
    return value.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class Request:
    stage: str
    url: str = field(repr=False)
    headers: dict = field(default_factory=dict, repr=False)
    body: bytes | None = field(default=None, repr=False)
    method: str = 'GET'
    # An injected transport must implement exactly one call, without redirects/retries.


@dataclass(frozen=True)
class DecodedInitial:
    payload: dict = field(repr=False)
    # Reviewed decoder evidence: a lower bound from which the first delta must replay.
    # This is NOT an invented provider response field or the local download time.
    coverage_since: str


def auth_request(credentials):
    try:
        client, secret = credentials['client_id'], credentials['client_secret']
        if not all(isinstance(v, str) and v for v in (client, secret)) or ':' in client:
            raise ValueError()
        basic = base64.b64encode((client+':'+secret).encode()).decode()
    except (KeyError, TypeError, ValueError):
        raise RefreshError('INVALID_CREDENTIALS') from None
    return Request('auth', AUTH, {'Authorization': 'Basic '+basic,
                   'Content-Type': 'application/x-www-form-urlencoded'},
                   b'grant_type=client_credentials', 'POST')


def initial_request(classification=None):
    if classification is None:
        # Legacy IL is documented as compressed AIXM in a SOAP envelope, NOT GeoJSON.
        return Request('initial-aixm-soap', BASE+'/v1/notams/il?allowRedirect=false')
    if classification not in CLASSIFICATIONS:
        raise RefreshError('INVALID_REQUEST')
    return Request('initial-classification-geojson', BASE+'/v1/notams?'+urlencode({
        'classification': classification, 'allowRedirect': 'false'}),
        {'nmsResponseFormat': 'GEOJSON'})


def delta_request(since, now):
    start = instant(since)
    current = instant(stamp(now))
    if start > current:
        raise RefreshError('CLOCK_ROLLBACK')
    if current-start > timedelta(hours=24):
        raise RefreshError('DELTA_GAP')
    # Floor to the documented whole-second wire format; replay overlap is intentional.
    wire = start.replace(microsecond=0).strftime('%Y-%m-%dT%H:%M:%SZ')
    return Request('delta', BASE+'/v1/notams?'+urlencode({'lastUpdatedDate': wire}),
                   {'nmsResponseFormat': 'GEOJSON'})


def content_request(path, created, now):
    if instant(stamp(now))-instant(created) >= timedelta(minutes=5):
        raise RefreshError('CONTENT_EXPIRED')
    if not isinstance(path, str):
        raise RefreshError('INVALID_CONTENT_PATH')
    try:
        parsed = urlsplit(path)
    except ValueError:
        raise RefreshError('INVALID_CONTENT_PATH') from None
    # OpenAPI paths are relative to /nmsapi; FAQ also shows that prefix explicitly.
    relative = path[len('/nmsapi'):] if path.startswith('/nmsapi/') else path
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or not re.fullmatch(
            r'/v1/content/(?:[A-Za-z0-9_~+=-]|%[0-9A-Fa-f]{2})+', relative):
        raise RefreshError('INVALID_CONTENT_PATH')
    return Request('content', BASE+relative)
