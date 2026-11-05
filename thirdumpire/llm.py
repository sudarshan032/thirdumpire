"""Minimal OpenAI-compatible chat client with a disk cache (stdlib only).

Every call is cached by a hash of its request, so a review can be replayed with no AI at all
(`THIRDUMPIRE_OFFLINE=1`): that is the AI-off fallback for the demo and for judges.
"""
import functools
import gzip
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "runs" / "cache"


def load_env(path=ROOT / ".env"):
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def endpoint(role):
    """role is BOT or JUDGE. Each points at its own vendor."""
    load_env()
    return {
        "base_url": os.environ.get(f"{role}_BASE_URL", ""),
        "key": os.environ.get(f"{role}_API_KEY", ""),
        "model": os.environ.get(f"{role}_MODEL", ""),
    }


class Offline(Exception):
    pass


@functools.cache
def replay():
    """Recorded answers committed with the sample data (sample/*/replay.json.gz), so a fresh clone replays with no keys."""
    out = {}
    for f in sorted(ROOT.glob("sample/*/replay.json.gz")):
        out.update(json.loads(gzip.decompress(f.read_bytes())))
    return out


def chat(ep, messages, tools=None, temperature=0.0, run=0):
    req = {"model": ep["model"], "messages": messages, "temperature": temperature}
    if tools:
        req["tools"] = tools
    key = hashlib.sha256(json.dumps([req, run], sort_keys=True).encode()).hexdigest()
    hit = CACHE / f"{key}.json"
    if hit.exists():
        return json.loads(hit.read_text())
    if key in replay():
        return replay()[key]
    if os.environ.get("THIRDUMPIRE_OFFLINE") == "1" or not ep["key"]:
        raise Offline(f"no cached answer and no API key for model {ep['model'] or '?'}")
    body = json.dumps(req).encode()
    for attempt in range(30):
        r = urllib.request.Request(
            ep["base_url"].rstrip("/") + "/chat/completions",
            data=body,
            headers={"Authorization": f"Bearer {ep['key']}", "Content-Type": "application/json", "User-Agent": "thirdumpire/0.1"},
        )
        try:
            with urllib.request.urlopen(r, timeout=90) as resp:
                msg = json.loads(resp.read())["choices"][0]["message"]
            break
        except urllib.error.HTTPError as e:
            # ponytail: fixed backoff on rate limits; free tiers allow ~30 requests/min
            detail = e.read()
            if e.code in (429, 500, 502, 503) and attempt < 29:
                wait = re.search(rb"try again in ([0-9.]+)(ms|s)", detail)
                time.sleep(float(wait.group(1)) / (1000 if wait.group(2) == b"ms" else 1) + 1 if wait else 5 * (attempt + 1))
                continue
            raise RuntimeError(f"{e.code} from {ep['base_url']}: {detail[:300]!r}") from e
    CACHE.mkdir(parents=True, exist_ok=True)
    hit.write_text(json.dumps(msg))
    return msg
