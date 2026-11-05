"""Creates the ThirdUmpire room in Switch and locks who may talk to each agent.

  python switch/setup.py --engineer <slack handle> --compliance <slack handle> --owner <slack handle> [--recreate]

Reads the gateway login from Switch Console's local server env (or GATEWAY_URL / GATEWAY_ADMIN_EMAIL /
GATEWAY_ADMIN_PASSWORD). SLACK_BOT_TOKEN in .env (optional) is used to invite the three people to the channel.

The lock is a Switch addressing policy per agent. Switch checks it for every message AND for every answer to an
agent's permission request, so only the owner can approve a release: the check lives in Switch, not in a prompt.
"""
import argparse
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE_ENV = Path.home() / "Library/Application Support/switchdash/local-switch-server/native/run/switch-core.env"


def env():
    for f in (CORE_ENV, ROOT / ".env"):
        if f.exists():
            for line in f.read_text().splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
    port = os.environ.get("SERVER_PORT", "65413")
    return os.environ.get("GATEWAY_URL", f"http://127.0.0.1:{port}/gateway")


class Gateway:
    def __init__(self, base):
        self.base = base
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self("POST", "/auth/login", {"email": os.environ["GATEWAY_ADMIN_EMAIL"], "password": os.environ["GATEWAY_ADMIN_PASSWORD"]})

    def __call__(self, method, path, body=None, ok=(200, 201)):
        r = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                   headers={"Content-Type": "application/json"}, method=method)
        try:
            with self.op.open(r) as x:
                return json.loads(x.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code in ok:
                return {}
            sys.exit(f"{method} {path} -> {e.code}: {e.read().decode()[:500]}")


def policy(users, agents=()):
    # one rule: these Slack people (Switch external-user ids) and these agents, nobody else, not even the agent's owner
    return {"policy": {"rules": [{"rooms": "*", "room_groups": "*", "users": list(users), "agents": list(agents),
                                  "owner": False, "owner_agents": False, "platform": False}]}}


def _channel(room):
    return (re.findall(r"\b[CG][A-Z0-9]{8,}\b", room.get("external_channel_url") or "") or [None])[-1]


def _slack(token, method, body):
    req = urllib.request.Request(f"https://slack.com/api/{method}", data=json.dumps(body).encode(), headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--team", default="kitebase")
    p.add_argument("--engineer", required=True)
    p.add_argument("--compliance", required=True)
    p.add_argument("--owner", required=True)
    p.add_argument("--recreate", action="store_true", help="delete this team's room and agents first")
    a = p.parse_args()
    gw = Gateway(env())
    names = [f"{a.team}-{n}" for n in ("builder", "umpire", "rules", "release")]

    bridge = next(b for b in gw("GET", "/collaborations") if b.get("bridge_type") == "slack")
    bridge["id"] = bridge["bridge_id"]
    people = {u["external_username"]: u for u in gw("GET", f"/collaborations/{bridge['id']}/users")}
    for h in (a.engineer, a.compliance, a.owner):
        if h not in people:
            sys.exit(f"{h} is not known to Switch's Slack bridge yet. Have them post once in a channel with the bot.")
    eng, comp, own = (people[h] for h in (a.engineer, a.compliance, a.owner))

    token = os.environ.get("SLACK_BOT_TOKEN")
    if a.recreate:
        for r in gw("GET", "/rooms"):
            if r.get("name") == f"{a.team}-thirdumpire":
                old = _channel(r)
                if token and old:  # Slack keeps archived channel names, so free the name first
                    _slack(token, "conversations.rename", {"channel": old, "name": f"{a.team}-thirdumpire-{int(time.time())}"})
                gw("DELETE", f"/rooms/{r['id']}?retire_channel=true")
        for n in names:
            gw("DELETE", f"/agents/by-name/{n}", ok=(200, 404))

    t = (ROOT / "switch/thirdumpire.template.yaml").read_text()
    gw("POST", "/rooms/from-template-document", {"template": t, "template_name": "thirdumpire", "inputs": {
        "team": a.team, "repo": str(ROOT), "engineer": a.engineer, "compliance": a.compliance, "owner": a.owner}})

    ids = {x["name"]: x["id"] for x in gw("GET", "/agents") if x["name"] in names}
    b, u, r, rel = (ids[n] for n in names)
    gw("PUT", f"/agents/{b}/addressing-policy", policy([eng["id"]], [u]))   # engineer; umpire hands OUT back
    gw("PUT", f"/agents/{u}/addressing-policy", policy([eng["id"]], [b]))   # engineer; builder hands over
    gw("PUT", f"/agents/{r}/addressing-policy", policy([comp["id"]]))       # compliance only
    gw("PUT", f"/agents/{rel}/addressing-policy", policy([own["id"]]))      # owner only: releases need the owner
    print(f"Room {a.team}-thirdumpire ready. Locked: builder/umpire={a.engineer}, rules={a.compliance}, release={a.owner}.")

    channel = next(_channel(x) for x in gw("GET", "/rooms") if x["name"] == f"{a.team}-thirdumpire")
    if token and channel:
        res = _slack(token, "conversations.invite", {"channel": channel, "users": ",".join(x["external_user_id"] for x in (eng, comp, own))})
        print("Invited the three people to the channel." if res.get("ok") else f"Slack invite: {res.get('error')} {res.get('errors', '')}")
    else:
        print("Invite the engineer, compliance and owner to the Slack channel by hand.")


if __name__ == "__main__":
    main()
