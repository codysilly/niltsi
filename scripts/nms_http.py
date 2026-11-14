"""One bounded HTTPS call per prepared request. Import/construction perform no I/O."""
import http.client
import re
import socket
import ssl
import time
from urllib.parse import parse_qs, unquote, urlsplit

from nms_refresh_contract import AUTH, CLASSIFICATIONS, Request, instant


class HTTPAdapterError(Exception):
    def __init__(self, code):
        allowed = {'HTTP_INVALID_REQUEST', 'HTTP_TIMEOUT', 'HTTP_TLS_FAILURE',
                   'HTTP_NETWORK_FAILURE', 'HTTP_PROTOCOL_FAILURE',
                   'HTTP_REDIRECT_REFUSED', 'HTTP_RESPONSE_TOO_LARGE'}
        super().__init__(code if code in allowed else 'HTTP_PROTOCOL_FAILURE')


def validate_request(request):
    """Validate method, stage, exact production authority, path, query and headers."""
    try:
        if not isinstance(request, Request) or '#' in request.url or any(ord(c) < 33 or ord(c) > 126 for c in request.url):
            raise ValueError()
        url = urlsplit(request.url)
        if (url.scheme != 'https' or url.netloc != 'api-nms.aim.faa.gov'
                or url.fragment or url.username or url.password):
            raise ValueError()
        pairs = parse_qs(url.query, keep_blank_values=True, strict_parsing=True)
        if any(len(values) != 1 for values in pairs.values()):
            raise ValueError()
        query = {name: values[0] for name, values in pairs.items()}
        headers = {}
        for name, value in request.headers.items():
            key = name.lower()
            if (key in headers or key not in {'authorization','content-type','nmsresponseformat','accept'}
                    or not isinstance(value, str) or any(ord(c) < 32 or ord(c) > 126 for c in value)):
                raise ValueError()
            headers[key] = value
        if request.stage == 'auth':
            if (request.url != AUTH or request.method != 'POST'
                    or request.body != b'grant_type=client_credentials'
                    or headers.get('content-type') != 'application/x-www-form-urlencoded'
                    or not re.fullmatch(r'Basic [A-Za-z0-9+/]+={0,2}', headers.get('authorization',''))):
                raise ValueError()
        else:
            if request.method != 'GET' or request.body is not None or not re.fullmatch(
                    r'Bearer [!-~]+', headers.get('authorization','')):
                raise ValueError()
            if request.stage == 'delta':
                if (url.path != '/nmsapi/v1/notams' or set(query) != {'lastUpdatedDate'}
                        or headers.get('nmsresponseformat') != 'GEOJSON'
                        or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', query['lastUpdatedDate'])):
                    raise ValueError()
                instant(query['lastUpdatedDate'])
            elif request.stage == 'initial-aixm-soap':
                suffix = url.path.removeprefix('/nmsapi/v1/notams/il')
                if (not url.path.startswith('/nmsapi/v1/notams/il')
                        or (suffix and suffix not in {'/'+c for c in CLASSIFICATIONS})
                        or query != {'allowRedirect':'false'} or 'nmsresponseformat' in headers):
                    raise ValueError()
            elif request.stage == 'initial-classification-geojson':
                if (url.path != '/nmsapi/v1/notams' or set(query) != {'classification','allowRedirect'}
                        or query['classification'] not in CLASSIFICATIONS or query['allowRedirect'] != 'false'
                        or headers.get('nmsresponseformat') != 'GEOJSON'):
                    raise ValueError()
            elif request.stage == 'content':
                prefix = '/nmsapi/v1/content/'
                token = url.path[len(prefix):]
                if (query or not url.path.startswith(prefix) or not re.fullmatch(
                        r'(?:[A-Za-z0-9_~+=-]|%[0-9A-Fa-f]{2})+', token)
                        or not re.fullmatch(r'[A-Za-z0-9_~+/=-]+', unquote(token))):
                    raise ValueError()
            else:
                raise ValueError()
        return url.path+('?' + url.query if url.query else '')
    except Exception:
        raise HTTPAdapterError('HTTP_INVALID_REQUEST') from None


class HTTPAdapter:
    """Direct HTTPS, system CA verification, no proxies/redirects/retries.

    Timeout bounds socket operations and the elapsed read loop. OS DNS resolution
    remains subject to OS resolver timing; it is not represented as a hard wall
    clock cancellation guarantee. connection_factory is a test seam, not a default
    fake or an alternate-host escape. Nothing connects until __call__.
    """
    def __init__(self, *, timeout=30, max_bytes=64*1024*1024,
                 connection_factory=None, monotonic=time.monotonic):
        if type(timeout) not in (int,float) or not 0 < timeout <= 30 or type(max_bytes) is not int or not 0 < max_bytes <= 1024**3:
            raise HTTPAdapterError('HTTP_INVALID_REQUEST')
        self.timeout, self.max_bytes = timeout, max_bytes
        self.factory = connection_factory or http.client.HTTPSConnection
        self.clock = monotonic

    def __call__(self, request, limit):
        target = validate_request(request)
        if type(limit) is not int or limit < 1:
            raise HTTPAdapterError('HTTP_INVALID_REQUEST')
        bound = min(limit, self.max_bytes)
        connection = response = active_socket = None
        deadline = self.clock()+self.timeout
        def remaining():
            value = deadline-self.clock()
            if value <= 0:
                raise TimeoutError()
            if active_socket is not None:
                active_socket.settimeout(value)
            return value
        try:
            context = ssl.create_default_context()
            # No caller-supplied insecure context or TLS bypass flag.
            connection = self.factory('api-nms.aim.faa.gov', port=443,
                                      timeout=remaining(), context=context)
            connection.debuglevel = 0
            connection.request(request.method, target, body=request.body, headers=dict(request.headers))
            active_socket = connection.sock
            remaining()
            response = connection.getresponse()
            remaining()
            status = response.status
            if type(status) is not int or not 100 <= status <= 599:
                raise HTTPAdapterError('HTTP_PROTOCOL_FAILURE')
            if 300 <= status <= 399:
                raise HTTPAdapterError('HTTP_REDIRECT_REFUSED')
            if status != 200:
                # Never read upstream error bodies or expose response headers.
                return status, {}, b''
            length = response.getheader('Content-Length')
            if length is not None:
                if not re.fullmatch(r'\d+', length):
                    raise HTTPAdapterError('HTTP_PROTOCOL_FAILURE')
                if int(length) > bound:
                    raise HTTPAdapterError('HTTP_RESPONSE_TOO_LARGE')
            result = bytearray()
            while True:
                remaining()
                chunk = response.read1(min(65536, bound+1-len(result)))
                remaining()
                if not isinstance(chunk, bytes):
                    raise HTTPAdapterError('HTTP_PROTOCOL_FAILURE')
                if not chunk:
                    break
                result.extend(chunk)
                if len(result) > bound:
                    raise HTTPAdapterError('HTTP_RESPONSE_TOO_LARGE')
            if length is not None and len(result) != int(length):
                raise HTTPAdapterError('HTTP_PROTOCOL_FAILURE')
            return status, {}, bytes(result)
        except HTTPAdapterError:
            raise
        except (TimeoutError, socket.timeout):
            raise HTTPAdapterError('HTTP_TIMEOUT') from None
        except ssl.SSLError:
            raise HTTPAdapterError('HTTP_TLS_FAILURE') from None
        except OSError:
            raise HTTPAdapterError('HTTP_NETWORK_FAILURE') from None
        except Exception:
            raise HTTPAdapterError('HTTP_PROTOCOL_FAILURE') from None
        finally:
            for resource in (response, connection):
                if resource is not None:
                    try:
                        resource.close()
                    except Exception:
                        pass  # Cleanup details may contain headers/tokens; never log them.
