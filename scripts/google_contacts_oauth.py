#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.request


SCOPE = "https://www.googleapis.com/auth/contacts.readonly"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def auth_url(client_id: str, redirect_uri: str) -> str:
    return AUTH_URL + "?" + urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",
            "prompt": "consent",
        }
    )


def exchange_code(client_id: str, client_secret: str, redirect_uri: str, code: str) -> dict:
    payload = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "code": code,
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        TOKEN_URL,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a Google Contacts refresh token for the texting app.")
    parser.add_argument("--client-id", default=os.environ.get("GOOGLE_CLIENT_ID", ""))
    parser.add_argument("--client-secret", default=os.environ.get("GOOGLE_CLIENT_SECRET", ""))
    parser.add_argument("--redirect-uri", default=os.environ.get("GOOGLE_REDIRECT_URI", "http://127.0.0.1:8767/callback"))
    parser.add_argument("--code", help="Authorization code returned by Google.")
    args = parser.parse_args()

    if not args.client_id:
        parser.error("--client-id or GOOGLE_CLIENT_ID is required")
    if not args.code:
        print(auth_url(args.client_id, args.redirect_uri))
        return
    if not args.client_secret:
        parser.error("--client-secret or GOOGLE_CLIENT_SECRET is required when exchanging a code")

    token = exchange_code(args.client_id, args.client_secret, args.redirect_uri, args.code)
    refresh_token = token.get("refresh_token")
    if refresh_token:
        print(f"GOOGLE_REFRESH_TOKEN={refresh_token}")
    else:
        print(json.dumps(token, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
