"""Bounded JSON transport; never expose upstream error bodies or credentials."""
import json
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class RemoteError(Exception):
    def __init__(self, code, category='transport_error', status=503, provider_code=None):
        self.code, self.category, self.status = code, category, status
        self.provider_code = provider_code
        super().__init__(code)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(url, data=None, *, timeout=10, token=None, max_bytes=4*1024*1024, trusted_errors=False):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer '+token
    request = Request(url, data=json.dumps(data).encode() if data is not None else None, headers=headers)
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=max(.1, timeout)) as response:
            body = response.read(max_bytes+1)
            if len(body) > max_bytes:
                raise RemoteError('response_too_large', 'protocol_error', 502)
            return json.loads(body)
    except HTTPError as exc:
        # Internal callers may use stable code/class; no upstream message/body is propagated.
        code, category = 'http_'+str(exc.code), 'transport_error'
        provider_code = None
        try:
            error = json.loads(exc.read(16384)).get('error', {})
            candidate = error.get('code', '')
            if trusted_errors:
                if isinstance(candidate, str) and candidate.replace('_','').isalnum() and len(candidate) <= 80:
                    code = candidate
                if error.get('class') in ('protocol_error','command_error','timeout','sandbox_error','transport_error','model_error'):
                    category = error['class']
            elif candidate == 'credit_balance_exhausted' or error.get('type') == 'insufficient_quota':
                provider_code = 'insufficient_quota'
            elif candidate in ('insufficient_quota','rate_limit_exceeded','model_not_found','invalid_api_key',
                               'unsupported_country_region_territory','unsupported_parameter'):
                provider_code = candidate
        except (ValueError, AttributeError, TypeError):
            pass
        raise RemoteError(code, category, exc.code, provider_code) from None
    except (TimeoutError, URLError, OSError):
        raise RemoteError('transport_failure') from None
    except (ValueError, TypeError):
        raise RemoteError('invalid_json_response', 'protocol_error', 502) from None
