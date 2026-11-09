"""Lager faste filer i src/: favicon, ikon for hjemskjerm og delingsbilde (og.jpg).

Kjøres for hånd ved behov (ikke i hvert bygg):  python scripts/make_assets.py
Bruker PMs egne logofiler i src/ og fontene fra PMs CDN.
"""
from __future__ import annotations

import json
import pathlib
import re

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def monogram_svg() -> str:
    svg = (SRC / "logo-staende.svg").read_text(encoding="utf-8")
    # Bare kvadratet med P-en (øverst i den stående logoen).
    svg = re.sub(r'width="\d+" height="\d+" viewBox="[^"]+"', 'width="100" height="100" viewBox="104.5 -2.5 100 100"', svg, count=1)
    return svg


def main() -> None:
    mono = monogram_svg()
    (SRC / "favicon.svg").write_text(mono, encoding="utf-8")
    fonts = CFG["fonts"]
    stacked = (SRC / "logo-staende.svg").read_text(encoding="utf-8")
    font_css = f"""
    @font-face {{ font-family: D; src: url('{fonts["display"]}'); }}
    @font-face {{ font-family: L; src: url('{fonts["lead"]}'); }}
    body {{ margin: 0; background: #262830; }}
    """
    icon_html = f"""<html><head><style>{font_css}
      .i {{ width: 180px; height: 180px; background: #262830; display: flex; align-items: center; justify-content: center; }}
      .i svg {{ width: 118px; height: 118px; }}</style></head>
      <body><div class="i">{mono}</div></body></html>"""
    og_html = f"""<html><head><style>{font_css}
      .og {{ width: 1200px; height: 630px; background: #262830; position: relative; display: flex; flex-direction: column; align-items: center; justify-content: center; }}
      .og::after {{ content: ""; position: absolute; top: 28px; right: 28px; bottom: 28px; left: 28px; border: 1.5px solid rgba(174,98,56,.75); }}
      .og svg {{ width: 290px; height: auto; }}
      h1 {{ font-family: D, Georgia, serif; font-weight: 400; color: #E5E3E4; font-size: 84px; line-height: 1; margin: 54px 0 0; }}
      p {{ font-family: L, Georgia, serif; color: #EDBDF2; font-size: 30px; margin: 18px 0 0; }}</style></head>
      <body><div class="og">{stacked}<h1>Boligene fra videoene</h1><p>– Trykk på boligen du så i videoen.</p></div></body></html>"""
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 180, "height": 180})
        page.set_content(icon_html, wait_until="networkidle")
        page.locator(".i").screenshot(path=str(SRC / "apple-touch-icon.png"))
        page = b.new_page(viewport={"width": 180, "height": 180}, device_scale_factor=32 / 180)
        page.set_content(icon_html, wait_until="networkidle")
        page.locator(".i").screenshot(path=str(SRC / "favicon-32.png"))
        page = b.new_page(viewport={"width": 1200, "height": 630})
        page.set_content(og_html, wait_until="networkidle")
        page.evaluate("document.fonts.ready")
        page.wait_for_timeout(400)
        page.locator(".og").screenshot(path=str(SRC / "og.jpg"), type="jpeg", quality=88)
        b.close()
    print("Laget favicon.svg, favicon-32.png, apple-touch-icon.png og og.jpg i src/.")


if __name__ == "__main__":
    main()
