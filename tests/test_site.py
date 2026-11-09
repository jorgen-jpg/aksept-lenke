"""Tester den ferdige siden i site/ på mange skjermstørrelser og i appenes nettlesere.

Kjør etter bygget:  python tests/test_site.py
Starter en lokal webserver, åpner siden i Chromium (Playwright) og sjekker at
  - ingenting stikker ut på siden, uansett telefon
  - alle bilder lastes, og det kommer ingen feil i konsollen
  - filtrene, «Vis alle» og kontaktarket virker
  - lenkene merkes riktig fra Instagram, TikTok og Facebook (også uten ?src=)
  - siden virker uten JavaScript
  - de korte lenkene /b/<id>/ sender folk til riktig bolig
Testene går aldri videre til privatmegleren.no. De lenkene fanges opp og sjekkes lokalt.
Avslutter med feilkode hvis noe feiler, slik at siden ikke publiseres.
"""
from __future__ import annotations

import functools
import http.server
import json
import pathlib
import re
import socketserver
import sys
import threading

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
STATUS = json.loads((SITE / "status.json").read_text(encoding="utf-8"))

UA = {
    "instagram-ios": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Instagram 389.0.0.38.84 (iPhone15,2; iOS 18_5; nb_NO; nb; scale=3.00; 1179x2556; 773081296)",
    "instagram-android": "Mozilla/5.0 (Linux; Android 15; Pixel 8 Build/AP4A.250205.002; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/134.0.6998.135 Mobile Safari/537.36 Instagram 372.0.0.48.60 Android (35/15; 420dpi; 1080x2400; Google/google; Pixel 8; shiba; shiba; nb_NO; 712830472)",
    "tiktok-ios": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 musical_ly_39.6.0 JsSdk/2.0 NetType/WIFI Channel/App Store ByteLocale/nb Region/NO",
    "tiktok-android": "Mozilla/5.0 (Linux; Android 14; SM-S918B Build/UP1A.231005.007; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/133.0.6943.137 Mobile Safari/537.36 trill_390004 BytedanceWebview/d8a21c6",
    "facebook-ios": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 [FBAN/FBIOS;FBAV/512.0.0.41.105;FBBV/724384912;FBDV/iPhone15,2;FBMD/iPhone;FBSN/iOS;FBSV/18.5;FBSS/3;FBCR/;FBID/phone;FBLC/nb_NO;FBOP/5]",
}

failures: list[str] = []
checks = 0


def check(cond: bool, msg: str) -> None:
    global checks
    checks += 1
    if not cond:
        failures.append(msg)
        print(f"  FEIL: {msg}", flush=True)


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    """Som GitHub Pages: ukjente adresser får 404.html."""

    def log_message(self, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        page = SITE / "404.html"
        if code == 404 and page.exists():
            body = page.read_bytes()
            self.send_response(404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().send_error(code, message, explain)


def serve() -> tuple[socketserver.TCPServer, str]:
    handler = functools.partial(QuietHandler, directory=str(SITE))
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}/"


def guard(context, base: str, captured: list[str]) -> None:
    """Slipper bare gjennom den lokale siden og PMs fonter. Alt annet fanges."""
    def handle(route):
        url = route.request.url
        if url.startswith(base) or url.startswith("https://cdn.reeltime.no/pm_assets/fonts/"):
            route.continue_()
        else:
            captured.append(url)
            route.fulfill(status=200, content_type="text/html", body="<html><body>ok</body></html>")
    context.route("**/*", handle)


def collect_errors(page, errors: list[str]) -> None:
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: errors.append(f"console: {m.text}") if m.type == "error" else None)


def scroll_all(page) -> None:
    h = page.evaluate("document.body.scrollHeight")
    y = 0
    while y < h:
        page.evaluate(f"window.scrollTo(0,{y})")
        page.wait_for_timeout(60)
        y += 500
        h = page.evaluate("document.body.scrollHeight")
    page.wait_for_timeout(300)


def test_layout(p, browser, base: str) -> None:
    devices = ["iPhone SE", "iPhone 13", "iPhone 15 Pro Max", "Pixel 7", "Galaxy S9+", "Galaxy S5", "iPad Mini"]
    sizes = {"Liten Android 320": {"viewport": {"width": 320, "height": 640}, "is_mobile": True, "has_touch": True, "device_scale_factor": 2},
             "Datamaskin": {"viewport": {"width": 1280, "height": 900}}}
    configs = [(d, p.devices[d]) for d in devices] + list(sizes.items())
    for name, cfg in configs:
        print(f"- Utseende: {name}", flush=True)
        ctx = browser.new_context(**cfg, locale="nb-NO")
        captured: list[str] = []
        guard(ctx, base, captured)
        page = ctx.new_page()
        errors: list[str] = []
        collect_errors(page, errors)
        page.goto(base, wait_until="load")
        scroll_all(page)
        sw, iw = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
        check(sw <= iw, f"{name}: siden er bredere enn skjermen ({sw} > {iw})")
        broken = page.evaluate("[...document.images].filter(i => i.complete && i.naturalWidth === 0 && i.getAttribute('src')).map(i => i.getAttribute('src'))")
        check(not broken, f"{name}: bilder som ikke lastes: {broken[:5]}")
        # Bilder i skjulte kort (etter de første 8) lastes først når de vises. Det er meningen.
        # Meglerbildene til høyre i raden lastes først når man sveiper. Det er også meningen.
        page.wait_for_timeout(500)
        not_loaded = page.evaluate("""[...document.images].filter(i => i.offsetParent !== null && !i.complete
            && i.getBoundingClientRect().left < Math.min(window.innerWidth,
                 (i.closest('.brokers') || document.body).getBoundingClientRect().right)).map(i => i.getAttribute('src'))""")
        not_loaded = len(not_loaded)
        check(not_loaded == 0, f"{name}: {not_loaded} synlige bilder ble ikke ferdige")
        check(not errors, f"{name}: feil i konsollen: {errors[:3]}")
        # Kontaktknappen skal ikke dekke bunnteksten når man har scrollet helt ned.
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(200)
        covered = page.evaluate("""() => { const bar=document.querySelector('.bar').getBoundingClientRect();
            const last=document.querySelector('.foot .addr').getBoundingClientRect(); return last.bottom > bar.top + 1; }""")
        check(not covered, f"{name}: kontaktknappen dekker adressen nederst")
        # Ingen tekst i kortene skal flyte utenfor kortet.
        spill = page.evaluate("""() => [...document.querySelectorAll('.card:not([hidden])')].filter(c => {
            const r=c.getBoundingClientRect(); return [...c.querySelectorAll('span')].some(s => s.getBoundingClientRect().right > r.right + 1); }).length""")
        check(spill == 0, f"{name}: {spill} kort har tekst som flyter utenfor")
        # Trykkflater skal være store nok.
        small = page.evaluate("""() => [...document.querySelectorAll('.bar .btn, .contact-list a, .chip select, #vis-alle')]
            .filter(e => e.offsetParent !== null).filter(e => e.getBoundingClientRect().height < 36).length""")
        check(small == 0, f"{name}: {small} knapper er lavere enn 36 px")
        ctx.close()


def test_sources(p, browser, base: str) -> None:
    cases = [("instagram-ios", "", "instagram"), ("instagram-android", "", "instagram"),
             ("tiktok-ios", "", "tiktok"), ("tiktok-android", "", "tiktok"), ("facebook-ios", "", "facebook"),
             (None, "?src=ig", "instagram"), (None, "?src=tt", "tiktok"), (None, "?src=fb", "facebook"),
             (None, "?src=story", "instagram-story"), (None, "", "lenke-i-bio"),
             ("tiktok-ios", "?src=ig", "instagram")]
    for ua, query, expected in cases:
        print(f"- Merking: {ua or 'vanlig nettleser'} {query}", flush=True)
        kw = dict(p.devices["iPhone 13"])
        if ua:
            kw["user_agent"] = UA[ua]
        ctx = browser.new_context(**kw, locale="nb-NO")
        guard(ctx, base, [])
        page = ctx.new_page()
        page.goto(base + query, wait_until="domcontentloaded")
        hrefs = page.eval_on_selector_all("a[href*='utm_source=']", "els => els.map(e => e.getAttribute('href'))")
        check(len(hrefs) > 70, f"{ua} {query}: for få merkede lenker ({len(hrefs)})")
        wrong = [h for h in hrefs if f"utm_source={expected}&" not in h and not h.endswith(f"utm_source={expected}")]
        check(not wrong, f"{ua} {query}: lenker uten utm_source={expected}: {wrong[:2]}")
        ctx.close()


def test_links(page) -> None:
    print("- Lenker", flush=True)
    hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))")
    listing = [h for h in hrefs if "/eiendom/" in h]
    check(all(re.match(r"^https://privatmegleren\.no/eiendom/\d+\?utm_source=[^&]+&utm_medium=social&utm_campaign=aksept-lenke-i-bio&utm_content=[a-z-]+$", h) for h in listing),
          "boliglenker har feil format")
    ids_on_page = set(re.findall(r"/eiendom/(\d+)", " ".join(listing)))
    ids_feed = {s["id"] for s in STATUS["videos"]["shown"]}
    check(ids_feed <= ids_on_page, "noen videoboliger mangler lenke")
    check(any(h.startswith("tel:+47") for h in hrefs), "mangler ring-lenke")
    check(any(h.startswith("mailto:") for h in hrefs), "mangler e-postlenke")
    check(any("/megler/" in h for h in hrefs), "mangler meglerlenker")
    check(any("verdivurdering" in h for h in hrefs), "mangler verdivurdering")
    bad = [h for h in hrefs if not (h.startswith(("https://", "tel:", "mailto:", "#")) or h.endswith((".html", "/")))]
    check(not bad, f"lenker med ukjent form: {bad[:3]}")


def test_filters(page) -> None:
    print("- Filtre og «Vis alle»", flush=True)
    visible = lambda: page.evaluate("[...document.querySelectorAll('#alle-kort .card')].filter(c => c.offsetParent !== null).length")
    total = page.evaluate("document.querySelectorAll('#alle-kort .card').length")
    check(total == STATUS["for_sale"], f"antall kort ({total}) stemmer ikke med boligene ({STATUS['for_sale']})")
    initial = visible()
    check(initial == min(8, total), f"viser {initial} kort før «Vis alle», ventet 8")
    opts = page.eval_on_selector_all("#f-omrade option", "os => os.map(o => o.value).filter(Boolean)")
    check(len(opts) >= 2, "for få områder i filteret")
    counts_ok = True
    for val in opts:
        page.select_option("#f-omrade", val)
        n = visible()
        expected = page.evaluate(f"document.querySelectorAll('#alle-kort .card[data-omrade=\"{val}\"]').length")
        label = page.evaluate(f"document.querySelector('#f-omrade option[value=\"{val}\"]').textContent")
        if n != expected or f"({n})" not in label:
            counts_ok = False
            print(f"    {val}: viser {n}, ventet {expected}, etikett «{label}»")
    check(counts_ok, "områdefilteret viser feil antall")
    page.select_option("#f-omrade", "")
    for sel in ("#f-pris", "#f-storrelse"):
        for val in page.eval_on_selector_all(f"{sel} option", "os => os.map(o => o.value).filter(Boolean)"):
            page.select_option(sel, val)
            label = page.evaluate(f"document.querySelector('{sel} option[value=\"{val}\"]').textContent")
            n = visible()
            check(f"({n})" in label, f"{sel} {val}: viser {n}, etiketten sier «{label}»")
        page.select_option(sel, "")
    # Kombinasjon som gir null treff skal vise beskjed.
    page.select_option("#f-pris", page.eval_on_selector_all("#f-pris option", "os => os.map(o => o.value).filter(Boolean)")[0])
    page.select_option("#f-storrelse", page.eval_on_selector_all("#f-storrelse option", "os => os.map(o => o.value).filter(Boolean)")[-1])
    if visible() == 0:
        check(page.is_visible("#filter-tom"), "mangler beskjed når ingen boliger passer")
    page.click("#filter-nullstill")
    check(visible() == initial, "«Nullstill» tar ikke siden tilbake til start")
    page.click("#vis-alle")
    check(visible() == total, "«Vis alle» viser ikke alle boligene")
    check(not page.is_visible("#vis-alle"), "«Vis alle» er fortsatt synlig")


def test_sheet(page) -> None:
    print("- Kontaktarket", flush=True)
    page.click("#kontakt-knapp")
    page.wait_for_timeout(400)
    check(page.is_visible(".sheet"), "kontaktarket åpnes ikke")
    links = page.eval_on_selector_all(".sheet .contact-list a", "els => els.map(e => e.getAttribute('href'))")
    check(len(links) == 4, f"kontaktarket har {len(links)} valg, ventet 4")
    check(page.evaluate("document.activeElement && document.activeElement.className") == "close", "fokus står ikke på lukk-knappen")
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)
    check(not page.is_visible(".sheet"), "Escape lukker ikke kontaktarket")
    page.click("#kontakt-knapp")
    page.wait_for_timeout(400)
    page.go_back()
    page.wait_for_timeout(400)
    check(not page.is_visible(".sheet"), "tilbakeknappen lukker ikke kontaktarket")
    check(page.url.split("#")[0].startswith("http://127.0.0.1"), "tilbakeknappen forlot siden")
    page.click("#kontakt-knapp")
    page.wait_for_timeout(400)
    page.mouse.click(10, 10)
    page.wait_for_timeout(400)
    check(not page.is_visible(".sheet"), "trykk utenfor lukker ikke kontaktarket")


def test_no_js(browser, base: str) -> None:
    print("- Uten JavaScript", flush=True)
    ctx = browser.new_context(java_script_enabled=False, viewport={"width": 390, "height": 844}, locale="nb-NO")
    guard(ctx, base, [])
    page = ctx.new_page()
    page.goto(base, wait_until="load")
    shown = page.eval_on_selector_all("#alle-kort .card", "els => els.filter(e => e.offsetParent !== null).length")
    check(shown == STATUS["for_sale"], f"uten JavaScript vises {shown} av {STATUS['for_sale']} boliger")
    check(not page.is_visible(".filters"), "filtrene vises uten JavaScript (de virker ikke da)")
    check(page.get_attribute("#kontakt-knapp", "href") == "#kontakt", "kontaktknappen peker ikke til kontaktdelen")
    check(page.is_visible("#kontakt .contact-list"), "kontaktdelen er skjult uten JavaScript")
    ctx.close()


def test_short_links(browser, base: str) -> None:
    print("- Korte lenker /b/<id>/", flush=True)
    ctx = browser.new_context(locale="nb-NO")
    captured: list[str] = []
    guard(ctx, base, captured)
    page = ctx.new_page()
    eid = STATUS["videos"]["shown"][0]["id"] if STATUS["videos"]["shown"] else None
    if eid:
        page.goto(f"{base}b/{eid}/?src=story", wait_until="load")
        page.wait_for_timeout(300)
        check(page.url.startswith(f"https://privatmegleren.no/eiendom/{eid}?"), f"kort lenke gikk til {page.url}")
        check("utm_source=instagram-story" in page.url, "kort lenke mistet ?src=story")
        page.goto(f"{base}b/{eid}/", wait_until="load")
        check("utm_source=lenke-i-bio" in page.url, "kort lenke uten ?src= har feil kilde")
    # Ukjent id: 404-siden sender likevel videre til PM.
    page.goto(f"{base}b/123456789/", wait_until="load")
    page.wait_for_timeout(300)
    check(page.url.startswith("https://privatmegleren.no/eiendom/123456789?"), f"404-reserven virker ikke ({page.url})")
    ctx.close()


def main() -> int:
    httpd, base = serve()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        test_layout(p, browser, base)
        test_sources(p, browser, base)
        ctx = browser.new_context(**p.devices["iPhone 13"], locale="nb-NO")
        guard(ctx, base, [])
        page = ctx.new_page()
        errors: list[str] = []
        collect_errors(page, errors)
        page.goto(base, wait_until="load")
        test_links(page)
        test_filters(page)
        page.goto(base, wait_until="load")
        test_sheet(page)
        check(not errors, f"feil i konsollen: {errors[:3]}")
        ctx.close()
        test_no_js(browser, base)
        test_short_links(browser, base)
        browser.close()
    httpd.shutdown()
    print(f"\n{checks - len(failures)} av {checks} sjekker OK.")
    if failures:
        print("Feilet:\n  " + "\n  ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
