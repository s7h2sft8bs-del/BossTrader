import os
import hmac
import hashlib
import secrets
from datetime import datetime, timezone

def new_api_key() -> str:
    return secrets.token_urlsafe(32)

def is_paid_active(user) -> bool:
    # hard disable switch
    if not getattr(user, "is_active", False):
        return False
    # must have paid_until set and not expired
    paid_until = getattr(user, "paid_until", None)
    if paid_until is None:
        return False
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return paid_until >= now

def sign(payload: str) -> str:
    secret = os.getenv("APP_SECRET", "")
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

def safe_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a or "", b or "")
