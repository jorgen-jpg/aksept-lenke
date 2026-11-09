"""Lagrer nye boligdata i repoet, men bare når det trengs.

Kjøres av GitHub Actions etter et vellykket bygg. data/feed.json lagres når
  - boligene, de solgte eller meglerne har endret seg, eller
  - den lagrede versjonen er eldre enn tre timer (så vi alltid vet når dataene sist var ferske).
Da blir historikken ryddig, og repoet holder seg aktivt, så GitHub ikke slår av timeplanen.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
FEED = "data/feed.json"
MAX_AGE = dt.timedelta(hours=3)


def git(*args: str, check: bool = True) -> str:
    res = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if check and res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} feilet: {res.stderr.strip()}")
    return res.stdout


def without_time(d: dict) -> dict:
    return {k: v for k, v in d.items() if k != "fetched_at"}


def main() -> int:
    current = json.loads((ROOT / FEED).read_text(encoding="utf-8"))
    try:
        committed = json.loads(git("show", f"HEAD:{FEED}"))
    except RuntimeError:
        committed = None

    reason = None
    if committed is None:
        reason = "første versjon"
    elif without_time(current) != without_time(committed):
        old_ids = {x["estateId"] for x in committed.get("for_sale", [])}
        new_ids = {x["estateId"] for x in current.get("for_sale", [])}
        added, removed = len(new_ids - old_ids), len(old_ids - new_ids)
        reason = f"{len(new_ids)} til salgs ({added} nye, {removed} borte)" if (added or removed) else "endringer i boligene"
    else:
        then = dt.datetime.fromisoformat(committed["fetched_at"].replace("Z", "+00:00"))
        now = dt.datetime.fromisoformat(current["fetched_at"].replace("Z", "+00:00"))
        if now - then >= MAX_AGE:
            reason = "fersk kopi (ingen endringer)"

    if not reason:
        print("[data] Ingen endringer å lagre.")
        return 0

    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    git("add", FEED)
    git("commit", "-m", f"Boligdata: {reason}")
    for attempt in range(3):
        res = subprocess.run(["git", "push"], cwd=ROOT, capture_output=True, text=True)
        if res.returncode == 0:
            print(f"[data] Lagret: {reason}.")
            return 0
        # Noen andre har pushet i mellomtiden. Hent og prøv igjen.
        git("pull", "--rebase", "--autostash", check=False)
    print("[data] Klarte ikke å lagre boligdataene i repoet. Siden er likevel publisert.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
