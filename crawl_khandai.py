import sys
import json
import re
import hashlib
import os
import unicodedata
import requests
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import BytesIO
from PIL import Image
from playwright.sync_api import sync_playwright

# ──────────────────────────────────────────────
# CAU HINH
# ──────────────────────────────────────────────
# Doi domain -> chi sua 1 dong nay.  phaohoa1.live (cu) -> khandai1.link
BASE_DOMAIN   = "https://khandai1.link"
SCHEDULE_PAGE = f"{BASE_DOMAIN}/lich-truc-tiep"
COVER_IMAGE   = f"{BASE_DOMAIN}/images/logo.png"

SITE_NAME     = "Khan Dai TV"
OUT_PREFIX    = "khandai"          # khandai.json | khandai_iptv.txt | khandai_vlc.txt

GITHUB_REPO   = "sanghvtac/bonglau"
GITHUB_BRANCH = "main"
THUMBS_DIR    = "thumbs"

DAYS_TO_CRAWL   = 2       # hom nay + ngay mai
LIVE_WINDOW_H   = 3.0     # tran duoc coi la "dang da" trong X gio ke tu gio bat dau
MATCH_WAIT      = 16      # so giay o lai moi trang tran de player kip goi stream
MATCH_GAP       = 2       # nghi giua 2 trang tran, tranh bi gioi han tan suat
MAX_LIVE_PAGES  = 24      # tran toi da mo trong 1 lan chay (GitHub co gioi han gio)
SCHEDULE_TRIES  = 3       # so lan tai lai trang lich neu chua ra card nao
RETRY_PAUSE     = 20      # giay cho giua 2 lan tai lai

# Mon muon lay. Muon them bong chuyen thi doi thanh {"Bóng đá", "Bóng chuyền"};
# de trong set() de lay tat ca cac mon.
SPORTS_WANTED = {"Bóng đá"}

# Map icon iconify tren trang -> ten mon
ICON_SPORT = {
    "soccer": "Bóng đá", "football": "Bóng đá",
    "volleyball": "Bóng chuyền", "basketball": "Bóng rổ",
    "tennis": "Tennis", "billiards": "Billiards", "badminton": "Cầu lông",
    "table-tennis": "Bóng bàn", "boxing-glove": "Boxing",
    "controller": "Esports", "gamepad-variant": "Esports",
}

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/122.0.0.0 Safari/537.36")


# ──────────────────────────────────────────────
# ═══ VI SAO LAM THEO CACH NAY ═══
#
# Day la kien truc DA CHAY DUOC tren GitHub Actions voi crawl_thiendinh.py
# va crawl_hoiquan.py: mo trang tran bang Playwright roi RINH request .m3u8
# ma player tu goi.
#
# Diem mau chot: script KHONG BAO GIO tu goi /api/. Moi request toi /api/
# deu do chinh trang web phat ra. Ly do: Cloudflare cua site chan request
# /api/ tu IP GitHub khi do la request cua ta, nhung request cua chinh app
# thi van qua (bang chung: trang render ra card that tren GitHub).
#
# Tren moi trang tran ta thu thap 2 nguon song song:
#   1. NGHE LEN phan hoi /api/matches/<slug>/ ma app tu goi
#      -> co san stream_url + ten BLV + status chinh xac
#   2. RINH request .m3u8 tren duong mang (cach cu cua thiendinh/hoiquan)
#      -> phong khi (1) that bai
# ──────────────────────────────────────────────

# Bay ghi lai phan hoi API cua chinh app. Cai TRUOC khi trang tai.
CAPTURE_INIT = """
(() => {
  if (window.__khdCap) return;
  window.__khdCap = [];
  const keep = (u, s, t) => {
    try { if (u && /\\/api\\/matches/.test(u))
            window.__khdCap.push({url:String(u), status:s, text:String(t||'')}); }
    catch (e) {}
  };
  const of = window.fetch;
  window.fetch = function (...a) {
    const p = of.apply(this, a);
    try {
      const u = typeof a[0] === 'string' ? a[0] : (a[0] && a[0].url) || '';
      if (/\\/api\\/matches/.test(u)) {
        p.then(r => { try { const st = r.status;
                            r.clone().text().then(t => keep(u, st, t)); } catch (e) {} })
         .catch(() => {});
      }
    } catch (e) {}
    return p;
  };
  const xo = XMLHttpRequest.prototype.open, xs = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m, u, ...r) {
    this.__khdUrl = u; return xo.call(this, m, u, ...r); };
  XMLHttpRequest.prototype.send = function (...r) {
    this.addEventListener('load', () => {
      try { keep(this.__khdUrl, this.status, this.responseText); } catch (e) {} });
    return xs.apply(this, r); };
})();
"""

# Popup quang cao phu kin man hinh chan het cu bam (thay trong log 21/09).
# Xoa moi lop phu 'fixed' chiem gan het man hinh.
KILL_OVERLAY = """
() => {
  let n = 0;
  document.querySelectorAll('div').forEach(d => {
    try {
      const cs = getComputedStyle(d);
      if (cs.position !== 'fixed') return;
      if ((parseInt(cs.zIndex) || 0) < 50) return;
      const r = d.getBoundingClientRect();
      if (r.width >= innerWidth * 0.8 && r.height >= innerHeight * 0.8) {
        d.remove(); n++;
      }
    } catch (e) {}
  });
  try { document.body.style.overflow = 'auto'; } catch (e) {}
  return n;
}
"""

# Doc card tu DOM trang lich. Cac selector duoi day da doi chieu voi
# HTML that cua site (21/09/2026).
CARD_EXTRACT_JS = """
() => {
  const uniq = el => new Set(
    Array.from(el.querySelectorAll("a[href*='/truc-tiep/']"))
         .map(x => x.getAttribute('href'))).size;
  const byHref = new Map();
  document.querySelectorAll("a[href*='/truc-tiep/']").forEach(a => {
    const h = a.getAttribute('href') || '';
    if (!h.includes('/truc-tiep/')) return;
    if (!byHref.has(h)) byHref.set(h, []);
    byHref.get(h).push(a);
  });
  const out = [];
  byHref.forEach((anchors, href) => {
    let best = anchors.find(a => a.querySelector('.match-schedule-ribbon')) || anchors[0];
    let card = best;
    for (let i = 0; i < 10; i++) {
      const p = card.parentElement;
      if (!p || p.tagName === 'BODY') break;
      if (uniq(p) > 1) break;
      card = p;
    }
    let league = '', timeText = '';
    const rib = card.querySelector('.match-schedule-ribbon');
    if (rib) {
      const k = rib.children;
      if (k[0]) league   = (k[0].innerText || '').trim();
      if (k[1]) timeText = (k[1].innerText || '').trim();
    }
    const teams = Array.from(card.querySelectorAll('span.truncate'))
                       .map(s => (s.innerText || '').trim()).filter(Boolean);
    const sports = [];
    card.querySelectorAll("[class*='i-mdi:']").forEach(el => {
      ((el.className.baseVal || el.className || '') + '').split(/\\s+/)
        .forEach(c => { if (c.startsWith('i-mdi:')) sports.push(c.slice(6)); });
    });
    let blv = '', avatar = '';
    const im = card.querySelector("img[src*='commentator'], img[src*='avatar']");
    if (im) { blv = (im.alt || '').trim(); avatar = im.src || ''; }
    // Logo 2 doi = cac img con lai trong card (bo avatar BLV)
    const logos = Array.from(card.querySelectorAll('img'))
      .map(x => x.src || '')
      .filter(s => s && !/commentator|avatar/i.test(s) && !s.startsWith('data:'));
    out.push({ href, league, time_text: timeText, teams, sports, blv, avatar, logos });
  });
  return out;
}
"""


# ──────────────────────────────────────────────
# TIEN ICH
# ──────────────────────────────────────────────
def generate_id(text):
    return hashlib.md5(text.encode()).hexdigest()[:12]


def slugify(s: str) -> str:
    s = (s or "").lower().replace("đ", "d")
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def abs_url(path: str) -> str:
    if not path:
        return ""
    if path.startswith("http"):
        return path
    return BASE_DOMAIN + (path if path.startswith("/") else "/" + path)


VN_TZ = timezone(timedelta(hours=7))


def vn_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(VN_TZ)


TIME_DMY_RE = re.compile(r'(\d{1,2}):(\d{2})\s+(\d{1,2})/(\d{1,2})/(\d{4})')


def parse_ribbon_time(s: str) -> datetime | None:
    """'17:30 23/08/2026' -> datetime co tzinfo gio VN."""
    m = TIME_DMY_RE.search(s or "")
    if not m:
        return None
    hh, mm, dd, mo, yy = (int(x) for x in m.groups())
    try:
        return datetime(yy, mo, dd, hh, mm, tzinfo=VN_TZ)
    except ValueError:
        return None


def parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=VN_TZ)
    return dt.astimezone(VN_TZ)


def slug_of(href: str) -> str:
    return href.rstrip("/").split("/")[-1]


# ──────────────────────────────────────────────
# ANH: ghep 2 logo -> luu PNG -> tra URL
# ──────────────────────────────────────────────
def _fetch_logo(url):
    try:
        proxy = (f"https://images.weserv.nl/?url={url}"
                 "&w=100&h=100&fit=contain&output=png&bg=ececec")
        res = requests.get(proxy, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        return Image.open(BytesIO(res.content)).convert("RGBA")
    except Exception:
        return None


def build_thumb(logo_a, logo_b, match_id):
    if not (logo_a and logo_b):
        return ""
    os.makedirs(THUMBS_DIR, exist_ok=True)
    path = os.path.join(THUMBS_DIR, f"{match_id}.png")
    try:
        canvas = Image.new("RGBA", (220, 100), (236, 236, 236, 255))
        a, b = _fetch_logo(logo_a), _fetch_logo(logo_b)
        if a:
            canvas.paste(a, (0, 0), a)
        if b:
            canvas.paste(b, (110, 0), b)
        canvas.save(path, format="PNG", optimize=True)
    except Exception:
        if not os.path.exists(path):
            Image.new("RGBA", (220, 100), (236, 236, 236, 255)).save(path, "PNG")
    # Luon dung '/' cho URL (os.path.join tren Windows sinh ra '\')
    return (f"https://raw.githubusercontent.com/{GITHUB_REPO}"
            f"/refs/heads/{GITHUB_BRANCH}/{THUMBS_DIR}/{match_id}.png")


# ──────────────────────────────────────────────
# DOC DU LIEU APP DA NHAN (nghe len)
# ──────────────────────────────────────────────
def read_captured(page) -> tuple[list[dict], list[tuple[str, int]]]:
    """Tra ve (danh sach tran, [(duong dan API, ma HTTP)]) tu bay CAPTURE_INIT.

    Quan trong nhat trong so cac lenh app tu goi la:
        /api/matches/?status=live&page_size=100&ordering=smart
    No tra ve TAT CA tran dang live kem stream_url va ten BLV, va app phat
    no ra moi lan tai trang. Chi can nghe duoc lenh nay la co du link.
    """
    items, codes, seen = [], [], set()
    try:
        caps = page.evaluate("window.__khdCap || []")
    except Exception:
        return items, codes
    for c in caps:
        u = (c.get("url") or "").split("//")[-1]
        codes.append((u[u.find("/"):] if "/" in u else u, c.get("status")))
        try:
            data = json.loads(c.get("text") or "")
        except Exception:
            continue
        if isinstance(data, dict) and data.get("results") is not None:
            batch = data["results"]
        elif isinstance(data, dict) and data.get("slug"):
            batch = [data]
        else:
            continue
        for m in batch:
            k = m.get("slug") or m.get("id")
            if k and k not in seen:
                seen.add(k)
                items.append(m)
    return items, codes


def from_api(m: dict) -> dict:
    """Chuan hoa 1 ban ghi API thanh dang noi bo."""
    streams = []
    for c in (m.get("commentators") or []):
        url = (c.get("stream_url") or "").strip() or \
              (c.get("backup_stream_url") or "").strip()
        if url:
            streams.append({"blv": (c.get("name") or "").strip() or "BLV",
                            "url": url,
                            "blv_live": bool(c.get("is_live")),
                            "avatar": abs_url(c.get("avatar_url") or "")})
    return {
        "slug":      m.get("slug") or "",
        "home":      (m.get("home_team_name") or "").strip(),
        "away":      (m.get("away_team_name") or "").strip(),
        "logo_home": abs_url(m.get("home_team_logo") or ""),
        "logo_away": abs_url(m.get("away_team_logo") or ""),
        "league":    (m.get("tournament_name") or "").strip(),
        "sport":     (m.get("sport_name") or "").strip(),
        "start_dt":  parse_iso(m.get("start_time", "")),
        "status":    str(m.get("status", "")).lower().strip(),
        "match_id":  str(m.get("api_football_id") or m.get("id") or ""),
        "streams":   streams,
    }


# ──────────────────────────────────────────────
# DOC CARD TU DOM
# ──────────────────────────────────────────────
def from_dom(card: dict) -> dict | None:
    href = card.get("href") or ""
    if "/truc-tiep/" not in href:
        return None
    slug = slug_of(href)

    blv = (card.get("blv") or "").strip()
    teams = [t for t in (card.get("teams") or [])
             if t and not re.match(r'^BLV\b', t, re.I)
             and (not blv or slugify(t) != slugify(blv))]
    home = teams[0] if len(teams) > 0 else ""
    away = teams[1] if len(teams) > 1 else ""

    sport = ""
    for ic in (card.get("sports") or []):
        for key, name in ICON_SPORT.items():
            if key in ic:
                sport = name
                break
        if sport:
            break

    logos = card.get("logos") or []
    m = re.search(r'(\d{5,})$', slug)
    return {
        "slug":      slug,
        "home":      home,
        "away":      away,
        "blv":       re.sub(r'^BLV\s+', '', blv, flags=re.I),
        "logo_home": logos[0] if len(logos) > 0 else "",
        "logo_away": logos[1] if len(logos) > 1 else "",
        "league":    (card.get("league") or "").strip(),
        "sport":     sport,
        "start_dt":  parse_ribbon_time(card.get("time_text") or ""),
        "status":    "",                       # DOM khong noi live hay chua
        "match_id":  m.group(1) if m else generate_id(slug),
        "avatar":    card.get("avatar") or "",
        "streams":   [],
    }


def merge(dst: dict, src: dict) -> dict:
    """Gop 2 nguon, uu tien gia tri co that."""
    out = dict(dst)
    for k, v in src.items():
        if k == "streams":
            if v:
                out["streams"] = v
        elif v:
            out[k] = v
    return out


# ──────────────────────────────────────────────
# MO 1 TRANG TRAN: nghe API + rinh .m3u8
# ──────────────────────────────────────────────
M3U8_RE = re.compile(r'https?://[^\s"\'<>\\]+\.m3u8[^\s"\'<>\\]*')


def crawl_match_page(ctx, slug: str) -> dict:
    """Tra ve {'api': {...} hoac None, 'm3u8': [url,...]}"""
    url = f"{BASE_DOMAIN}/truc-tiep/{slug}"
    page = ctx.new_page()
    sniffed: list[str] = []

    def note(u: str):
        if u and ".m3u8" in u and u not in sniffed:
            sniffed.append(u)

    # Nghe CA request LAN response. Quan trong: neu CDN chan IP GitHub thi
    # khong co response nao het, nhung request van phat ra va van lo URL.
    def on_req(req):
        try:
            note(req.url)
        except Exception:
            pass

    def on_resp(res):
        try:
            note(res.url)
        except Exception:
            pass

    page.on("request", on_req)
    page.on("response", on_resp)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2000)
        try:
            page.evaluate(KILL_OVERLAY)
        except Exception:
            pass

        # Giup player khoi dong neu no doi mot cu bam
        for sel in ["button[aria-label*='Play']", ".vjs-big-play-button",
                    ".jw-icon-display", "[class*='play-button']", "video"]:
            try:
                el = page.locator(sel).first
                if el.count() and el.is_visible():
                    el.click(timeout=2000)
                    break
            except Exception:
                continue

        page.wait_for_timeout(MATCH_WAIT * 1000)

        # Quet them trong HTML + iframe (phong khi player kieu khac)
        chunks = []
        try:
            chunks.append(page.content())
        except Exception:
            pass
        for fr in page.frames:
            try:
                chunks.append(fr.content())
            except Exception:
                pass
        for ch in chunks:
            for u in M3U8_RE.findall(ch or ""):
                note(u.replace("\\/", "/"))

        items, _ = read_captured(page)
        api = None
        for m in items:
            if (m.get("slug") or "") == slug:
                api = from_api(m)
                break
        return {"api": api, "m3u8": sniffed}
    except Exception as e:
        print(f"  [WARN] {slug}: {e}")
        return {"api": None, "m3u8": sniffed}
    finally:
        for ev, fn in (("request", on_req), ("response", on_resp)):
            try:
                page.remove_listener(ev, fn)
            except Exception:
                pass
        try:
            page.close()
        except Exception:
            pass


# ──────────────────────────────────────────────
# TIEU DE
# ──────────────────────────────────────────────
def build_title(m: dict, blv: str = "") -> str:
    parts = []
    if m.get("start_dt"):
        parts.append(m["start_dt"].strftime("%H:%M %d/%m"))
    if m.get("league"):
        parts.append(m["league"])
    if m.get("home") and m.get("away"):
        parts.append(f"{m['home']} VS {m['away']}")
    elif m.get("home"):
        parts.append(m["home"])
    tag = blv or m.get("blv") or ""
    if tag:
        parts.append(f"[{tag}]")
    return " ".join(parts)


# ──────────────────────────────────────────────
# CHAN DOAN: khi khong doc duoc card, in ro trang that su tra ve cai gi
# thay vi de phai doan.
# ──────────────────────────────────────────────
CHALLENGE_RE = re.compile(
    r'just a moment|checking your browser|cf[-_]chl|challenge-platform|'
    r'attention required|cf-browser-verification|enable javascript and cookies|'
    r'ddos-guard|are you human|captcha', re.I)


def dump_page(page, resp, lan: int):
    try:
        html = page.content()
    except Exception as e:
        print(f"  [DEBUG] Khong doc duoc noi dung trang: {e}")
        return
    try:
        tieu_de = page.title()
    except Exception:
        tieu_de = "?"
    try:
        chu = page.evaluate(
            "document.body ? document.body.innerText.slice(0,500) : ''")
    except Exception:
        chu = ""
    print(f"  [DEBUG] HTTP {resp.status if resp else '?'} | url sau khi tai: "
          f"{page.url}")
    print(f"  [DEBUG] title: {tieu_de!r}")
    print(f"  [DEBUG] do dai HTML: {len(html)} ky tu")
    print(f"  [DEBUG] chu tren trang: "
          f"{' '.join(chu.split())[:300]!r}")
    if CHALLENGE_RE.search(html):
        print("  [DEBUG] >>> DAY LA TRANG KIEM TRA CHONG BOT "
              "(Cloudflare/DDoS-Guard), khong phai trang lich.")
    elif len(html) < 3000:
        print("  [DEBUG] >>> Trang gan nhu rong: bi chan hoac tra ve trang loi.")
    else:
        print("  [DEBUG] >>> Trang co noi dung nhung khong khop selector card: "
              "co the site da doi giao dien.")
    try:
        with open(f"khandai_debug_{lan}.html", "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  [DEBUG] Da luu khandai_debug_{lan}.html de xem chi tiet")
    except Exception:
        pass


# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────
def main():
    now = vn_now()
    now_str = now.strftime("%H:%M %d/%m/%Y")
    print(f"[INFO] Bat dau luc {now_str} (Gio VN)")

    by_slug: dict[str, dict] = {}

    with sync_playwright() as p:
        # Chay CO GIAO DIEN khi co man hinh ao (workflow dung xvfb-run).
        # Chrome headless bi Cloudflare nhan dien de hon han chrome that;
        # chay duoi xvfb la chrome that, chi khac la ve vao man hinh ao.
        headless = not os.environ.get("DISPLAY")
        args = [
            "--autoplay-policy=no-user-gesture-required",   # cho player tu chay
            "--mute-audio",
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox", "--disable-dev-shm-usage",
        ]
        # May chay cua GitHub da cai san Google Chrome. Dung luon no thi
        # workflow khoi phai tai Chromium (tiet kiem phan lon thoi gian cai),
        # va Chrome that con it bi nhan dien hon Chromium di kem.
        # Neu khong co Chrome thi quay ve Chromium nhu cu.
        browser = None
        for kenh in ("chrome", None):
            try:
                browser = p.chromium.launch(headless=headless, args=args,
                                            **({"channel": kenh} if kenh else {}))
                print(f"[INFO] Trinh duyet: {kenh or 'chromium (playwright)'}, "
                      f"{'headless' if headless else 'co giao dien (xvfb)'}")
                break
            except Exception as e:
                if kenh:
                    print(f"[INFO] Khong co Google Chrome ({str(e)[:60]}), "
                          f"dung Chromium di kem")
                else:
                    raise
        ctx = browser.new_context(
            user_agent=USER_AGENT, locale="vi-VN",
            timezone_id="Asia/Ho_Chi_Minh",
            viewport={"width": 1440, "height": 900},
            extra_http_headers={
                "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
                "sec-ch-ua": '"Chromium";v="122", "Not(A:Brand";v="24", '
                             '"Google Chrome";v="122"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "Upgrade-Insecure-Requests": "1",
            })
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        ctx.add_init_script(CAPTURE_INIT)

        page = ctx.new_page()
        try:
            # Tai trang lich.
            #
            # LUAT SAT: chi tai lai khi KHONG co card nao. Tuyet doi khong
            # tai lai chi vi thieu danh sach live.
            # Bai hoc that (09:34 22/09): lan tai dau da co card, nhung vi
            # thieu danh sach live nen script tai lai -> lan 2 va 3 bi site
            # chan, mat sach card, ket qua ve 0. Cang tai nhieu cang de bi
            # chan, nen moi lan tai them la mot canh bac. Thieu danh sach
            # live thi da co duong khac: mo trang tran de rinh .m3u8.
            cards = []
            for lan in range(1, SCHEDULE_TRIES + 1):
                print(f"[INFO] Mo {SCHEDULE_PAGE} (lan {lan}/{SCHEDULE_TRIES})")
                resp = None
                try:
                    resp = page.goto(SCHEDULE_PAGE, wait_until="domcontentloaded",
                                     timeout=60000)
                except Exception as e:
                    print(f"  [WARN] Tai trang loi: {e}")
                for _ in range(25):
                    page.wait_for_timeout(1000)
                    try:
                        page.evaluate(KILL_OVERLAY)
                        got = page.evaluate(CARD_EXTRACT_JS)
                    except Exception:
                        got = []
                    if got:
                        cards = got
                        break
                if cards:
                    break
                dump_page(page, resp, lan)      # 0 card -> noi ro trang co gi
                if lan < SCHEDULE_TRIES:
                    print(f"  [WARN] Chua co card nao, cho {RETRY_PAUSE}s roi thu lai")
                    page.wait_for_timeout(RETRY_PAUSE * 1000)
            print(f"[INFO] Trang lich: doc duoc {len(cards)} card tu DOM")

            for c in cards:
                d = from_dom(c)
                if d:
                    by_slug[d["slug"]] = d

            # Bam sang cac ngay ke tiep (popup da bi go nen bam duoc)
            try:
                tabs = page.locator(
                    "xpath=//*[normalize-space(text())='Hôm Nay' or "
                    "normalize-space(text())='Ngày Mai' or "
                    "normalize-space(text())='T2' or normalize-space(text())='T3' or "
                    "normalize-space(text())='T4' or normalize-space(text())='T5' or "
                    "normalize-space(text())='T6' or normalize-space(text())='T7' or "
                    "normalize-space(text())='CN']")
                n_tabs = tabs.count()
                today_idx = next((i for i in range(n_tabs)
                                  if tabs.nth(i).inner_text().strip() == "Hôm Nay"), 0)
            except Exception:
                n_tabs, today_idx = 0, 0

            for step in range(1, DAYS_TO_CRAWL):
                idx = today_idx + step
                if idx >= n_tabs:
                    break
                try:
                    nhan = tabs.nth(idx).inner_text().strip()
                    page.evaluate(KILL_OVERLAY)
                    tabs.nth(idx).click(timeout=8000)
                    page.wait_for_timeout(4000)
                    page.evaluate(KILL_OVERLAY)
                    them = 0
                    for c in page.evaluate(CARD_EXTRACT_JS):
                        d = from_dom(c)
                        if d and d["slug"] not in by_slug:
                            by_slug[d["slug"]] = d
                            them += 1
                    print(f"[INFO] Tab '{nhan}': them {them} tran")
                except Exception as e:
                    print(f"  [WARN] Khong bam duoc tab #{idx}: {e}")

            # Neu chua nghe duoc danh sach live, bam ve tab hom nay: app se
            # goi lai API ma KHONG phai tai lai trang -> them mot co hoi
            # mien phi, khong lam tang rui ro bi chan.
            _, codes0 = read_captured(page)
            if not any("status=live" in u and s == 200 for u, s in codes0) \
                    and n_tabs and DAYS_TO_CRAWL > 1:
                try:
                    print("[INFO] Chua co danh sach live -> bam ve tab hom nay")
                    page.evaluate(KILL_OVERLAY)
                    tabs.nth(today_idx).click(timeout=8000)
                    page.wait_for_timeout(6000)
                except Exception as e:
                    print(f"  [WARN] Khong bam duoc tab hom nay: {e}")

            # Nghe len du lieu API ma chinh trang lich da goi
            api_items, codes = read_captured(page)
            if codes:
                print(f"[INFO] App tu goi API {len(codes)} lan:")
                for u, s in sorted(set(codes)):
                    print(f"        {s}  {u[:96]}")
            else:
                print("[WARN] Khong nghe duoc lenh API nao cua app!")
            for m in api_items:
                a = from_api(m)
                if a["slug"]:
                    by_slug[a["slug"]] = merge(by_slug.get(a["slug"], {}), a)
            if api_items:
                print(f"[INFO] Nghe len duoc {len(api_items)} tran tu trang lich")

            if not by_slug:
                print("[ERROR] Khong doc duoc tran nao. Dung lai, KHONG ghi de file cu.")
                sys.exit(1)

            # Loc mon
            if SPORTS_WANTED:
                truoc = len(by_slug)
                bo = [f"{v.get('home')} vs {v.get('away')} ({v.get('sport')})"
                      for v in by_slug.values()
                      if v.get("sport") and v["sport"] not in SPORTS_WANTED]
                by_slug = {k: v for k, v in by_slug.items()
                           if not v.get("sport") or v["sport"] in SPORTS_WANTED}
                print(f"[INFO] Chi lay {sorted(SPORTS_WANTED)}: "
                      f"bo {truoc - len(by_slug)}, con {len(by_slug)}")
                for x in bo:
                    print(f"        (bo) {x}")

            # Tran dang da. Uu tien status that tu API; khong co thi doan
            # theo gio bat dau.
            def dang_da(m):
                if m.get("status"):
                    return m["status"] == "live"
                dt = m.get("start_dt")
                return bool(dt and dt <= now <= dt + timedelta(hours=LIVE_WINDOW_H))

            live_all = [m for m in by_slug.values() if dang_da(m)]
            # Chi mo trang tran cho nhung tran CHUA co link (nghe len that bai).
            # Nghe duoc danh sach live la da du, khoi mo trang -> nhanh hon va
            # it request hon, do la dieu kien tien quyet de khong bi chan.
            candidates = [m for m in live_all if not m.get("streams")]
            candidates.sort(key=lambda m: m.get("start_dt") or now)
            print(f"[INFO] {len(live_all)} tran dang da, {len(candidates)} tran "
                  f"chua co link -> mo trang de rinh .m3u8")

            for idx, m in enumerate(candidates[:MAX_LIVE_PAGES]):
                if idx:
                    page.wait_for_timeout(MATCH_GAP * 1000)
                r = crawl_match_page(ctx, m["slug"])
                if r["api"]:
                    by_slug[m["slug"]] = merge(by_slug[m["slug"]], r["api"])
                    m = by_slug[m["slug"]]
                if not m.get("streams") and r["m3u8"]:
                    # Bo playlist quang cao, uu tien URL day du (co token ky)
                    best = sorted(set(u for u in r["m3u8"]
                                      if not re.search(r'/ads?[/._-]', u, re.I)),
                                  key=len, reverse=True)
                    if best:
                        m["streams"] = [{"blv": m.get("blv") or "BLV",
                                         "url": best[0], "blv_live": True,
                                         "avatar": m.get("avatar", "")}]
                n = len(m.get("streams") or [])
                nguon = "API" if r["api"] and r["api"]["streams"] else (
                    "m3u8" if n else "khong co")
                print(f"   {'🔴' if n else '⚪'} {m.get('home')} vs "
                      f"{m.get('away')}: {n} stream ({nguon})")
        finally:
            try:
                ctx.close()
                browser.close()
            except Exception:
                pass

    match_data = list(by_slug.values())
    match_data.sort(key=lambda m: m.get("start_dt") or datetime.max.replace(tzinfo=VN_TZ))

    # Anh
    ex = ThreadPoolExecutor(max_workers=8)
    thumbs = list(ex.map(lambda m: build_thumb(m.get("logo_home"),
                                               m.get("logo_away"),
                                               m.get("match_id")), match_data))
    ex.shutdown(wait=True)
    for m, t in zip(match_data, thumbs):
        m["img_url"] = t or m.get("avatar") or m.get("logo_home") or COVER_IMAGE

    # ── Xuat file ──
    json_out = {
        "name": f"{SITE_NAME} ({now_str})",
        "image": {"url": COVER_IMAGE},
        "groups": [
            {"id": "live",     "name": "🔴 Live",        "channels": []},
            {"id": "upcoming", "name": "🗓 Sắp diễn ra", "channels": []},
        ],
    }
    m3u = f"#EXTM3U\n#PLAYLIST: {SITE_NAME} ({now_str})\n"
    vlc = f"#EXTM3U\n#PLAYLIST: {SITE_NAME} ({now_str})\n"

    def make_entry(m, stream_url, blv, is_live):
        title = build_title(m, blv)
        eid   = generate_id(m["slug"] + (blv or ""))
        group = "LIVE" if is_live else "UPCOMING"
        ref   = BASE_DOMAIN + "/"
        st    = stream_url or "http://0.0.0.0/not-live"
        cj = {
            "id": f"ch-{eid}", "name": f"⚽ {title}",
            "type": "single", "display": "thumbnail-only",
            "image": {"url": m["img_url"], "display": "contain",
                      "padding": 1, "background_color": "#ececec"},
            "sources": [{"id": f"src-{eid}", "contents": [{"id": f"ct-{eid}",
                "streams": [{"stream_links": [{
                    "url": stream_url or "", "type": "hls",
                    "request_headers": [
                        {"key": "Referer", "value": ref},
                        {"key": "Origin", "value": BASE_DOMAIN},
                        {"key": "User-Agent", "value": "Mozilla/5.0"}]}]}]}]}],
        }
        a = (f'#EXTINF:-1 tvg-id="{eid}" group-title="{group}", {title}\n'
             f'#EXTVLCOPT:http-referrer={ref}\n'
             f'#EXTVLCOPT:http-user-agent=Mozilla/5.0\n{st}\n')
        vt = re.sub(r' {2,}', ' ', title.replace("[", "").replace("]", "")
                                        .replace(" - ", " ")).strip()
        b = (f'#EXTINF:-1 tvg-id="{eid}" group-title="{group}", ⚽ {vt}\n'
             f'#EXTVLCOPT:network-caching=1000\n'
             f'#EXTVLCOPT:http-referrer={ref}\n'
             f'#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
             f'AppleWebKit/537.36\n{st}\n')
        return cj, a, b

    total = live_count = 0
    for m in match_data:
        is_live = bool(m.get("streams")) and (
            m.get("status") == "live" or not m.get("status")
            or m.get("status") == "")
        if m.get("status") == "live":
            is_live = True
        gi = 0 if is_live else 1
        live_count += 1 if is_live else 0

        if is_live and m.get("streams"):
            # CANH BAO: chi gan link khi BLV that su dang len song. API tra
            # stream_url cho ca tran chua da, va do la kenh co dinh cua BLV
            # -> gan bua se lam nguoi xem vao nham tran khac dang phat.
            onair = [s for s in m["streams"] if s.get("blv_live")] or m["streams"]
            for s in onair:
                cj, a, b = make_entry(m, s["url"], s.get("blv", ""), True)
                json_out["groups"][gi]["channels"].append(cj)
                m3u += a
                vlc += b
                total += 1
        else:
            blv = (m.get("streams") or [{}])[0].get("blv", "") or m.get("blv", "")
            cj, a, b = make_entry(m, "", blv, False)
            json_out["groups"][gi]["channels"].append(cj)
            m3u += a
            vlc += b
            total += 1

    with open(f"{OUT_PREFIX}.json", "w", encoding="utf-8") as f:
        json.dump(json_out, f, ensure_ascii=False, indent=4)
    with open(f"{OUT_PREFIX}_iptv.txt", "w", encoding="utf-8") as f:
        f.write(m3u)
    with open(f"{OUT_PREFIX}_vlc.txt", "w", encoding="utf-8") as f:
        f.write(vlc)

    co_link = sum(1 for g in json_out["groups"] for ch in g["channels"]
                  if ch["sources"][0]["contents"][0]["streams"][0]["stream_links"][0]["url"])
    print(f"\n✅ Hoan thanh luc: {now_str} (Gio VN)")
    print(f"   🔴 Live: {live_count}  |  🗓 Sắp diễn ra: {len(match_data) - live_count}")
    print(f"   📺 Tong entries: {total} (co link stream: {co_link})")
    print(f"   📄 Da xuat: {OUT_PREFIX}.json | {OUT_PREFIX}_iptv.txt | {OUT_PREFIX}_vlc.txt")


if __name__ == "__main__":
    main()
