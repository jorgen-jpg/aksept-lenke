"""Avgjør om et planlagt forsøk skal bygge siden på nytt.

GitHub hopper ofte over planlagte kjøringer. Derfor prøver arbeidsflyten hvert
kvarter, og dette skriptet svarer «nei» når siden allerede er fersk. Da stopper
forsøket etter noen sekunder, og privatmegleren.no får ikke flere besøk enn nødvendig.

Skriver bygg=ja eller bygg=nei til $GITHUB_OUTPUT.
Endringer i repoet og kjøringer startet for hånd bygger alltid.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time
import urllib.request

MAX_AGE_MIN = 50  # eldre enn dette: bygg på nytt


def decide(event: str, built_at: str | None, now: dt.datetime) -> tuple[bool, str]:
    if event != "schedule":
        return True, f"startet av «{event}», bygger alltid"
    if not built_at:
        return True, "fant ikke når siden sist ble bygget"
    try:
        then = dt.datetime.fromisoformat(built_at.replace("Z", "+00:00"))
    except ValueError:
        return True, f"forstår ikke tidspunktet «{built_at}»"
    age = (now - then).total_seconds() / 60
    if age >= MAX_AGE_MIN:
        return True, f"siden er {age:.0f} minutter gammel"
    return False, f"siden er {age:.0f} minutter gammel, fersk nok"


def live_built_at(repository: str) -> str | None:
    """Leser status.json fra den publiserte siden. Følger videresending til eget domene."""
    owner, _, repo = repository.partition("/")
    url = f"https://{owner}.github.io/{repo}/status.json?t={int(time.time())}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "aksept-lenke-bygg"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.load(resp).get("built_at")
    except Exception as exc:  # noqa: BLE001 - kan vi ikke lese siden, bygger vi
        print(f"[trenger] Klarte ikke å lese {url}: {exc}", flush=True)
        return None


def main() -> int:
    event = os.environ.get("GITHUB_EVENT_NAME", "manuell")
    built_at = None
    if event == "schedule":
        built_at = live_built_at(os.environ.get("GITHUB_REPOSITORY", ""))
    build, why = decide(event, built_at, dt.datetime.now(dt.timezone.utc))
    print(f"[trenger] bygg={'ja' if build else 'nei'}: {why}", flush=True)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"bygg={'ja' if build else 'nei'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
