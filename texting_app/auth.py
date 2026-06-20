from __future__ import annotations

import argparse
import base64
import binascii
import getpass
import hashlib
import hmac
import json
import secrets
import struct
import time
from typing import Any
from urllib.parse import quote, urlencode

from . import config


PASSWORD_SCHEME = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 600_000
SESSION_COOKIE_NAME = "switchboard_session"
TOTP_PERIOD_SECONDS = 30
TOTP_DIGITS = 6
TOTP_WINDOW = 1
BACKUP_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


def hash_password(password: str, iterations: int = PASSWORD_ITERATIONS) -> str:
    salt = secrets.token_bytes(18)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{PASSWORD_SCHEME}:{iterations}:{_b64encode(salt)}:{_b64encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        separator = "$" if "$" in encoded else ":"
        scheme, raw_iterations, raw_salt, raw_digest = encoded.split(separator, 3)
        if scheme != PASSWORD_SCHEME:
            return False
        iterations = int(raw_iterations)
        salt = _b64decode(raw_salt)
        expected = _b64decode(raw_digest)
    except (ValueError, TypeError, binascii.Error):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def normalize_totp_secret(secret: str) -> str:
    return "".join(str(secret or "").upper().split())


def _totp_secret_bytes(secret: str) -> bytes:
    normalized = normalize_totp_secret(secret)
    if not normalized:
        raise ValueError("TOTP secret is blank.")
    padding = "=" * (-len(normalized) % 8)
    return base64.b32decode((normalized + padding).encode("ascii"), casefold=True)


def hotp(secret: str, counter: int, digits: int = TOTP_DIGITS) -> str:
    digest = hmac.new(_totp_secret_bytes(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10**digits)).zfill(digits)


def totp(secret: str, timestamp: int | None = None) -> str:
    timestamp = int(time.time() if timestamp is None else timestamp)
    return hotp(secret, timestamp // TOTP_PERIOD_SECONDS)


def verify_totp(code: str, secret: str | None = None, timestamp: int | None = None) -> bool:
    normalized = "".join(str(code or "").split())
    if not normalized.isdigit() or len(normalized) != TOTP_DIGITS:
        return False
    secret = normalize_totp_secret(secret or config.AUTH_TOTP_SECRET)
    if not secret:
        return False
    timestamp = int(time.time() if timestamp is None else timestamp)
    counter = timestamp // TOTP_PERIOD_SECONDS
    for offset in range(-TOTP_WINDOW, TOTP_WINDOW + 1):
        try:
            if hmac.compare_digest(normalized, hotp(secret, counter + offset)):
                return True
        except (ValueError, TypeError, binascii.Error):
            return False
    return False


def totp_uri(username: str, secret: str, issuer: str | None = None) -> str:
    issuer = issuer or config.AUTH_TOTP_ISSUER
    label = f"{issuer}:{username or config.AUTH_USERNAME or 'user'}"
    query = urlencode(
        {
            "secret": normalize_totp_secret(secret),
            "issuer": issuer,
            "algorithm": "SHA1",
            "digits": str(TOTP_DIGITS),
            "period": str(TOTP_PERIOD_SECONDS),
        }
    )
    return f"otpauth://totp/{quote(label)}?{query}"


def two_factor_enabled(secret: str | None = None, backup_hashes: list[str] | tuple[str, ...] | None = None) -> bool:
    secret = config.AUTH_TOTP_SECRET if secret is None else secret
    backup_hashes = config.AUTH_BACKUP_CODE_HASHES if backup_hashes is None else backup_hashes
    return bool(normalize_totp_secret(secret) or backup_hashes)


def normalize_backup_code(code: str) -> str:
    return "".join(char for char in str(code or "").upper() if char.isalnum())


def generate_backup_code() -> str:
    raw = "".join(secrets.choice(BACKUP_CODE_ALPHABET) for _ in range(10))
    return f"{raw[:5]}-{raw[5:]}"


def generate_backup_codes(count: int = 10) -> list[str]:
    return [generate_backup_code() for _ in range(max(count, 1))]


def backup_code_hash(code: str) -> str:
    return hash_password(normalize_backup_code(code))


def matching_backup_code_hash(code: str, backup_hashes: list[str] | tuple[str, ...] | None = None) -> str | None:
    normalized = normalize_backup_code(code)
    if not normalized:
        return None
    for encoded in config.AUTH_BACKUP_CODE_HASHES if backup_hashes is None else backup_hashes:
        if verify_password(normalized, encoded):
            return encoded
    return None


def backup_code_fingerprint(encoded: str) -> str:
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def verify_second_factor(
    code: str,
    secret: str | None = None,
    backup_hashes: list[str] | tuple[str, ...] | None = None,
) -> tuple[str, str | None] | None:
    if verify_totp(code, secret):
        return ("totp", None)
    backup_hash = matching_backup_code_hash(code, backup_hashes)
    if backup_hash:
        return ("backup", backup_hash)
    return None


def auth_disabled() -> bool:
    return bool(config.AUTH_DISABLED)


def auth_configured() -> bool:
    return bool(config.AUTH_USERNAME and config.AUTH_PASSWORD_HASH)


def auth_status() -> dict[str, Any]:
    return {
        "disabled": auth_disabled(),
        "configured": auth_configured(),
        "username": config.AUTH_USERNAME if auth_configured() else "",
        "two_factor_enabled": two_factor_enabled() if auth_configured() else False,
        "backup_codes_configured": len(config.AUTH_BACKUP_CODE_HASHES),
    }


def _session_key() -> bytes:
    key = config.AUTH_SECRET_KEY or config.AUTH_PASSWORD_HASH
    return key.encode("utf-8")


def create_signed_payload(payload: dict[str, Any], purpose: str, max_age_seconds: int) -> str:
    now = int(time.time())
    signed_payload = {
        **payload,
        "purpose": purpose,
        "iat": now,
        "exp": now + max_age_seconds,
        "n": secrets.token_urlsafe(18),
    }
    body = _b64encode(json.dumps(signed_payload, separators=(",", ":")).encode("utf-8"))
    signature = _b64encode(hmac.new(_session_key(), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{signature}"


def verify_signed_payload(token: str | None, purpose: str) -> dict[str, Any] | None:
    if not token or "." not in token:
        return None
    body, signature = token.rsplit(".", 1)
    expected = _b64encode(hmac.new(_session_key(), body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(_b64decode(body).decode("utf-8"))
    except (ValueError, TypeError, json.JSONDecodeError, binascii.Error):
        return None
    if payload.get("purpose") != purpose or int(payload.get("exp") or 0) < int(time.time()):
        return None
    return payload


def create_session_token(username: str, max_age_seconds: int) -> str:
    now = int(time.time())
    payload = {
        "u": username,
        "iat": now,
        "exp": now + max_age_seconds,
        "n": secrets.token_urlsafe(18),
    }
    body = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = _b64encode(hmac.new(_session_key(), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{signature}"


def verify_session_token(token: str | None) -> str | None:
    if auth_disabled():
        return config.AUTH_USERNAME or "local"
    if not auth_configured() or not token or "." not in token:
        return None
    body, signature = token.rsplit(".", 1)
    expected = _b64encode(hmac.new(_session_key(), body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(_b64decode(body).decode("utf-8"))
    except (ValueError, TypeError, json.JSONDecodeError, binascii.Error):
        return None
    username = str(payload.get("u") or "")
    expires_at = int(payload.get("exp") or 0)
    if username != config.AUTH_USERNAME or expires_at < int(time.time()):
        return None
    return username


def session_cookie(token: str, secure: bool, max_age_seconds: int) -> str:
    parts = [
        f"{SESSION_COOKIE_NAME}={token}",
        "Path=/",
        f"Max-Age={max_age_seconds}",
        "HttpOnly",
        "SameSite=Lax",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def clear_session_cookie(secure: bool) -> str:
    parts = [
        f"{SESSION_COOKIE_NAME}=",
        "Path=/",
        "Max-Age=0",
        "HttpOnly",
        "SameSite=Lax",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Switchboard auth helpers")
    subparsers = parser.add_subparsers(dest="command")
    hash_parser = subparsers.add_parser("hash-password", help="Generate a TEXTING_AUTH_PASSWORD_HASH value.")
    hash_parser.add_argument("--iterations", type=int, default=PASSWORD_ITERATIONS)
    subparsers.add_parser("secret-key", help="Generate a TEXTING_AUTH_SECRET_KEY value.")
    totp_parser = subparsers.add_parser("totp-secret", help="Generate a TEXTING_AUTH_TOTP_SECRET value.")
    totp_parser.add_argument("--username", default=config.AUTH_USERNAME or "admin")
    totp_parser.add_argument("--issuer", default=config.AUTH_TOTP_ISSUER)
    backup_parser = subparsers.add_parser("backup-codes", help="Generate backup codes and TEXTING_AUTH_BACKUP_CODE_HASHES.")
    backup_parser.add_argument("--count", type=int, default=10)
    setup_parser = subparsers.add_parser("setup-2fa", help="Generate a TOTP secret, authenticator URI, and backup codes.")
    setup_parser.add_argument("--username", default=config.AUTH_USERNAME or "admin")
    setup_parser.add_argument("--issuer", default=config.AUTH_TOTP_ISSUER)
    setup_parser.add_argument("--backup-count", type=int, default=10)
    args = parser.parse_args(argv)

    if args.command == "secret-key":
        print(secrets.token_urlsafe(32))
        return 0
    if args.command == "totp-secret":
        secret = generate_totp_secret()
        print(f"TEXTING_AUTH_TOTP_SECRET={secret}")
        print(f"Authenticator URI: {totp_uri(args.username, secret, args.issuer)}")
        return 0
    if args.command == "backup-codes":
        codes = generate_backup_codes(args.count)
        hashes = [backup_code_hash(code) for code in codes]
        print("Backup codes - save these now. They will not be shown again:")
        for code in codes:
            print(code)
        print()
        print("TEXTING_AUTH_BACKUP_CODE_HASHES=" + ",".join(hashes))
        return 0
    if args.command == "setup-2fa":
        secret = generate_totp_secret()
        codes = generate_backup_codes(args.backup_count)
        hashes = [backup_code_hash(code) for code in codes]
        print(f"TEXTING_AUTH_TOTP_SECRET={secret}")
        print(f"Authenticator URI: {totp_uri(args.username, secret, args.issuer)}")
        print()
        print("Backup codes - save these now. They will not be shown again:")
        for code in codes:
            print(code)
        print()
        print("TEXTING_AUTH_BACKUP_CODE_HASHES=" + ",".join(hashes))
        return 0
    if args.command in {None, "hash-password"}:
        password = getpass.getpass("Password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            raise SystemExit("Passwords did not match.")
        if not password:
            raise SystemExit("Password cannot be empty.")
        print(hash_password(password, iterations=args.iterations if args.command == "hash-password" else PASSWORD_ITERATIONS))
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
