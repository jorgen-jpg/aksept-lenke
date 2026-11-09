"""Henter Aksepts boliger, solgte boliger og meglere fra privatmegleren.no.

Kontorsiden privatmegleren.no/aksept henter dataene sine fra en feed hos PMs
leverandør (Broker AS, tidligere Reeltime). Feeden svarer bare privatmegleren.no,
så vi åpner kontorsiden i en vanlig nettleser (Playwright) og tar vare på det
siden selv laster. Vi sender ingen egne forespørsler til feeden.

Nettleseren laster bare det som trengs for å få dataene: selve siden, skriptene
og feeden. Bilder, fonter, samtykkebanner og sporing (Google Tag Manager) blokkeres,
så besøkene våre ikke havner i PMs statistikk og belastningen blir minst mulig.

data/feed.json skrives bare når dataene består sjekkene. Feiler noe, beholdes
siste gode versjon, og skriptet avslutter med feilkode, slik at GitHub varsler.

Feilkoder: 0 = ok, 2 = dataene besto ikke sjekkene, 3 = klarte ikke å hente.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
OUT = ROOT / "data" / "feed.json"
GATEWAY_HOST = "rt-gateway.reeltime.no"
ALLOW_HOSTS = {"privatmegleren.no", "www.privatmegleren.no", GATEWAY_HOST}
ALLOW_TYPES = {"document", "script", "xhr", "fetch"}
MAX_PAGES = 25          # 25 sider x 12 = 300 boliger, langt over det Aksept har
WAIT_SECONDS = 40       # hvor lenge vi venter på svar fra feeden per side


def log(msg: str) -> None:
    print(f"[feed] {msg}", flush=True)


class Capture:
    """Samler svarene kontorsiden får fra feeden, sammen med hva siden spurte om."""

    def __init__(self) -> None:
        self.estate: list[tuple[dict, dict]] = []   # (variables, estate)
        self.department: dict | None = None

    def on_response(self, resp) -> None:
        try:
            if urlparse(resp.url).hostname != GATEWAY_HOST or resp.request.method != "POST":
                return
            body = json.loads(resp.request.post_data or "{}")
            data = (resp.json() or {}).get("data") or {}
        except Exception:  # noqa: BLE001 - et svar vi ikke forstår, skal ikke stoppe resten
            return
        op = body.get("operationName")
        if op == "SearchEstate":
            estate = (data.get("search") or {}).get("estate")
            if estate is not None:
                self.estate.append((body.get("variables") or {}, estate))
        elif op == "Department" and data.get("department"):
            self.department = data["department"]

    def find(self, sold: bool, page: int) -> dict | None:
        for variables, estate in self.estate:
            f = variables.get("filter") or {}
            if bool(f.get("sold")) == sold and (f.get("pagination") or {}).get("page") == page:
                return estate
        return None


def block_extras(route) -> None:
    req = route.request
    if urlparse(req.url).hostname in ALLOW_HOSTS and req.resource_type in ALLOW_TYPES:
        route.continue_()
    else:
        route.abort()


def open_page(page, cap: Capture, url: str, sold: bool, n: int) -> dict:
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    start = time.time()
    while time.time() - start < WAIT_SECONDS:
        estate = cap.find(sold, n)
        if estate is not None:
            return estate
        page.wait_for_timeout(250)
    raise RuntimeError(f"Fikk ikke svar fra feeden for {url}")


def slim_estate(item: dict) -> dict:
    """Tar bare vare på feltene siden bruker. Gir mindre filer og ryddigere historikk."""
    prices = item.get("prices") or {}
    size = item.get("size") or {}
    images = [i.get("url") for i in ((item.get("images") or {}).get("list") or []) if i.get("url")]
    return {
        "estateId": str(item.get("estateId") or ""),
        "assignmentNumber": str(item.get("assignmentNumber") or ""),
        "type": item.get("type"),
        "ownershipType": item.get("ownershipType"),
        "assignmentType": item.get("assignmentTypeMapped") or item.get("assignmentType"),
        "isProjectMaster": bool(item.get("isProjectMaster")),
        "streetAddress": (item.get("streetAddress") or "").strip(),
        "localArea": (item.get("localArea") or "").strip(),
        "municipalityArea": (item.get("municipalityArea") or "").strip(),
        "municipalityName": (item.get("municipalityName") or "").strip(),
        "postalPlace": (item.get("postalPlace") or "").strip(),
        "sold": bool(item.get("sold")),
        "soldAt": item.get("soldAt"),
        "publishedAt": item.get("publishedAt"),
        "prices": {"askingPrice": prices.get("askingPrice"), "askingPriceTo": prices.get("askingPriceTo")},
        "size": {
            "grossInternalArea": size.get("grossInternalArea"),
            "grossInternalAreaTo": size.get("grossInternalAreaTo"),
            "usableArea": size.get("usableArea"),
            "usableAreaTo": size.get("usableAreaTo"),
            "primaryArea": size.get("primaryArea"),
            "primaryAreaTo": size.get("primaryAreaTo"),
        },
        "images": images[:4],
    }


def slim_department(dep: dict) -> dict:
    refs = dep.get("referenceUrls") or {}
    return {
        "alias": dep.get("alias"),
        "name": dep.get("name"),
        "legalName": dep.get("legalName"),
        "email": dep.get("email"),
        "phone": dep.get("phone"),
        "postalZip": dep.get("postalZip"),
        "postalPlace": dep.get("postalPlace"),
        "valuationUrl": refs.get("valuationUrl"),
        "departmentUrl": refs.get("departmentUrl"),
        "employees": [
            {
                "name": (e.get("name") or "").strip(),
                "title": (e.get("title") or "").strip(),
                "employeeAlias": e.get("employeeAlias"),
                "email": e.get("email"),
                "mobilePhone": e.get("mobilePhone"),
                "workPhone": e.get("workPhone"),
                "image": (e.get("image") or {}).get("url"),
            }
            for e in (dep.get("employees") or [])
        ],
    }


def fetch() -> dict:
    office = CONFIG["office_url"].rstrip("/")
    cap = Capture()
    for_sale: dict[str, dict] = {}
    total = None
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(locale="nb-NO")
        context.route("**/*", block_extras)
        page = context.new_page()
        page.on("response", cap.on_response)

        for n in range(MAX_PAGES):
            estate = open_page(page, cap, f"{office}?p={n}&so=false", sold=False, n=n)
            if total is None:
                total = int(estate.get("total") or 0)
            new = 0
            for item in estate.get("list") or []:
                eid = str(item.get("estateId") or "")
                if eid and eid not in for_sale:
                    for_sale[eid] = item
                    new += 1
            log(f"side {n}: {new} nye, {len(for_sale)} av {total}")
            if len(for_sale) >= total or new == 0:
                break

        sold_estate = open_page(page, cap, f"{office}?p=0&so=true", sold=True, n=0)
        sold = sold_estate.get("list") or []
        log(f"solgte: {len(sold)}")
        department = cap.department
        browser.close()

    return {
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": office,
        "total_for_sale": total,
        "for_sale": [slim_estate(i) for i in for_sale.values()],
        "sold": [slim_estate(i) for i in sold],
        "department": slim_department(department) if department else None,
    }


def validate(new: dict, old: dict | None) -> list[str]:
    problems = []
    total = new.get("total_for_sale") or 0
    items = new.get("for_sale") or []
    if total <= 0:
        problems.append("Feeden sier at det er 0 boliger til salgs.")
    if total and len(items) < 0.9 * total:
        problems.append(f"Fikk bare {len(items)} av {total} boliger.")
    missing = [i for i in items if not i.get("streetAddress") or not i.get("estateId")]
    if missing:
        problems.append(f"{len(missing)} boliger mangler adresse eller ID.")
    no_img = [i for i in items if not i.get("images")]
    if items and len(no_img) > 0.2 * len(items):
        problems.append(f"{len(no_img)} av {len(items)} boliger mangler bilde.")
    dep = new.get("department") or {}
    if not dep.get("employees"):
        problems.append("Fant ingen meglere.")
    if old and old.get("for_sale"):
        old_n = len(old["for_sale"])
        if old_n >= 10 and len(items) < 0.5 * old_n:
            problems.append(f"Antall boliger falt fra {old_n} til {len(items)}. Ser feil ut.")
    return problems


def main() -> int:
    old = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else None
    try:
        new = fetch()
    except Exception as exc:  # noqa: BLE001
        log(f"FEIL: klarte ikke å hente feeden: {exc}")
        return 3
    problems = validate(new, old)
    if problems:
        for p in problems:
            log(f"FEIL: {p}")
        log("Beholder siste gode versjon av data/feed.json.")
        return 2
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(new, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    log(f"OK: {len(new['for_sale'])} til salgs, {len(new['sold'])} solgte, "
        f"{len(new['department']['employees'])} ansatte.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
