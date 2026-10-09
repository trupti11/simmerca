#!/usr/bin/env python3
"""One-time Etsy OAuth (PKCE) to obtain the refresh token for the Simmerca secret.

1. In your Etsy app settings, add the callback URL http://localhost:3003/callback
2. python scripts/etsy_oauth.py <ETSY_KEYSTRING>
3. Open the printed URL, approve, and copy the printed refresh token into the secret (etsy_refresh_token).
"""

import base64
import hashlib
import http.server
import json
import secrets
import sys
import urllib.parse
import urllib.request

REDIRECT = "http://localhost:3003/callback"
SCOPES = "listings_r listings_w transactions_r"


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: etsy_oauth.py <ETSY_KEYSTRING>")
    keystring = sys.argv[1]
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    url = "https://www.etsy.com/oauth/connect?" + urllib.parse.urlencode({
        "response_type": "code", "redirect_uri": REDIRECT, "scope": SCOPES, "client_id": keystring,
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})
    print("Open this URL and approve:\n\n" + url + "\n")
    result: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state", [""])[0] != state:
                self.send_response(400)
                self.end_headers()
                return
            result["code"] = q.get("code", [""])[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Done. You can close this tab.")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("localhost", 3003), Handler)
    while "code" not in result:
        server.handle_request()
    body = urllib.parse.urlencode({"grant_type": "authorization_code", "client_id": keystring,
                                   "redirect_uri": REDIRECT, "code": result["code"],
                                   "code_verifier": verifier}).encode()
    req = urllib.request.Request("https://api.etsy.com/v3/public/oauth/token", data=body, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
        tokens = json.loads(resp.read())
    print("etsy_refresh_token:", tokens["refresh_token"])
    print("Your Etsy user id (prefix of the access token):", tokens["access_token"].split(".")[0])


if __name__ == "__main__":
    main()
