"""Read-only connectivity + data-inventory probe.

Answers: does the key work, and is there any data to analyze?
Reads PAULSJOB_API_KEY from .env or the environment. Never prints the key.
Makes only GET requests. Standard library only.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

# One correlation id for the whole probe run; each request gets its own id.
CORRELATION_ID = str(uuid.uuid4())


def load_env(path=".env"):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


USER_AGENT = "screening-accuracy-analyzer/0.1"


def get(base, path, key, payload=None, **params):
    url = base.rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {
        "x-company-api-key": key,
        "Accept": "application/json",
        # The API sits behind Cloudflare, which rejects urllib's default
        # User-Agent with a 1010 "browser signature" block before the request
        # reaches the API at all.
        "User-Agent": USER_AGENT,
        # Recommended by the API docs for request tracing.
        "paul-request-id": str(uuid.uuid4()),
        "paul-correlation-id": CORRELATION_ID,
    }
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, (exc.read()[:300].decode("utf-8", "replace") or "")
    except Exception as exc:  # noqa: BLE001 - probe reports, never crashes
        return None, f"{type(exc).__name__}: {exc}"


def describe(node, indent=6, depth=0):
    """Print the SHAPE of a response without assuming it.

    The spec is not always an exact match for what the API returns, so the
    probe reports structure rather than reading fields that may not exist.
    """
    pad = " " * indent
    if depth > 3:
        print(f"{pad}...")
        return
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, dict):
                print(f"{pad}{k}: object({len(v)} keys)")
                describe(v, indent + 2, depth + 1)
            elif isinstance(v, list):
                kinds = sorted({type(i).__name__ for i in v})
                print(f"{pad}{k}: array[{len(v)}] of {kinds or '-'}")
                if v:
                    describe(v[0], indent + 2, depth + 1)
            else:
                print(f"{pad}{k}: {type(v).__name__} = {str(v)[:70]!r}")
    elif isinstance(node, list):
        kinds = sorted({type(i).__name__ for i in node})
        print(f"{pad}array[{len(node)}] of {kinds or '-'}")
        if node:
            describe(node[0], indent + 2, depth + 1)
    else:
        print(f"{pad}{type(node).__name__} = {str(node)[:70]!r}")


def probe(base, key, path, label=None, payload=None, **params):
    status, body = get(base, path, key, payload=payload, **params)
    verb = "POST" if payload is not None else "GET "
    print(f"[{status}] {verb} {label or path}")
    if status != 200:
        print(f"      {str(body)[:300]}")
        print()
        return None
    describe(body.get("data") if isinstance(body, dict) and "data" in body else body)
    print()
    return body


def main():
    load_env()
    key = os.environ.get("PAULSJOB_API_KEY")
    base = os.environ.get("PAULSJOB_BASE_URL", "https://api.paulsjob.ai/dev/v1")
    if not key:
        sys.exit("PAULSJOB_API_KEY not set. Copy .env.example to .env and fill it in.")
    print(f"base url : {base}")
    print("api key  : loaded from environment (value never printed)")
    print()

    # What step categories exist on this company (compact list).
    status, body = get(base, "/recruiting/job-step-categories", key)
    print(f"[{status}] GET  /recruiting/job-step-categories")
    if status == 200:
        cats = (body.get("data") or {}).get("Categories") or []
        for c in cats:
            print(f"      {c.get('ID'):<22} optout={c.get('AllowOptOut')!s:<5} config={c.get('AllowChangeConfig')}")
    print()

    # Current (non-deprecated) search endpoints. Both take an optional body.
    probe(base, key, "/recruiting/jobs/search-jobs", payload={"PerPage": 100})
    probe(base, key, "/recruiting/applications/search-applications", payload={"PerPage": 100, "Page": 1})


if __name__ == "__main__":
    main()
