"""Surface actionable upstream errors without exposing credentials."""
import os
from request_identity import current_oidc_token

class AIRequestError(RuntimeError):
    def __init__(self, status, message):
        self.status = status
        super().__init__(f'AI request failed ({status}): {message}')

async def check_ai_response(response):
    if response.status_code < 400:
        return
    await response.aread()
    try:
        error = response.json().get('error', {})
        message = error.get('message', 'Provider refused the request') if isinstance(error, dict) else str(error)
    except (ValueError, AttributeError):
        message = 'Provider refused the request'
    for secret in (os.getenv('AI_API_KEY'), os.getenv('AI_GATEWAY_API_KEY'), current_oidc_token()):
        if secret:
            message = message.replace(secret, '[redacted]')
    raise AIRequestError(response.status_code, message[:400])
