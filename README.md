# Lenke i bio for PrivatMegleren Aksept

En mobilside for lenken i bio på Instagram, TikTok og Facebook. Folk som har sett en boligvideo, finner boligen øverst og kommer rett til annonsen på privatmegleren.no med ett trykk.

Siden viser, fra toppen og ned:

1. **Nylig i video**: boligene fra de siste videoene, nyeste først
2. **Alle boliger til salgs**: alle boligene til Aksept, med filter for område, pris og størrelse
3. **Nylig solgt**: de seks sist solgte boligene
4. **Skal du selge?**: verdivurdering på privatmegleren.no
5. **Meglerne**: bilde og navn, med lenke til meglersiden på privatmegleren.no
6. **Kontakt oss**: ring, melding, e-post og verdivurdering. Knappen nederst på skjermen åpner de samme valgene.

Alle henvendelser går til PMs egne sider, skjemaer, telefon og e-post. Siden samler ikke inn noe selv.

## Slik virker det

Alt går av seg selv i GitHub Actions, omtrent hver time fra 05 til 23. GitHub hopper over mange planlagte kjøringer, så arbeidsflyten prøver hvert kvarter, og `scripts/trenger_bygg.py` stopper forsøket når siden er under 50 minutter gammel. Et bygg går slik:

1. `scripts/fetch_feed.py` åpner privatmegleren.no/aksept i en vanlig nettleser og tar vare på boligene, de solgte og meglerne som siden selv laster. Vi sender ingen egne forespørsler til PMs feed. Bilder, fonter og sporing blokkeres, så besøkene ikke havner i PMs statistikk.
2. `scripts/build.py` lager siden i `site/`, med kvadratiske bilder.
3. `tests/test_site.py` tester siden på ni skjermstørrelser, i nettleserne til Instagram, TikTok og Facebook, og uten JavaScript.
4. Står alle testene, publiseres siden på GitHub Pages.
5. `scripts/commit_data.py` lagrer boligdataene i repoet når noe har endret seg.

Går noe galt, publiseres ikke en halvferdig side. Den forrige versjonen blir liggende.

## Lenkene

| Hvor | Lenke |
| --- | --- |
| Bio på Instagram | `<adresse>/?src=ig` |
| Bio på TikTok | `<adresse>/?src=tt` |
| Facebook | `<adresse>/?src=fb` |
| Story, rett til én bolig | `<adresse>/b/<boligens nummer>/?src=story` |

Boligens nummer er tallet i adressen på privatmegleren.no, for eksempel 138145 i privatmegleren.no/eiendom/138145.

Alle lenker videre til privatmegleren.no får `utm_source`, `utm_medium=social`, `utm_campaign=aksept-lenke-i-bio` og `utm_content` (hvilken del av siden). Da ser PM trafikken i sin egen statistikk. Mangler `?src=`, kjenner siden likevel igjen nettleseren inne i Instagram, TikTok og Facebook.

## Legge til en ny video

Nylig i video kommer fra `data/videos.csv`:

```
dato,bolig,tittel,lenke,vis
2026-11-07,Thereses gate 26A,,https://www.instagram.com/reel/DfciQGYMDp6/,ja
```

- **dato**: når videoen ble lagt ut. `2026-11-07`, `7.11.2026` og `lør. 7. nov.` virker.
- **bolig**: adressen, oppdragsnummeret eller lenken til boligen på privatmegleren.no. Er det flere boliger på samme adresse, bruk oppdragsnummeret.
- **tittel**: valgfri, kort tittel på kortet (maks 30 tegn). Står den tom, vises adressen.
- **lenke**: valgfri lenke til videoen, til oversikten.
- **vis**: `nei` skjuler videoen uten å slette raden.

Videoer vises i 60 dager, maks åtte om gangen. En bolig som blir solgt, får merket «Solgt». Rader som ikke passer med en bolig, vises ikke, og står i `status.json` under `videos.not_shown`.

Den daglige sjekken (under) leser posteoversikten til Teo og legger inn nye videoer her. Settes `video_sheet_csv_url` i `config.json` til en publisert CSV fra et regneark med de samme kolonnene, leses det i tillegg hver time.

## Innstillinger

Alt i `config.json`. Det viktigste:

| Nøkkel | Hva |
| --- | --- |
| `noindex` | `true` holder siden unna Google. Settes til `false` ved lansering. |
| `video_max_age_days`, `video_max_count` | Hvor lenge og hvor mange videoer som vises |
| `sold_count`, `all_initial_count` | Antall solgte, og antall boliger før «Vis alle» |
| `area_groups` | Områdene i filteret. Første treff vinner. |
| `price_buckets`, `size_buckets` | Pris- og størrelsesvalgene i filteret |
| `utm`, `sources` | Merking av lenker, og hva `?src=` betyr |
| `contact` | Telefon, e-post og adresse |

## Når noe feiler

GitHub sender e-post når en kjøring feiler. Siden ligger uansett oppe med siste gode data.

- **«Fikk ikke hentet nye boliger»** som advarsel: et enkelt bomskudd, som regel borte neste time. Gjør ingenting.
- **«Har ikke klart å hente boliger … på flere timer»**: PM har trolig endret nettsiden. Se loggen fra `fetch_feed.py`. Det vanligste er at navnene i feeden har endret seg (`SearchEstate`, `Department`) eller at adressen `privatmegleren.no/aksept?p=0&so=false` ikke lenger viser boligene.
- **Testene feiler**: les meldingen fra `tests/test_site.py`. Den sier hva som er galt, på norsk.
- **En video vises ikke**: se `status.json` på siden, under `videos.not_shown`.

Kjøre for hånd: Actions → «Bygg og publiser» → «Run workflow».

## Kjøre lokalt

```
pip install -r requirements.txt
python -m playwright install chromium
python scripts/fetch_feed.py
python scripts/build.py
python tests/test_site.py
python -m http.server --directory site
```

`scripts/make_assets.py` lager favicon, ikon og delingsbilde på nytt fra logoene i `src/`.

## Valg vi har tatt

- **Ingen informasjonskapsler og ingen sporing** på siden. Derfor trengs ingen samtykkebanner. Måling skjer i PMs egen statistikk via UTM.
- **Fontene lastes fra PMs egen CDN**, de samme filene som privatmegleren.no bruker. Vi kopierer dem ikke.
- **Logoene** er PMs egne filer fra samme CDN.
- **Virker uten JavaScript.** Filtrene og kontaktarket er et tillegg.
- **Bygger på privatmegleren.no.** Endrer PM nettsiden, kan hentingen slutte å virke. Da står siden med siste gode data til det er fikset.

## Daglig sjekk

En planlagt oppgave i Claude går gjennom driften hver morgen etter `docs/daglig-sjekk.md`: kjøringene siste døgn, at siden er oppe og fersk, og nye videoer i posteoversikten. Selve sjekken gjøres av Sonnet.
