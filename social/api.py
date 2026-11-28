"""Small REST adapters. Exception text never includes a response body, URL or key."""

import json
import os
import subprocess
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class RemoteError(RuntimeError):
    def __init__(self, status=0):
        self.status = status
        super().__init__("Remote request failed" + (f" (HTTP {status})" if status else ""))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def request(method, url, headers=None, data=None, *, raw=False):
    # Do not send credentials or local vision inputs through an ambient proxy.
    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        with opener.open(Request(url, data=data, method=method, headers=headers or {}), timeout=120) as res:
            buf = res.read(24 * 1024 * 1024 + 1)
            if len(buf) > 24 * 1024 * 1024:
                raise ValueError("Response too large")
            return buf if raw or not buf else json.loads(buf)
    except HTTPError as exc:
        raise RemoteError(exc.code) from None
    except (URLError, OSError, ValueError):
        raise RemoteError() from None


def secret(name):
    if name != "ANTHROPIC_API_KEY" and (value := os.environ.get(name)):
        return value
    result = subprocess.run(
        ["/usr/bin/security", "find-generic-password", "-s", "social-autopilot/" + name, "-w"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode or not result.stdout.strip():
        raise ValueError("Missing runtime credential: " + name)
    return result.stdout.strip()


def https(url, *, public=False):
    u = urlsplit(url)
    if (
        u.scheme != "https"
        or not u.hostname
        or u.username
        or u.password
        or (public and (u.query or u.fragment))
    ):
        raise ValueError("Unexpected media URL")
    return url


class Blotato:
    def __init__(self, key, send=request):
        self.headers = {"blotato-api-key": key, "Content-Type": "application/json"}
        self.base = "https://backend.blotato.com/v2"
        self.send = send

    def get(self, path, **params):
        query = "?" + urlencode(params, doseq=True) if params else ""
        return self.send("GET", self.base + path + query, self.headers)

    def post(self, path, body):
        return self.send("POST", self.base + path, self.headers, json.dumps(body).encode())

    def upload(self, data):
        ticket = self.post("/media/uploads", {"filename": "photo.jpg"})
        url = https(ticket["publicUrl"], public=True)
        self.send("PUT", https(ticket["presignedUrl"]), {"Content-Type": "image/jpeg"}, data)
        return url


def pages(get, path, **params):
    seen = set()
    while True:
        response = get(path, **params)
        yield from response["items"]
        cursor = response.get("cursor")
        if not cursor:
            return
        if cursor in seen:
            raise ValueError("Repeated page cursor")
        seen.add(cursor)
        params["cursor"] = cursor
