"""Print fresh secrets for a Supabase deployment, ready to paste into .env.

ANON_KEY and SERVICE_ROLE_KEY are HS256 JWTs signed with JWT_SECRET, so all three
must come from the same run. Standard library only.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time

TEN_YEARS = 10 * 365 * 24 * 3600


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _sign(secret: str, role: str) -> str:
    now = int(time.time())
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url(
        json.dumps(
            {"role": role, "iss": "supabase", "iat": now, "exp": now + TEN_YEARS}
        ).encode()
    )
    signature = hmac.new(
        secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256
    ).digest()
    return f"{header}.{payload}.{_b64url(signature)}"


def main() -> None:
    jwt_secret = secrets.token_urlsafe(48)
    print(f"POSTGRES_PASSWORD = {secrets.token_urlsafe(32)}")
    print(f"JWT_SECRET = {jwt_secret}")
    print(f"ANON_KEY = {_sign(jwt_secret, 'anon')}")
    print(f"SERVICE_ROLE_KEY = {_sign(jwt_secret, 'service_role')}")
    print(f"PG_META_CRYPTO_KEY = {secrets.token_hex(16)}")
    print(f"DASHBOARD_PASSWORD = {secrets.token_urlsafe(24)}")


if __name__ == "__main__":
    main()
