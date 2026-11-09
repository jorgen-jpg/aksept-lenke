"""Bygger lenke-i-bio-siden for PrivatMegleren Aksept.

Leser
  data/feed.json     boliger til salgs, solgte boliger og meglere (fra fetch_feed.py)
  data/videos.csv    hvilke boliger som har vært i video (Nylig i video)
  config.json        tekster, lenker, filtre og innstillinger

og skriver en ferdig statisk nettside til site/:
  index.html         selve siden
  b/<id>/index.html  korte lenker rett til hver bolig, f.eks. .../b/138145/?src=story
  img/               kvadratiske bilder, laget fra boligannonsene
  status.json        hva som ble bygget, og hva som eventuelt ikke stemte
  404.html, robots.txt, favicon og delingsbilde

Stopper med feilkode hvis noe viktig mangler. Da publiseres ikke en halvferdig side,
og den forrige versjonen blir liggende.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import html
import io
import json
import os
import pathlib
import re
import shutil
import sys
import unicodedata
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from PIL import Image, ImageOps

ROOT = pathlib.Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
DATA = ROOT / "data"
SRC = ROOT / "src"
OUT = pathlib.Path(os.environ.get("SITE_DIR") or (ROOT / "site"))
CACHE = pathlib.Path(os.environ.get("IMG_CACHE") or (ROOT / ".cache" / "img"))
OSLO = ZoneInfo("Europe/Oslo")
NOW = dt.datetime.now(dt.timezone.utc)
SITE_URL = (os.environ.get("SITE_URL") or CFG.get("site_url") or "").rstrip("/")
USER_AGENT = "PrivatMegleren-Aksept-lenke-i-bio/1.0 (bygger lenkesiden; kontakt: aksept@privatmegleren.no)"

LISTING_PX = 640
SOLD_PX = 360
BROKER_PX = 192

warnings: list[str] = []
errors: list[str] = []


def warn(msg: str) -> None:
    warnings.append(msg)
    print(f"[bygg] ADVARSEL: {msg}", flush=True)


def fail(msg: str) -> None:
    errors.append(msg)
    print(f"[bygg] FEIL: {msg}", flush=True)


def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


# ---------------------------------------------------------------- tekst og tall

NBSP = " "


def fmt_int(n: int | float) -> str:
    return f"{int(round(n)):,}".replace(",", NBSP)


def fmt_price(listing: dict) -> str:
    p = listing.get("prices") or {}
    lo, hi = p.get("askingPrice"), p.get("askingPriceTo")
    if not lo:
        return "Pris på forespørsel"
    if hi and hi != lo:
        return f"{fmt_int(lo)}–{fmt_int(hi)}{NBSP}kr"
    if listing.get("isProjectMaster"):
        return f"Fra {fmt_int(lo)}{NBSP}kr"
    return f"{fmt_int(lo)}{NBSP}kr"


def area_value(listing: dict) -> tuple[float | None, float | None]:
    """Arealet PM selv viser på kortene: BRA-i, ellers BRA, ellers P-rom."""
    s = listing.get("size") or {}
    for a, b in (("grossInternalArea", "grossInternalAreaTo"), ("usableArea", "usableAreaTo"), ("primaryArea", "primaryAreaTo")):
        if s.get(a):
            return s[a], s.get(b)
    return None, None


def fmt_area(listing: dict) -> str:
    lo, hi = area_value(listing)
    if not lo:
        return ""
    if hi and hi != lo:
        return f"{fmt_int(lo)}–{fmt_int(hi)}{NBSP}m²"
    return f"{fmt_int(lo)}{NBSP}m²"


SMALL_WORDS = {"på", "i", "ved", "v/", "mot", "og", "av", "til", "med", "for", "om", "fra", "under", "over"}


def tidy_heading(text: str) -> str:
    """Gjør overskrifter i bare store bokstaver lesbare: «ØVRE TORSHOV» blir «Øvre Torshov»."""
    text = re.sub(r"\s+", " ", (text or "").strip())
    letters = [c for c in text if c.isalpha()]
    if not letters or not all(c.isupper() for c in letters):
        return text
    out = []
    for i, word in enumerate(text.lower().split(" ")):
        if i > 0 and word in SMALL_WORDS:
            out.append(word)
        else:
            out.append("/".join(part[:1].upper() + part[1:] for part in word.split("/")))
    return " ".join(out)


def norm(text: str) -> str:
    t = unicodedata.normalize("NFKC", (text or "").lower())
    return t.replace("ü", "u")


def norm_address(text: str) -> str:
    t = norm(text)
    t = t.split(",")[0]
    t = re.sub(r"\bgt\.?(?=\s|\d|$)", "gate", t)
    t = re.sub(r"\bvn\.?(?=\s|\d|$)", "veien", t)
    t = re.sub(r"[^0-9a-zæøåäö]", "", t)
    return t


def address_key(text: str) -> tuple[str, str, str]:
    """('theresesgate', '26', 'a') fra «Thereses gate 26 A»."""
    t = norm(text).split(",")[0]
    t = re.sub(r"\bgt\.?(?=\s|\d|$)", "gate", t)
    m = re.match(r"^\s*(.*?)\s*(\d+)\s*([a-zæøå]?)\b", t)
    if not m:
        return (re.sub(r"[^a-zæøåäö]", "", t), "", "")
    street = re.sub(r"[^a-zæøåäö]", "", m.group(1))
    return (street, m.group(2), m.group(3))


# ---------------------------------------------------------------- lenker

def utm(url: str, content: str) -> str:
    u = CFG["utm"]
    params = {
        "utm_source": u["default_source"],
        "utm_medium": u["medium"],
        "utm_campaign": u["campaign"],
        "utm_content": content,
    }
    sep = "&" if "?" in url else "?"
    return url + sep + urllib.parse.urlencode(params)


def listing_url(eid: str) -> str:
    return CFG["listing_url"].format(id=eid)


def broker_url(alias: str) -> str:
    return CFG["broker_url"].format(alias=alias)


def abs_url(path: str) -> str:
    if not SITE_URL:
        return path
    return SITE_URL + "/" + path.lstrip("/")


# ---------------------------------------------------------------- bilder

def http_get(url: str, timeout: int = 25) -> bytes:
    last = None
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise RuntimeError(f"Klarte ikke å laste ned {url}: {last}")


def source_variant(url: str) -> str:
    # Finn sine bilder finnes i flere bredder. 960 piksler er nok til et skarpt kvadrat.
    return re.sub(r"/dynamic/\d+w/", "/dynamic/960w/", url)


def square_image(urls: list[str], out_name: str, px: int, focus_top: bool = False) -> str | None:
    """Lager et skarpt, kvadratisk JPEG. Bruker mellomlager så bildene ikke lastes ned hver time."""
    for url in urls:
        if not url:
            continue
        src = source_variant(url)
        key = hashlib.sha1(f"{src}|{px}|{focus_top}".encode()).hexdigest()[:12]
        cached = CACHE / f"{key}.jpg"
        if not cached.exists():
            try:
                raw = http_get(src)
                im = Image.open(io.BytesIO(raw))
                im = ImageOps.exif_transpose(im).convert("RGB")
                w, h = im.size
                side = min(w, h)
                left = (w - side) // 2
                top = 0 if (focus_top and h > w) else (h - side) // 2
                if focus_top and h > w:
                    top = int((h - side) * 0.12)
                im = im.crop((left, top, left + side, top + side)).resize((px, px), Image.LANCZOS)
                CACHE.mkdir(parents=True, exist_ok=True)
                im.save(cached, "JPEG", quality=80, optimize=True, progressive=True)
            except Exception as exc:  # noqa: BLE001
                warn(f"Bilde feilet ({exc}). Prøver neste.")
                continue
        rel = f"img/{out_name}-{key[:8]}.jpg"
        dest = OUT / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(cached, dest)
        return rel
    return None


# ---------------------------------------------------------------- områder

def area_group(listing: dict) -> dict:
    groups = CFG["area_groups"]
    muni = norm(listing.get("municipalityName") or "")
    if not muni and norm(listing.get("postalPlace") or "") == "oslo":
        muni = "oslo"

    def allowed(g):
        return not g["municipality"] or muni in [norm(m) for m in g["municipality"]]

    for field in ("municipalityArea", "localArea"):
        hay = norm(listing.get(field) or "")
        if not hay:
            continue
        for g in groups:
            if g["match"] and allowed(g) and any(norm(k) in hay for k in g["match"]):
                return g
    for g in groups:
        if not g["match"] and g["municipality"] and allowed(g):
            return g
    return groups[-1]


# ---------------------------------------------------------------- videoer

MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "mai": 5, "may": 5, "jun": 6, "jul": 7, "aug": 8,
          "sep": 9, "okt": 10, "oct": 10, "nov": 11, "des": 12, "dec": 12}


def parse_date(text: str) -> dt.date | None:
    t = (text or "").strip().lower()
    if not t:
        return None
    today = NOW.astimezone(OSLO).date()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r"^(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?$", t)
    if m:
        y = int(m.group(3)) if m.group(3) else today.year
        if y < 100:
            y += 2000
        d = dt.date(y, int(m.group(2)), int(m.group(1)))
        if not m.group(3) and d > today + dt.timedelta(days=7):
            d = d.replace(year=y - 1)
        return d
    m = re.search(r"(\d{1,2})\.?\s*([a-zæøå]{3})[a-zæøå]*\.?\s*(\d{4})?", t)
    if m and m.group(2)[:3] in MONTHS:
        y = int(m.group(3)) if m.group(3) else today.year
        d = dt.date(y, MONTHS[m.group(2)[:3]], int(m.group(1)))
        if not m.group(3) and d > today + dt.timedelta(days=7):
            d = d.replace(year=y - 1)
        return d
    return None


def read_video_rows() -> list[dict]:
    rows: list[dict] = []
    local = DATA / "videos.csv"
    if local.exists():
        with local.open(encoding="utf-8-sig", newline="") as fh:
            for i, r in enumerate(csv.DictReader(fh), start=2):
                r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
                r["_kilde"] = f"videos.csv rad {i}"
                rows.append(r)
    sheet = (CFG.get("video_sheet_csv_url") or "").strip()
    if sheet:
        try:
            text = http_get(sheet).decode("utf-8-sig")
            for i, r in enumerate(csv.DictReader(io.StringIO(text)), start=2):
                r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
                r["_kilde"] = f"regnearket rad {i}"
                rows.append(r)
        except Exception as exc:  # noqa: BLE001
            warn(f"Klarte ikke å lese regnearket med videoer ({exc}). Bruker bare data/videos.csv.")
    return rows


def find_listing(ref: str, for_sale: list[dict], sold: list[dict]) -> tuple[dict | None, str]:
    ref = (ref or "").strip()
    if not ref:
        return None, "mangler bolig"
    pool = for_sale + sold
    m = re.search(r"/eiendom/(\d+)", ref)
    if m:
        hit = next((x for x in pool if x["estateId"] == m.group(1)), None)
        return (hit, "") if hit else (None, f"fant ikke boligen {m.group(1)} blant boligene til salgs eller nylig solgt")
    digits = re.sub(r"\s", "", ref)
    if digits.isdigit():
        hit = next((x for x in pool if x["estateId"] == digits), None) or \
              next((x for x in pool if x.get("assignmentNumber") == digits), None)
        return (hit, "") if hit else (None, f"fant ikke oppdragsnummer {digits}")
    key = norm_address(ref)
    exact = [x for x in pool if norm_address(x["streetAddress"]) == key]
    if len(exact) == 1:
        return exact[0], ""
    if len(exact) > 1:
        return None, "flere boliger har denne adressen. Bruk oppdragsnummeret"
    street, number, letter = address_key(ref)
    if street and number:
        cands = [x for x in pool if address_key(x["streetAddress"])[:2] == (street, number)
                 and (not letter or address_key(x["streetAddress"])[2] == letter)]
        if len(cands) == 1:
            return cands[0], ""
        if len(cands) > 1:
            return None, "flere boliger har denne adressen. Bruk oppdragsnummeret"
    return None, "fant ikke adressen blant boligene til salgs eller nylig solgt"


def pick_videos(for_sale: list[dict], sold: list[dict]) -> tuple[list[dict], list[dict], int]:
    rows = read_video_rows()
    today = NOW.astimezone(OSLO).date()
    oldest = today - dt.timedelta(days=int(CFG["video_max_age_days"]))
    chosen: dict[str, dict] = {}
    skipped: list[dict] = []
    for r in rows:
        ref = r.get("bolig") or r.get("oppdragsnummer") or r.get("ref. nr") or r.get("adresse") or ""
        if (r.get("vis") or "ja").lower() in ("nei", "n", "no", "0", "false", "skjul"):
            continue
        approved = (r.get("godkjent (y/n)") or r.get("godkjent") or "").lower()
        if approved in ("n", "nei", "no"):
            continue
        raw_date = r.get("dato") or r.get("posting") or ""
        when = parse_date(raw_date)
        if not when:
            # Uten dato er videoen ikke lagt ut ennå. Ukjent datoformat varsler vi om.
            if raw_date.strip() and ref:
                skipped.append({"rad": r["_kilde"], "bolig": ref, "grunn": f"forstår ikke datoen «{raw_date}»"})
            continue
        if when > today or when < oldest:
            continue
        listing, why = find_listing(ref, for_sale, sold)
        if not listing:
            skipped.append({"rad": r["_kilde"], "bolig": ref, "grunn": why})
            continue
        prev = chosen.get(listing["estateId"])
        if prev and prev["date"] >= when:
            continue
        chosen[listing["estateId"]] = {"listing": listing, "date": when,
                                       "title": (r.get("tittel") or "").strip(), "link": r.get("lenke") or ""}
    videos = sorted(chosen.values(), key=lambda v: (v["date"], v["listing"].get("publishedAt") or ""), reverse=True)
    return videos[: int(CFG["video_max_count"])], skipped, len(rows)


# ---------------------------------------------------------------- HTML-biter

ICONS = {
    "phone": '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6.6 3.5l2.6 3.3-1.6 2.4a12.5 12.5 0 006.2 6.2l2.4-1.6 3.3 2.6-1.4 3.3c-.3.6-1 .9-1.6.8C9.9 19.4 4.6 14.1 3.4 7.5c-.1-.7.2-1.3.8-1.6l2.4-2.4z" fill="none" stroke="#E5E3E4" stroke-width="1.4" stroke-linejoin="round"/></svg>',
    "chat": '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v11H9l-4.5 3.5V16H4z" fill="none" stroke="#E5E3E4" stroke-width="1.4" stroke-linejoin="round"/></svg>',
    "mail": '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="5.5" width="17" height="13" fill="none" stroke="#E5E3E4" stroke-width="1.4"/><path d="M4 6.5l8 6.5 8-6.5" fill="none" stroke="#E5E3E4" stroke-width="1.4"/></svg>',
    "home": '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 11l8-6.5 8 6.5M6.5 9.5V19h11V9.5" fill="none" stroke="#E5E3E4" stroke-width="1.4" stroke-linejoin="round"/></svg>',
    "chevron": '<svg width="8" height="13" viewBox="0 0 8 13" aria-hidden="true"><path d="M1.5 1.5l5 5-5 5" fill="none" stroke="#A6A4A8" stroke-width="1.4"/></svg>',
}


def img_tag(rel: str | None, alt: str, px: int, eager: bool = False) -> str:
    if not rel:
        return ""
    loading = 'fetchpriority="high"' if eager else 'loading="lazy"'
    return f'<img src="{esc(rel)}" width="{px}" height="{px}" alt="{esc(alt)}" decoding="async" {loading}>'


def render_video(v: dict, idx: int) -> str:
    x = v["listing"]
    title = v["title"] or x["streetAddress"]
    sold = x.get("sold")
    meta = "Solgt" if sold else fmt_price(x)
    if v["title"]:
        meta = f'{esc(x["streetAddress"])}<br>{esc(meta)}'
    else:
        meta = esc(meta)
    badge = '<span class="badge">Solgt</span>' if sold else ""
    label = f'{title}, {x["streetAddress"]}. {"Solgt" if sold else fmt_price(x)}. Åpner boligen på privatmegleren.no'
    return (
        f'<a class="sq v" href="{esc(utm(listing_url(x["estateId"]), "nylig-i-video"))}" aria-label="{esc(label)}">'
        f'{img_tag(x.get("_img"), "", LISTING_PX, eager=idx < 4)}<span class="shade"></span>{badge}'
        f'<span class="play" aria-hidden="true"></span>'
        f'<span class="t"><span class="h">{esc(title)}</span><span class="p">{meta}</span></span></a>'
    )


def render_card(x: dict, idx: int, initial: int) -> str:
    lo, _ = area_value(x)
    price = (x.get("prices") or {}).get("askingPrice") or ""
    sub = tidy_heading(x.get("localArea") or "") or x.get("municipalityArea") or x.get("postalPlace") or ""
    area_txt = fmt_area(x)
    more = " more" if idx >= initial else ""
    small = f"<small>{esc(area_txt)}</small>" if area_txt else ""
    return (
        f'<a class="card{more}" href="{esc(utm(listing_url(x["estateId"]), "alle-boliger"))}" '
        f'data-omrade="{esc(x["_group"]["key"])}" data-pris="{esc(price)}" data-storrelse="{esc(lo or "")}">'
        f'<span class="sq">{img_tag(x.get("_img"), "", LISTING_PX)}</span>'
        f'<span class="a">{esc(x["streetAddress"])}</span>'
        f'<span class="o">{esc(sub)}</span>'
        f'<span class="pr">{esc(fmt_price(x))}{small}</span></a>'
    )


def render_sold(x: dict) -> str:
    return (
        f'<a class="card" href="{esc(utm(listing_url(x["estateId"]), "nylig-solgt"))}">'
        f'<span class="sq">{img_tag(x.get("_img_small"), "", SOLD_PX)}<span class="badge">Solgt</span></span>'
        f'<span class="a">{esc(x["streetAddress"])}</span></a>'
    )


def render_broker(e: dict) -> str:
    img = img_tag(e.get("_img"), "", BROKER_PX)
    return (
        f'<a class="broker" href="{esc(utm(broker_url(e["employeeAlias"]), "meglere"))}" '
        f'aria-label="{esc(e["name"] + ", " + e["title"])}">'
        f'<span class="c">{img}</span><span class="n">{esc(e["name"])}</span></a>'
    )


def contact_items(dep: dict) -> str:
    c = CFG["contact"]
    email = dep.get("email") or c["email"]
    items = [
        ("phone", f"tel:{c['phone_e164']}", "Ring oss", c["phone"]),
        ("chat", utm(CFG["contact_form_url"], "kontakt-melding"), "Send en melding", "Skjemaet på privatmegleren.no"),
        ("mail", f"mailto:{email}", "Send e-post", email),
        ("home", utm(CFG["valuation_url"], "kontakt-verdivurdering"), "Bestill verdivurdering", "Gratis og uforpliktende"),
    ]
    lis = "".join(
        f'<li><a href="{esc(href)}"><span class="ic">{ICONS[ic]}</span>'
        f'<span class="tx"><b>{esc(title)}</b><span>{esc(sub)}</span></span>'
        f'<span class="go">{ICONS["chevron"]}</span></a></li>'
        for ic, href, title, sub in items
    )
    return f'<ul class="contact-list">{lis}</ul>'


def options(buckets: list[dict], counts: dict, placeholder: str, with_range: bool) -> str:
    out = [f'<option value="">{esc(placeholder)}</option>']
    for b in buckets:
        n = counts.get(b["key"], 0)
        if not n:
            continue
        rng = f' data-min="{b["min"]}" data-max="{b["max"]}"' if with_range else ""
        out.append(f'<option value="{esc(b["key"])}"{rng}>{esc(b["label"])} ({n})</option>')
    return "".join(out)


def bucket_of(value, buckets):
    if value in (None, ""):
        return None
    for b in buckets:
        if b["min"] <= value <= b["max"]:
            return b["key"]
    return None


def oslo_time(iso: str | None) -> str:
    if not iso:
        return ""
    t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(OSLO)
    months = ["januar", "februar", "mars", "april", "mai", "juni", "juli", "august", "september", "oktober", "november", "desember"]
    return f"{t.day}. {months[t.month - 1]} kl. {t:%H.%M}"


# ---------------------------------------------------------------- sider

def page_head(title: str, description: str, canonical: str | None, extra: str = "", *,
              og_image: str = "og.jpg", og_size: tuple[int, int] = (1200, 630), base: str = "",
              noindex: bool | None = None) -> str:
    if noindex is None:
        noindex = bool(CFG.get("noindex"))
    robots = '<meta name="robots" content="noindex">' if noindex else ""
    og_url = abs_url("") if SITE_URL else ""
    canon = f'<link rel="canonical" href="{esc(canonical)}">' if canonical else ""
    ogu = f'<meta property="og:url" content="{esc(og_url)}">' if og_url else ""
    return f"""<!doctype html>
<html lang="nb">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
{robots}
<meta name="theme-color" content="#262830">
<meta name="color-scheme" content="dark">
<meta name="format-detection" content="telephone=no">
<meta property="og:type" content="website">
<meta property="og:locale" content="nb_NO">
<meta property="og:site_name" content="{esc(CFG['office_name'])}">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:image" content="{esc(abs_url(og_image))}">
<meta property="og:image:width" content="{og_size[0]}">
<meta property="og:image:height" content="{og_size[1]}">
{ogu}
<meta name="twitter:card" content="summary_large_image">
{canon}
<link rel="icon" href="{base}favicon.svg" type="image/svg+xml">
<link rel="icon" href="{base}favicon-32.png" sizes="32x32" type="image/png">
<link rel="apple-touch-icon" href="{base}apple-touch-icon.png">
{extra}
</head>"""


def build_index(feed: dict, videos: list[dict]) -> str:
    dep = feed["department"]
    for_sale = feed["for_sale"]
    sold = feed["sold"][: int(CFG["sold_count"])]
    brokers = feed["_brokers"]
    initial = int(CFG["all_initial_count"])
    fonts = CFG["fonts"]

    css = (SRC / "style.css").read_text(encoding="utf-8")
    css = (css.replace("__FONT_DISPLAY__", fonts["display"])
              .replace("__FONT_TEXT_LIGHT__", fonts["text_light"])
              .replace("__FONT_TEXT_REGULAR__", fonts["text_regular"])
              .replace("__FONT_LEAD__", fonts["lead"]))
    css = minify_css(css)
    js = (SRC / "app.js").read_text(encoding="utf-8")
    js_cfg = json.dumps({"sources": CFG["sources"], "defaultSource": CFG["utm"]["default_source"]}, ensure_ascii=False)

    preload = (
        '<link rel="preconnect" href="https://cdn.reeltime.no" crossorigin>'
        f'<link rel="preload" href="{esc(fonts["display"])}" as="font" type="font/otf" crossorigin>'
        f'<link rel="preload" href="{esc(fonts["text_light"])}" as="font" type="font/otf" crossorigin>'
        f'<style>{css}</style>'
        '<script>document.documentElement.className+=" js"</script>'
    )

    title = f"{CFG['office_name']}: boligene fra videoene"
    desc = f"Boligene fra videoene til {CFG['office_name']}, alle boligene til salgs og meglerne. Trykk på boligen du så i videoen."

    # Toppen
    if videos:
        h1, lead = "Boligene fra videoene", "– Trykk på boligen du så i videoen."
    else:
        h1, lead = "Boligene våre", "– Alle boligene vi har til salgs nå."
    parts = [page_head(title, desc, None, preload), "<body>", '<div class="wrap">']
    parts.append(
        '<header class="top">'
        f'<img class="logo" src="logo-liggende.svg" width="252" height="36" alt="{esc(CFG["office_name"])}">'
        f'<h1>{esc(h1)}</h1><p class="lead">{esc(lead)}</p></header>'
    )

    # Nylig i video
    if videos:
        tiles = [render_video(v, i) for i, v in enumerate(videos)]
        if len(tiles) % 2 == 1:
            tiles.append(
                f'<a class="sq v tip" href="#alle"><span class="t" style="position:static">'
                f'<span class="h">Fant du ikke boligen?</span>'
                f'<span class="p">Se alle {len(for_sale)} boligene ↓</span></span></a>'
            )
        parts.append(
            '<section aria-labelledby="t-video"><h2 class="label" id="t-video">Nylig i video</h2>'
            f'<div class="grid">{"".join(tiles)}</div></section>'
        )

    # Alle boliger
    area_counts: dict[str, int] = {}
    price_counts: dict[str, int] = {}
    size_counts: dict[str, int] = {}
    for x in for_sale:
        area_counts[x["_group"]["key"]] = area_counts.get(x["_group"]["key"], 0) + 1
        pb = bucket_of((x.get("prices") or {}).get("askingPrice"), CFG["price_buckets"])
        if pb:
            price_counts[pb] = price_counts.get(pb, 0) + 1
        sb = bucket_of(area_value(x)[0], CFG["size_buckets"])
        if sb:
            size_counts[sb] = size_counts.get(sb, 0) + 1
    cards = "".join(render_card(x, i, initial) for i, x in enumerate(for_sale))
    limit = " limit" if len(for_sale) > initial else ""
    parts.append(
        '<section id="alle" aria-labelledby="t-alle">'
        f'<h2 class="label" id="t-alle">Alle boliger til salgs <span class="count">{len(for_sale)}</span></h2>'
        '<div class="filters" role="group" aria-label="Filtrer boligene">'
        f'<label class="chip"><span class="sr-only">Område</span><select id="f-omrade">{options(CFG["area_groups"], area_counts, "Område", False)}</select></label>'
        f'<label class="chip"><span class="sr-only">Pris</span><select id="f-pris">{options(CFG["price_buckets"], price_counts, "Pris", True)}</select></label>'
        f'<label class="chip"><span class="sr-only">Størrelse</span><select id="f-storrelse">{options(CFG["size_buckets"], size_counts, "Størrelse", True)}</select></label>'
        '</div>'
        '<div class="filter-status" id="filter-status" aria-live="polite"><span id="filter-antall"></span>'
        '<button type="button" class="linkbtn" id="filter-nullstill">Nullstill</button></div>'
        '<p class="empty" id="filter-tom" hidden>Ingen boliger passer akkurat nå. Prøv et annet filter.</p>'
        f'<div class="grid cards{limit}" id="alle-kort">{cards}</div>'
        + (f'<button type="button" class="btn-o" id="vis-alle">Vis alle {len(for_sale)} boliger</button>' if limit else "")
        + '</section>'
    )

    # Nylig solgt
    if sold:
        parts.append(
            '<section aria-labelledby="t-solgt"><h2 class="label" id="t-solgt">Nylig solgt</h2>'
            f'<div class="grid sold">{"".join(render_sold(x) for x in sold)}</div></section>'
        )

    # Skal du selge?
    parts.append(
        '<section class="sell" aria-labelledby="t-selge">'
        '<h2 id="t-selge">Vil du ha boligen din vist slik?</h2>'
        '<p class="lead">– Få en uforpliktende verdivurdering.</p>'
        f'<a class="btn" href="{esc(utm(CFG["valuation_url"], "verdivurdering"))}">Bestill verdivurdering</a>'
        '</section>'
    )

    # Meglerne
    if brokers:
        parts.append(
            '<section aria-labelledby="t-meglere"><h2 class="label" id="t-meglere">Meglerne '
            f'<span class="count">{len(brokers)}</span></h2>'
            f'<div class="brokers">{"".join(render_broker(e) for e in brokers)}</div>'
            '<p class="brokers-hint">Sveip for å se alle. Trykk på en megler for telefon og boliger.</p></section>'
        )

    # Kontakt (også reserve for kontaktknappen når JavaScript er slått av)
    parts.append(
        '<section id="kontakt" aria-labelledby="t-kontakt"><h2 class="label" id="t-kontakt">Kontakt oss</h2>'
        f'{contact_items(dep)}</section>'
    )

    # Bunnen
    c = CFG["contact"]
    updated = oslo_time(feed.get("fetched_at"))
    parts.append(
        '<footer class="foot">'
        f'<img class="logo-st" src="logo-staende.svg" width="310" height="142" alt="PrivatMegleren">'
        '<p class="addr">'
        f'{esc(CFG["office_name"])}<br>'
        f'<a href="{esc(c["maps_url"])}">{esc(c["street"])}, {esc(c["postal"])}</a><br>'
        f'<a href="tel:{esc(c["phone_e164"])}">{esc(c["phone"])}</a><br>'
        f'<a href="{esc(utm(CFG["office_url"], "bunnen"))}">privatmegleren.no/aksept</a></p>'
        + (f'<p class="updated">Boligene hentes fra privatmegleren.no. Sist oppdatert {esc(updated)}.</p>' if updated else "")
        + '</footer>'
    )
    parts.append("</div>")

    # Kontaktknappen
    parts.append('<div class="bar"><a class="btn" id="kontakt-knapp" href="#kontakt">Kontakt oss</a></div>')
    parts.append(f"<script>window.AKSEPT={js_cfg};</script>")
    parts.append(f"<script>{js}</script>")
    parts.append("</body></html>")
    return "\n".join(parts)


def build_redirect(x: dict) -> str:
    target = utm(listing_url(x["estateId"]), "kort-lenke")
    title = f"{x['streetAddress']} – {CFG['office_name']}"
    desc = ("Solgt. " if x.get("sold") else f"{fmt_price(x)}. ") + "Se boligen på privatmegleren.no."
    img = x.get("_img") or x.get("_img_small")
    size = LISTING_PX if x.get("_img") else SOLD_PX
    head = page_head(title, desc, listing_url(x["estateId"]),
                     f'<meta http-equiv="refresh" content="0; url={esc(target)}">',
                     og_image=img or "og.jpg", og_size=(size, size) if img else (1200, 630),
                     base="../../", noindex=True)
    js_cfg = json.dumps({"sources": CFG["sources"], "default": CFG["utm"]["default_source"]})
    return head + f"""
<body style="margin:0;background:#262830;color:#E5E3E4;font-family:Georgia,serif">
<p style="padding:24px 16px">Sender deg videre til boligen … <a style="color:#EDBDF2" href="{esc(target)}">Trykk her hvis ingenting skjer.</a></p>
<script>(function(){{var c={js_cfg};var t={json.dumps(target)};var m=location.search.match(/[?&]src=([^&#]+)/i);
if(m){{var k=decodeURIComponent(m[1]).toLowerCase();var s=c.sources[k]||k.replace(/[^a-z0-9_-]/g,"").slice(0,40);
if(s)t=t.replace("utm_source="+encodeURIComponent(c.default),"utm_source="+encodeURIComponent(s));}}location.replace(t);}})();</script>
</body></html>
"""


def build_404() -> str:
    listing = CFG["listing_url"]
    utm_q = urllib.parse.urlencode({"utm_source": CFG["utm"]["default_source"], "utm_medium": CFG["utm"]["medium"],
                                    "utm_campaign": CFG["utm"]["campaign"], "utm_content": "kort-lenke"})
    home = SITE_URL + "/" if SITE_URL else "/"
    return f"""<!doctype html>
<html lang="nb"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fant ikke siden – {esc(CFG['office_name'])}</title><meta name="robots" content="noindex">
<script>(function(){{var m=location.pathname.match(/\\/b\\/(\\d+)\\/?$/);
if(m){{location.replace({json.dumps(listing)}.replace("{{id}}",m[1])+"?{utm_q}");}}}})();</script>
</head>
<body style="margin:0;background:#262830;color:#E5E3E4;font-family:Georgia,serif">
<div style="max-width:480px;margin:0 auto;padding:48px 16px">
<h1 style="font-weight:400;font-size:30px;margin:0 0 12px">Fant ikke siden</h1>
<p style="color:#A6A4A8;line-height:1.5">Lenken kan være gammel. Alle boligene til {esc(CFG['office_name'])} ligger på forsiden.</p>
<p><a style="color:#EDBDF2" href="{esc(home)}">Til boligene</a></p>
</div></body></html>
"""


def minify_css(css: str) -> str:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = re.sub(r"\s+", " ", css)
    css = re.sub(r"\s*([{}:;,>])\s*", r"\1", css)
    return css.replace(";}", "}").strip()


# ---------------------------------------------------------------- hovedløp

def main() -> int:
    feed_path = DATA / "feed.json"
    if not feed_path.exists():
        fail("data/feed.json finnes ikke. Kjør scripts/fetch_feed.py først.")
        return 1
    feed = json.loads(feed_path.read_text(encoding="utf-8"))
    for_sale = feed.get("for_sale") or []
    sold = feed.get("sold") or []
    dep = feed.get("department") or {}
    if not for_sale:
        fail("Ingen boliger til salgs i data/feed.json.")
        return 1

    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "img").mkdir(parents=True)

    # Bilder og områder
    missing_img = 0
    for x in for_sale:
        x["_group"] = area_group(x)
        x["_img"] = square_image(x.get("images") or [], x["estateId"], LISTING_PX)
        if not x["_img"]:
            missing_img += 1
    for x in sold[: int(CFG["sold_count"])]:
        x["_img_small"] = square_image(x.get("images") or [], f"{x['estateId']}-s", SOLD_PX)

    keys = [k.lower() for k in CFG["broker_title_keywords"]]
    excl = [k.lower() for k in CFG.get("broker_title_exclude", [])]
    brokers = []
    for e in dep.get("employees") or []:
        t = (e.get("title") or "").lower()
        if any(k in t for k in excl) or not any(k in t for k in keys) or not e.get("employeeAlias"):
            continue
        e["_img"] = square_image([e.get("image")], f"m-{e['employeeAlias']}", BROKER_PX, focus_top=True)
        brokers.append(e)
    feed["_brokers"] = brokers

    videos, skipped, video_rows = pick_videos(for_sale, sold)
    for v in videos:
        x = v["listing"]
        if not x.get("_img"):
            x["_img"] = square_image(x.get("images") or [], x["estateId"], LISTING_PX)
    for s in skipped:
        warn(f"Video ikke vist ({s['rad']}: «{s['bolig']}»): {s['grunn']}.")

    if missing_img > 0.2 * len(for_sale):
        fail(f"{missing_img} av {len(for_sale)} boliger mangler bilde.")

    # Skriv sidene
    index = build_index(feed, videos)
    for bad in ("None", "nan", "undefined", "__FONT_"):
        if re.search(rf">[^<]*\b{bad}\b[^<]*<", index) or f'"{bad}"' in index:
            fail(f"Siden inneholder «{bad}». Noe data mangler.")
    (OUT / "index.html").write_text(index, encoding="utf-8")

    redirect_targets = {x["estateId"]: x for x in sold}
    redirect_targets.update({x["estateId"]: x for x in for_sale})
    for eid, x in redirect_targets.items():
        d = OUT / "b" / eid
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.html").write_text(build_redirect(x), encoding="utf-8")

    (OUT / "404.html").write_text(build_404(), encoding="utf-8")
    robots = "User-agent: *\nAllow: /\n"
    (OUT / "robots.txt").write_text(robots, encoding="utf-8")
    (OUT / ".nojekyll").write_text("", encoding="utf-8")
    for name in ("logo-liggende.svg", "logo-staende.svg", "favicon.svg", "favicon-32.png", "apple-touch-icon.png", "og.jpg"):
        if (SRC / name).exists():
            shutil.copyfile(SRC / name, OUT / name)
        else:
            warn(f"Mangler src/{name}.")

    fetched = feed.get("fetched_at")
    age_h = None
    if fetched:
        age_h = round((NOW - dt.datetime.fromisoformat(fetched.replace("Z", "+00:00"))).total_seconds() / 3600, 1)
        if age_h > float(CFG["max_feed_age_hours"]):
            warn(f"Boligdataene er {age_h} timer gamle.")
    status = {
        "ok": not errors,
        "built_at": NOW.isoformat(timespec="seconds"),
        "feed_fetched_at": fetched,
        "feed_age_hours": age_h,
        "feed_stale": bool(age_h is not None and age_h > float(CFG["max_feed_age_hours"])),
        "for_sale": len(for_sale),
        "total_for_sale": feed.get("total_for_sale"),
        "sold_shown": len(sold[: int(CFG["sold_count"])]),
        "brokers": len(brokers),
        "videos": {
            "rows": video_rows,
            "shown": [{"bolig": v["listing"]["streetAddress"], "id": v["listing"]["estateId"], "dato": v["date"].isoformat()} for v in videos],
            "not_shown": skipped,
        },
        "missing_images": missing_img,
        "warnings": warnings,
        "errors": errors,
        "commit": os.environ.get("GITHUB_SHA", "")[:7],
        "site_url": SITE_URL,
    }
    (OUT / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")

    size_kb = round(sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file()) / 1024)
    print(f"[bygg] {len(for_sale)} boliger, {len(videos)} videoer, {len(status['videos']['not_shown'])} videoer ikke vist, "
          f"{len(brokers)} meglere, {len(redirect_targets)} korte lenker, {size_kb} kB totalt.", flush=True)
    if errors:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
