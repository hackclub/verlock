"""Keep Vercel's per-request identity out of process-wide environment state."""
from contextvars import ContextVar
import os

_oidc_token = ContextVar('verlock_oidc_token', default=None)

def current_oidc_token():
    return _oidc_token.get() or os.getenv('VERCEL_OIDC_TOKEN')

class VercelIdentityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        headers = dict(scope.get('headers', []))
        value = headers.get(b'x-vercel-oidc-token') if os.getenv('VERCEL') else None
        token = _oidc_token.set(value.decode() if value else None)
        try:
            await self.app(scope, receive, send)
        finally:
            _oidc_token.reset(token)
