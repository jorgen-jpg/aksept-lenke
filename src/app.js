/* Lenke i bio for PrivatMegleren Aksept.
   Siden virker uten JavaScript. Dette skriptet legger bare til:
   1. Merking av lenker med hvor besøket kom fra (?src=ig gir utm_source=instagram).
   2. Filtrene og «Vis alle boliger».
   3. Kontaktarket som åpnes fra knappen nederst.
   Skrevet i enkel JavaScript, så det virker også i eldre nettlesere inne i appene. */
(function () {
  "use strict";
  var d = document;
  var CFG = window.AKSEPT || {};

  /* 1. Hvor kom besøket fra? */
  function detectSource() {
    var q = window.location.search || "";
    var m = q.match(/[?&]src=([^&#]+)/i);
    if (m) {
      var key = decodeURIComponent(m[1]).toLowerCase();
      var known = (CFG.sources || {})[key];
      if (known) return known;
      key = key.replace(/[^a-z0-9_-]/g, "").slice(0, 40);
      if (key) return key;
    }
    m = q.match(/[?&]utm_source=([^&#]+)/i);
    if (m) {
      var s = decodeURIComponent(m[1]).toLowerCase().replace(/[^a-z0-9_-]/g, "").slice(0, 40);
      if (s) return s;
    }
    var ua = navigator.userAgent || "";
    if (/Instagram/i.test(ua)) return "instagram";
    if (/FBAN|FBAV|FB_IAB|FBIOS|FB4A/i.test(ua)) return "facebook";
    if (/musical_ly|BytedanceWebview|TikTok|trill_/i.test(ua)) return "tiktok";
    return null;
  }

  var source = detectSource();
  var defaultSource = CFG.defaultSource || "lenke-i-bio";
  if (source && source !== defaultSource) {
    var needle = "utm_source=" + encodeURIComponent(defaultSource);
    var links = d.querySelectorAll("a[href*='utm_source=']");
    for (var i = 0; i < links.length; i++) {
      var href = links[i].getAttribute("href");
      if (href.indexOf(needle) !== -1) {
        links[i].setAttribute("href", href.replace(needle, "utm_source=" + encodeURIComponent(source)));
      }
    }
  }
  var names = { instagram: "Instagram", tiktok: "TikTok", facebook: "Facebook", "instagram-story": "Instagram" };
  var mails = d.querySelectorAll("a[href^='mailto:']");
  for (var k = 0; k < mails.length; k++) {
    var from = names[source] ? " fra " + names[source] : "";
    mails[k].setAttribute("href", mails[k].getAttribute("href").split("?")[0] + "?subject=" + encodeURIComponent("Spørsmål" + from));
  }

  /* 2. Filtre og «Vis alle» */
  var grid = d.getElementById("alle-kort");
  var more = d.getElementById("vis-alle");
  var selects = [d.getElementById("f-omrade"), d.getElementById("f-pris"), d.getElementById("f-storrelse")];
  var status = d.getElementById("filter-status");
  var statusText = d.getElementById("filter-antall");
  var reset = d.getElementById("filter-nullstill");
  var empty = d.getElementById("filter-tom");
  var showAll = false;

  function range(sel) {
    var opt = sel && sel.options[sel.selectedIndex];
    if (!opt || !opt.value) return null;
    return { value: opt.value, min: parseFloat(opt.getAttribute("data-min")), max: parseFloat(opt.getAttribute("data-max")) };
  }

  function applyFilters() {
    if (!grid) return;
    var area = range(selects[0]), price = range(selects[1]), size = range(selects[2]);
    var active = !!(area || price || size);
    var cards = grid.querySelectorAll(".card");
    var shown = 0;
    for (var i = 0; i < cards.length; i++) {
      var c = cards[i];
      var ok = true;
      if (area && c.getAttribute("data-omrade") !== area.value) ok = false;
      if (ok && price) {
        var p = parseFloat(c.getAttribute("data-pris"));
        if (isNaN(p) || p < price.min || p > price.max) ok = false;
      }
      if (ok && size) {
        var s = parseFloat(c.getAttribute("data-storrelse"));
        if (isNaN(s) || s < size.min || s > size.max) ok = false;
      }
      if (ok) { c.removeAttribute("hidden"); shown++; } else { c.setAttribute("hidden", ""); }
    }
    for (var j = 0; j < selects.length; j++) {
      if (selects[j]) selects[j].className = selects[j].value ? "on" : "";
    }
    if (active || showAll) grid.className = grid.className.replace(/\s*\blimit\b/g, "");
    else if (!/\blimit\b/.test(grid.className)) grid.className += " limit";
    if (status) status.className = "filter-status" + (active ? " on" : "");
    if (statusText) statusText.textContent = "Viser " + shown + " av " + cards.length + " boliger";
    if (empty) { if (active && shown === 0) empty.removeAttribute("hidden"); else empty.setAttribute("hidden", ""); }
  }

  for (var f = 0; f < selects.length; f++) {
    if (selects[f]) selects[f].addEventListener("change", applyFilters);
  }
  if (reset) reset.addEventListener("click", function () {
    for (var r = 0; r < selects.length; r++) if (selects[r]) selects[r].value = "";
    applyFilters();
  });
  if (more) more.addEventListener("click", function () {
    showAll = true;
    var first = grid && grid.querySelector(".card.more");
    applyFilters();
    if (first) first.focus({ preventScroll: true });
  });
  applyFilters();

  /* 3. Kontaktarket */
  var openBtn = d.getElementById("kontakt-knapp");
  var section = d.getElementById("kontakt");
  if (!openBtn || !section) return;
  var list = section.querySelector(".contact-list");
  var scrim = d.createElement("div");
  scrim.className = "scrim";
  scrim.hidden = true;
  var sheet = d.createElement("div");
  sheet.className = "sheet";
  sheet.setAttribute("role", "dialog");
  sheet.setAttribute("aria-modal", "true");
  sheet.setAttribute("aria-labelledby", "ark-tittel");
  sheet.hidden = true;
  sheet.innerHTML = '<div class="grab" aria-hidden="true"></div><div class="head"><h2 id="ark-tittel">Kontakt Aksept</h2>' +
    '<button type="button" class="close" aria-label="Lukk"><svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true"><path d="M2 2l14 14M16 2L2 16" stroke="#E5E3E4" stroke-width="1.5"/></svg></button></div>';
  if (list) sheet.appendChild(list.cloneNode(true));
  d.body.appendChild(scrim);
  d.body.appendChild(sheet);
  var closeBtn = sheet.querySelector(".close");
  var isOpen = false, pushed = false, lastFocus = null;

  function open() {
    if (isOpen) return;
    isOpen = true;
    lastFocus = d.activeElement;
    scrim.hidden = false;
    sheet.hidden = false;
    // Tving en ny layout før klassen settes, så overgangen vises.
    void sheet.offsetHeight;
    scrim.className = "scrim open";
    sheet.className = "sheet open";
    d.documentElement.className += " no-scroll";
    try { history.pushState({ kontakt: 1 }, "", "#kontakt"); pushed = true; } catch (e) { pushed = false; }
    closeBtn.focus();
  }
  function close(fromHistory) {
    if (!isOpen) return;
    isOpen = false;
    scrim.className = "scrim";
    sheet.className = "sheet";
    d.documentElement.className = d.documentElement.className.replace(/\s*\bno-scroll\b/g, "");
    setTimeout(function () { if (!isOpen) { scrim.hidden = true; sheet.hidden = true; } }, 280);
    if (pushed && !fromHistory) { pushed = false; try { history.back(); } catch (e) {} }
    pushed = false;
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  openBtn.addEventListener("click", function (e) { e.preventDefault(); open(); });
  closeBtn.addEventListener("click", function () { close(false); });
  scrim.addEventListener("click", function () { close(false); });
  window.addEventListener("popstate", function () { if (isOpen) close(true); });
  d.addEventListener("keydown", function (e) {
    if (!isOpen) return;
    if (e.key === "Escape" || e.key === "Esc") { close(false); return; }
    if (e.key === "Tab") {
      var items = sheet.querySelectorAll("a[href], button");
      if (!items.length) return;
      var firstEl = items[0], lastEl = items[items.length - 1];
      if (e.shiftKey && d.activeElement === firstEl) { e.preventDefault(); lastEl.focus(); }
      else if (!e.shiftKey && d.activeElement === lastEl) { e.preventDefault(); firstEl.focus(); }
    }
  });
  // Ring og e-post åpner en annen app; da lukker vi arket så siden er klar når de kommer tilbake.
  sheet.addEventListener("click", function (e) {
    var a = e.target.closest ? e.target.closest("a") : null;
    if (!a) return;
    var href = a.getAttribute("href") || "";
    if (/^(tel|mailto|sms):/i.test(href)) setTimeout(function () { close(false); }, 400);
  });
})();
