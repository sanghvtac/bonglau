import sys
import json
import asyncio
import re
import hashlib
import os
import unicodedata
import requests
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import BytesIO
from PIL import Image
from playwright.async_api import async_playwright

# ──────────────────────────────────────────────
# CAU HINH
# ──────────────────────────────────────────────
BASE_DOMAIN   = "https://phaohoa1.live"
TARGET_URL    = f"{BASE_DOMAIN}/lich-truc-tiep"
HOME_URL      = f"{BASE_DOMAIN}/"
COVER_IMAGE   = f"{BASE_DOMAIN}/images/logo.png"
GITHUB_REPO   = "sanghvtac/bonglau"
GITHUB_BRANCH = "main"
THUMBS_DIR    = "thumbs"

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/122.0.0.0 Safari/537.36")

DAYS_TO_CRAWL   = 2      # Hom nay + ngay mai
LIVE_BATCH_SIZE = 4      # So trang tran mo song song de bat stream
STREAM_WAIT     = 6      # So giay cho player load .m3u8
LIVE_WINDOW_H   = 3.0    # Tran duoc coi la LIVE trong X gio ke tu gio bat dau

# CHI LAY BONG DA. Dat None neu muon lay tat ca cac mon.
SPORT_FILTER: str | None = "Bóng đá"

# Map icon iconify tren trang -> ten mon (trang /lich-truc-tiep khong ghi chu ten mon)
ICON_SPORT = {
    "soccer":        "Bóng đá",
    "football":      "Bóng đá",
    "volleyball":    "Bóng chuyền",
    "basketball":    "Bóng rổ",
    "tennis":        "Tennis",
    "billiards":     "Billiards",
    "badminton":     "Cầu lông",
    "table-tennis":  "Bóng bàn",
    "boxing-glove":  "Boxing",
    "controller":    "Esports",
}
SPORTS = tuple(dict.fromkeys(ICON_SPORT.values()))


# ──────────────────────────────────────────────
# CO DEBUG
#   py crawl_phaohoa.py --dump     (hoac set PHAOHOA_DUMP=1)
#   py crawl_phaohoa.py --debug    (hoac set PHAOHOA_DEBUG=1)
# ──────────────────────────────────────────────
def _flag(env_name: str, argv_name: str) -> bool:
    if os.getenv(env_name, "").strip() in ("1", "true", "True", "yes"):
        return True
    return argv_name in sys.argv


DEBUG_API  = _flag("PHAOHOA_DEBUG", "--debug")
DEBUG_CARD = _flag("PHAOHOA_DUMP",  "--dump")


def generate_id(text):
    return hashlib.md5(text.encode()).hexdigest()[:12]


def slugify(s: str) -> str:
    s = (s or "").lower().replace("đ", "d")
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def unrepeat(s: str) -> str:
    """'Đài LoanĐài Loan' -> 'Đài Loan' (site render 2 ban desktop + mobile).
    Chi cat khi nua chuoi du dai (>=4 ky tu), tranh cat nham ten ngan
    that su co lap am tiet nhu 'KaKa' -> 'Ka'."""
    s = (s or "").strip()
    n = len(s)
    if n >= 8 and n % 2 == 0 and s[: n // 2] == s[n // 2:]:
        return s[: n // 2]
    return s


# ──────────────────────────────────────────────
# ANH
# ──────────────────────────────────────────────
def _fetch_logo(url):
    try:
        proxy = f"https://images.weserv.nl/?url={url}&w=100&h=100&fit=contain&output=png&bg=ececec"
        res = requests.get(proxy, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        return Image.open(BytesIO(res.content)).convert("RGBA")
    except:
        return None


def _build_and_save_thumb(logo_a_url, logo_b_url, match_id):
    os.makedirs(THUMBS_DIR, exist_ok=True)
    path = os.path.join(THUMBS_DIR, f"{match_id}.png")
    try:
        canvas = Image.new("RGBA", (220, 100), (236, 236, 236, 255))
        img_a = _fetch_logo(logo_a_url) if logo_a_url else None
        img_b = _fetch_logo(logo_b_url) if logo_b_url else None
        if img_a:
            canvas.paste(img_a, (0, 0), img_a)
        if img_b:
            canvas.paste(img_b, (110, 0), img_b)
        canvas.save(path, format="PNG", optimize=True)
    except:
        if not os.path.exists(path):
            Image.new("RGBA", (220, 100), (236, 236, 236, 255)).save(path, format="PNG")
    # LUU Y: luon dung '/' cho URL, khong dung os.sep (Windows sinh ra '\')
    return (f"https://raw.githubusercontent.com/{GITHUB_REPO}"
            f"/refs/heads/{GITHUB_BRANCH}/{THUMBS_DIR}/{match_id}.png")


async def make_thumb_async(logo_a_url, logo_b_url, match_id, executor):
    """Chi ghep anh khi CO DU 2 logo doi. Trang /lich-truc-tiep khong co logo doi
    nen se dung avatar BLV / anh bia thay vi tao ra 1 dong PNG xam vo nghia."""
    if not (logo_a_url and logo_b_url):
        return ""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        executor, _build_and_save_thumb, logo_a_url, logo_b_url, match_id
    )


# ──────────────────────────────────────────────
# TIMEZONE
# ──────────────────────────────────────────────
def detect_time_offset():
    local_now  = datetime.now()
    utc_now    = datetime.now(timezone.utc).replace(tzinfo=None)
    diff_hours = round((local_now - utc_now).total_seconds() / 3600)
    print(f"[INFO] Local timezone: UTC+{diff_hours}")
    return diff_hours


def vn_now() -> datetime:
    """Gio VN, chay dung ca tren may VN (UTC+7) lan GitHub Actions (UTC+0)."""
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=7)


# ──────────────────────────────────────────────
# REGEX THOI GIAN
# ──────────────────────────────────────────────
# Trang /lich-truc-tiep: '17:30 23/08/2026'
TIME_DMY_RE  = re.compile(r'(\d{1,2}):(\d{2})\s+(\d{1,2})/(\d{1,2})/(\d{4})')
# Trang chu: '13:00 - 23-08'
TIME_FULL_RE = re.compile(r'(\d{1,2}:\d{2})\s*[-–]\s*(\d{1,2})[-/](\d{1,2})')
TIME_HM_RE   = re.compile(r'^\s*([01]?\d|2[0-3]):([0-5]\d)\s*$')
SCORE_RE     = re.compile(r'^\d+\s*[:\-]\s*\d+$')
STATUS_LC    = ("vs", "trực tiếp", "sắp diễn ra", "xem thêm", "kết thúc", "tạm dừng")


def parse_start_dt(time_text: str) -> datetime | None:
    """'17:30 23/08/2026' -> datetime."""
    m = TIME_DMY_RE.search(time_text or "")
    if not m:
        return None
    hh, mm, dd, mo, yy = (int(x) for x in m.groups())
    try:
        return datetime(yy, mo, dd, hh, mm)
    except ValueError:
        return None


def slug_parts(href: str) -> tuple[str, str]:
    slug = href.rstrip("/").split("/")[-1]
    slug = re.sub(r'-\d{1,2}-\d{1,2}-\d{4}-\d+$', '', slug)
    slug = re.sub(r'-\d{5,}$', '', slug)
    if "-vs-" not in slug:
        return slug, ""
    a, b = slug.split("-vs-", 1)
    return a, b


def slug_to_match_id(href: str) -> str:
    slug = href.rstrip("/").split("/")[-1]
    m = re.search(r'(\d{5,})$', slug)
    return m.group(1) if m else generate_id(href)


def clean_lines(raw_text: str) -> list[str]:
    out, seen = [], set()
    for ln in (raw_text or "").split("\n"):
        ln = unrepeat(ln.strip())
        if not ln or ln in seen:
            continue
        seen.add(ln)
        out.append(ln)
    return out


# ──────────────────────────────────────────────
# TRICH XUAT CARD
#
# v5: doc theo DUNG CAU TRUC THAT cua phaohoa1.live thay vi doan tu text:
#   a[href*='/truc-tiep/']
#     └ .match-schedule-ribbon > div[0] = ten giai, div[1] = 'HH:MM DD/MM/YYYY'
#     └ span.truncate x2               = ten 2 doi
#     └ span[class*='i-mdi:']          = icon mon the thao
#     └ img[src*='commentators']       = avatar BLV (alt = ten BLV)
# Van giu nhanh 'text' de fallback cho trang chu (DOM khac).
# ──────────────────────────────────────────────
CARD_EXTRACT_JS = """
() => {
  const SKIP_IMG = ['/sponsors/', '/images/logo', '/images/footer/',
                    '/images/banner/', '/sports/icons/'];
  const uniq = el => new Set(
    Array.from(el.querySelectorAll("a[href*='/truc-tiep/']"))
         .map(x => x.getAttribute('href'))
  ).size;

  const byHref = new Map();
  document.querySelectorAll("a[href*='/truc-tiep/']").forEach(a => {
    const h = a.getAttribute('href') || '';
    if (!h.includes('/truc-tiep/')) return;
    if (!byHref.has(h)) byHref.set(h, []);
    byHref.get(h).push(a);
  });

  const out = [];
  byHref.forEach((anchors, href) => {
    // The <a> co ribbon la the chuan; khong co thi lay the noi dung dai nhat
    let best = anchors.find(a => a.querySelector('.match-schedule-ribbon')) || anchors[0];
    anchors.forEach(a => {
      if (!best.querySelector('.match-schedule-ribbon') &&
          (a.innerText || '').length > (best.innerText || '').length) best = a;
    });

    let card = best;
    for (let i = 0; i < 10; i++) {
      const p = card.parentElement;
      if (!p || p.tagName === 'BODY') break;
      if (uniq(p) > 1) break;
      card = p;
    }

    // ── Truong co cau truc ──
    let league = '', timeText = '';
    const ribbon = card.querySelector('.match-schedule-ribbon');
    if (ribbon) {
      const kids = ribbon.children;
      if (kids[0]) league   = (kids[0].innerText || '').trim();
      if (kids[1]) timeText = (kids[1].innerText || '').trim();
    }

    const teams = Array.from(card.querySelectorAll('span.truncate'))
      .map(s => (s.innerText || '').trim())
      .filter(Boolean);

    const sports = [];
    card.querySelectorAll("[class*='i-mdi:']").forEach(el => {
      (el.className.baseVal || el.className || '').split(/\\s+/).forEach(c => {
        if (c.startsWith('i-mdi:')) sports.push(c.slice(6));
      });
    });

    let blv = '';
    const blvImg = card.querySelector("img[src*='commentator'], img[src*='avatar']");
    if (blvImg) blv = (blvImg.alt || '').trim();

    // ── Fallback: text tho (dung cho trang chu) ──
    const texts = [card.innerText || ''];
    anchors.forEach(a => texts.push(a.innerText || ''));

    const imgs = [], seenSrc = new Set();
    [card].concat(anchors).forEach(el => {
      el.querySelectorAll('img').forEach(i => {
        const s = i.src || '';
        if (!s || seenSrc.has(s)) return;
        if (SKIP_IMG.some(k => s.includes(k))) return;
        seenSrc.add(s);
        imgs.push({ src: s, alt: (i.alt || '').trim() });
      });
    });

    out.push({
      href: href, league: league, time_text: timeText,
      teams: teams, sports: sports, blv: blv,
      text: texts.join('\\n'), imgs: imgs,
      outer: (card.outerHTML || '').slice(0, 1500)
    });
  });
  return out;
}
"""


async def collect_cards(page) -> list[dict]:
    for _ in range(6):
        await page.mouse.wheel(0, 2500)
        await asyncio.sleep(0.8)

    for _ in range(10):
        try:
            btn = page.locator("text=/Xem th[eê]m/i").first
            if await btn.count() == 0 or not await btn.is_visible():
                break
            await btn.click(timeout=3000)
            await asyncio.sleep(1.2)
            await page.mouse.wheel(0, 2500)
        except Exception:
            break

    try:
        return await page.evaluate(CARD_EXTRACT_JS)
    except Exception as e:
        print(f"  [WARN] collect_cards loi: {e}")
        return []


async def click_sport_filter(page, sport: str) -> bool:
    if not sport:
        return False
    try:
        before = len(await page.evaluate(CARD_EXTRACT_JS))
        tab = page.locator(f"xpath=//*[normalize-space(text())='{sport}']").first
        if await tab.count() == 0:
            print(f"[INFO] Khong thay tab loc mon '{sport}' tren trang")
            return False
        await tab.click(timeout=5000)
        await asyncio.sleep(2.5)
        after = len(await page.evaluate(CARD_EXTRACT_JS))
        print(f"[INFO] Da bam tab loc mon '{sport}': {before} -> {after} card")
        return True
    except Exception as e:
        print(f"[WARN] Khong bam duoc tab mon '{sport}': {e}")
        return False


async def click_day_tabs(page, max_days: int) -> list[dict]:
    all_cards, seen = [], set()

    def absorb(cards):
        added = 0
        for c in cards:
            if c["href"] in seen:
                continue
            seen.add(c["href"])
            all_cards.append(c)
            added += 1
        return added

    absorb(await collect_cards(page))
    if max_days <= 1:
        return all_cards

    try:
        tabs = page.locator(
            "xpath=//*[normalize-space(text())='Hôm Nay' or "
            "normalize-space(text())='Ngày Mai' or "
            "normalize-space(text())='T2' or normalize-space(text())='T3' or "
            "normalize-space(text())='T4' or normalize-space(text())='T5' or "
            "normalize-space(text())='T6' or normalize-space(text())='T7' or "
            "normalize-space(text())='CN']"
        )
        n_tabs = await tabs.count()
    except Exception:
        n_tabs = 0

    if n_tabs == 0:
        print("[WARN] Khong tim thay tab ngay nao")
        return all_cards

    today_idx = 0
    for i in range(n_tabs):
        try:
            if (await tabs.nth(i).inner_text()).strip() == "Hôm Nay":
                today_idx = i
                break
        except Exception:
            continue

    for step in range(1, max_days):
        idx = today_idx + step
        if idx >= n_tabs:
            break
        try:
            label = (await tabs.nth(idx).inner_text()).strip()
            await tabs.nth(idx).click(timeout=5000)
            await asyncio.sleep(2.5)
            added = absorb(await collect_cards(page))
            print(f"[INFO] Tab '{label}': +{added} tran")
        except Exception as e:
            print(f"  [WARN] Khong bam duoc tab #{idx}: {e}")
            break

    return all_cards


def parse_card(card: dict) -> dict | None:
    href = card.get("href", "")
    if not href or "/truc-tiep/" not in href:
        return None

    full_url = href if href.startswith("http") else BASE_DOMAIN + href
    lines    = clean_lines(card.get("text", ""))
    joined   = " | ".join(lines)
    low      = joined.lower()

    home_slug, away_slug = slug_parts(href)

    # ── Ten doi: uu tien span.truncate, fallback doi chieu slug voi text ──
    teams = [unrepeat(t) for t in card.get("teams", []) if t]
    if len(teams) >= 2:
        home, away = teams[0], teams[1]
    else:
        def from_lines(slug):
            if not slug:
                return ""
            for ln in lines:
                s = slugify(ln)
                if s and (s == slug or s.startswith(slug) or slug.startswith(s)) and len(ln) < 40:
                    return ln
            return ""
        home = from_lines(home_slug) or home_slug.replace("-", " ").title()
        away = from_lines(away_slug) or away_slug.replace("-", " ").title()

    # ── Mon the thao: uu tien icon, fallback quet text ──
    sport = ""
    for ic in card.get("sports", []):
        for key, name in ICON_SPORT.items():
            if key in ic:
                sport = name
                break
        if sport:
            break
    if not sport:
        for sp in SPORTS:
            if sp in joined:
                sport = sp
                break

    # ── Giai dau ──
    league = unrepeat(card.get("league", "").strip())
    if not league:
        for ln in lines:
            if TIME_FULL_RE.search(ln) or TIME_DMY_RE.search(ln) or SCORE_RE.match(ln):
                continue
            if ln in (home, away, sport) or ln.lower() in STATUS_LC:
                continue
            if 2 < len(ln) < 60:
                league = ln
                break

    # ── Thoi gian ──
    time_text = card.get("time_text", "") or joined
    start_dt  = parse_start_dt(time_text)
    if start_dt:
        time_str = start_dt.strftime("%H:%M %d/%m")
    else:
        m = TIME_FULL_RE.search(joined)
        if m:
            time_str = f"{m.group(1)} {int(m.group(2)):02d}/{int(m.group(3)):02d}"
        else:
            m2 = next((TIME_HM_RE.match(ln) for ln in lines if TIME_HM_RE.match(ln)), None)
            time_str = (f"{int(m2.group(1)):02d}:{m2.group(2)} "
                        f"{vn_now().strftime('%d/%m')}") if m2 else ""

    # ── Trang thai: badge tren trang > suy ra tu gio bat dau ──
    if "trực tiếp" in low or "đang live" in low:
        is_live = True
    elif "sắp diễn ra" in low or "chưa bắt đầu" in low:
        is_live = False
    elif any(SCORE_RE.match(ln) for ln in lines):
        is_live = True
    elif start_dt:
        now = vn_now()
        is_live = start_dt <= now <= start_dt + timedelta(hours=LIVE_WINDOW_H)
    else:
        is_live = False

    # ── BLV: uu tien alt avatar ('Văn Minh') hon text ('BLV VĂN MINH') ──
    blv = unrepeat(card.get("blv", "").strip())
    if not blv:
        for ln in reversed(lines):
            if ln in (home, away, league, sport):
                continue
            if (TIME_FULL_RE.search(ln) or TIME_DMY_RE.search(ln)
                    or SCORE_RE.match(ln) or TIME_HM_RE.match(ln)):
                continue
            if ln.lower() in STATUS_LC:
                continue
            if 1 < len(ln) <= 25:
                blv = ln
                break

    # ── Logo doi (trang chu moi co) + avatar BLV ──
    imgs = card.get("imgs", [])
    logo_home = logo_away = ""
    for im in imgs:
        s = slugify(im.get("alt", ""))
        if not s:
            continue
        if not logo_home and (s == home_slug or s == slugify(home)):
            logo_home = im.get("src", "")
        elif not logo_away and (s == away_slug or s == slugify(away)):
            logo_away = im.get("src", "")
    avatar = next((im["src"] for im in imgs
                   if "commentator" in im["src"] or "avatar" in im["src"]), "")

    return {
        "match_id":  slug_to_match_id(href),
        "url":       full_url,
        "home":      home,
        "away":      away,
        "logo_home": logo_home,
        "logo_away": logo_away,
        "avatar":    avatar,
        "time_str":  time_str,
        "start_dt":  start_dt,
        "sport":     sport,
        "league":    league,
        "blv":       blv,
        "is_live":   is_live,
        "streams":   [],
        "img_url":   "",
    }


# ──────────────────────────────────────────────
# BAT STREAM .m3u8 TU TRANG TRAN
# ──────────────────────────────────────────────
M3U8_RE = re.compile(r'https?://[^\s"\'<>\\]+\.m3u8[^\s"\'<>\\]*')


async def fetch_match_streams(context, match: dict) -> list[dict]:
    page  = await context.new_page()
    found: list[str] = []
    apis:  list[str] = []

    def on_response(res):
        u = res.url
        if ".m3u8" in u and u not in found:
            found.append(u)
        if DEBUG_API and ("/api/" in u or u.endswith(".json")):
            apis.append(u)

    page.on("response", on_response)
    try:
        await page.goto(match["url"], wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)

        for sel in ["button[aria-label*='Play']", ".vjs-big-play-button",
                    ".jw-icon-display", "[class*='play-button']"]:
            try:
                el = page.locator(sel).first
                if await el.count() and await el.is_visible():
                    await el.click(timeout=2000)
                    break
            except Exception:
                continue

        await asyncio.sleep(STREAM_WAIT)

        html_parts = []
        try:
            html_parts.append(await page.content())
        except Exception:
            pass
        for fr in page.frames:
            try:
                html_parts.append(await fr.content())
            except Exception:
                pass
            try:
                src = fr.url or ""
                if "streamUrl=" in src:
                    from urllib.parse import unquote, urlparse, parse_qs
                    q = parse_qs(urlparse(src).query)
                    for v in q.get("streamUrl", []):
                        html_parts.append(unquote(v))
            except Exception:
                continue

        for chunk in html_parts:
            for u in M3U8_RE.findall(chunk or ""):
                u = u.replace("\\/", "/")
                if u not in found:
                    found.append(u)

        if DEBUG_API and apis:
            with open("phaohoa_debug_api.txt", "a", encoding="utf-8") as f:
                f.write(f"\n### {match['url']}\n" + "\n".join(sorted(set(apis))) + "\n")

    except Exception as e:
        print(f"  [WARN] {match['url']} -> {e}")
    finally:
        page.remove_listener("response", on_response)
        await page.close()

    clean = [u for u in found if "/ads" not in u.lower()]
    clean = sorted(set(clean), key=len, reverse=True)

    blv_default = match.get("blv") or "BLV"
    streams = []
    for idx, url in enumerate(clean[:3]):
        streams.append({
            "blv_name":   blv_default if idx == 0 else f"{blv_default} {idx + 1}",
            "stream_url": url,
            "quality":    "FHD1" if idx == 0 else f"HD{idx}",
        })
    return streams


# ──────────────────────────────────────────────
# TIEU DE
# ──────────────────────────────────────────────
def build_title(m: dict, blv_and_quality: str = "") -> str:
    parts = []
    if m["time_str"]:
        parts.append(m["time_str"])
    if m["league"]:
        parts.append(m["league"])
    if m["home"] and m["away"]:
        parts.append(f"{m['home']} VS {m['away']}")
    elif m["home"]:
        parts.append(m["home"])
    if blv_and_quality:
        parts.append(f"[{blv_and_quality}]")
    elif m.get("blv"):
        parts.append(f"[{m['blv']}]")
    return " ".join(parts)


# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────
async def main():
    now_str = vn_now().strftime("%H:%M %d/%m/%Y")
    detect_time_offset()
    if DEBUG_CARD:
        print("[INFO] DEBUG_CARD BAT -> se ghi phaohoa_debug_card.txt")
    executor = ThreadPoolExecutor(max_workers=8)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1440, "height": 900},
        )
        page = await context.new_page()

        try:
            print(f"[INFO] Dang tai: {TARGET_URL}")
            await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=40000)
            await asyncio.sleep(3)

            await click_sport_filter(page, SPORT_FILTER)
            raw_cards = await click_day_tabs(page, DAYS_TO_CRAWL)
            print(f"[INFO] /lich-truc-tiep -> {len(raw_cards)} card")

            if len(raw_cards) < 5:
                print(f"[INFO] Bo sung tu trang chu: {HOME_URL}")
                await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=40000)
                await asyncio.sleep(3)
                seen = {c["href"] for c in raw_cards}
                for c in await collect_cards(page):
                    if c["href"] not in seen:
                        seen.add(c["href"])
                        raw_cards.append(c)
                print(f"[INFO] Sau khi bo sung -> {len(raw_cards)} card")

            if DEBUG_CARD:
                with open("phaohoa_debug_card.txt", "w", encoding="utf-8") as f:
                    for c in raw_cards:
                        f.write(f"\n{'=' * 70}\n### {c['href']}\n"
                                f"league={c.get('league')!r} time={c.get('time_text')!r}\n"
                                f"teams={c.get('teams')} sports={c.get('sports')} "
                                f"blv={c.get('blv')!r}\n"
                                f"--- TEXT ---\n{c.get('text','')}\n"
                                f"--- IMGS ---\n"
                                + "\n".join(f"{i['alt']} | {i['src']}" for i in c["imgs"])
                                + f"\n--- OUTER HTML ---\n" + c.get("outer", "") + "\n")
                print(f"[INFO] Da ghi {os.path.abspath('phaohoa_debug_card.txt')}")

            all_matches = [x for x in (parse_card(c) for c in raw_cards) if x]

            # Loc mon (icon rat dang tin, nen loc thang khong can giu 'unknown')
            if SPORT_FILTER:
                match_data = [m for m in all_matches
                              if m["sport"] == SPORT_FILTER or not m["sport"]]
                bo = len(all_matches) - len(match_data)
                print(f"[INFO] Loc mon '{SPORT_FILTER}': bo {bo} tran, con {len(match_data)}")
            else:
                match_data = all_matches

            match_data.sort(key=lambda m: m["start_dt"] or datetime.max)

            live_matches = [m for m in match_data if m["is_live"]]
            no_time = sum(1 for m in match_data if not m["time_str"])
            if no_time:
                print(f"[WARN] {no_time} tran khong lay duoc gio")
            print(f"[INFO] {len(match_data)} tran: "
                  f"{len(live_matches)} live, {len(match_data) - len(live_matches)} sap dien ra")

            for i in range(0, len(live_matches), LIVE_BATCH_SIZE):
                batch = live_matches[i:i + LIVE_BATCH_SIZE]
                print(f"[INFO] Bat stream batch {i // LIVE_BATCH_SIZE + 1} "
                      f"({len(batch)} tran)...")
                results = await asyncio.gather(
                    *[fetch_match_streams(context, m) for m in batch],
                    return_exceptions=True
                )
                for m, r in zip(batch, results):
                    m["streams"] = r if isinstance(r, list) else []
                    tag = f"{len(m['streams'])} stream" if m["streams"] else "KHONG co stream"
                    print(f"   -> {m['home']} vs {m['away']}: {tag}")

        finally:
            await browser.close()

    thumb_tasks = [
        make_thumb_async(m["logo_home"], m["logo_away"], m["match_id"], executor)
        for m in match_data
    ]
    thumb_results = await asyncio.gather(*thumb_tasks)
    for m, img_url in zip(match_data, thumb_results):
        m["img_url"] = img_url or m["avatar"] or m["logo_home"] or COVER_IMAGE
    executor.shutdown(wait=False)

    json_output = {
        "name": f"Phao Hoa TV ({now_str})",
        "image": {"url": COVER_IMAGE},
        "groups": [
            {"id": "live",     "name": "🔴 Live",        "channels": []},
            {"id": "upcoming", "name": "🗓 Sắp diễn ra", "channels": []}
        ]
    }
    m3u_content = f"#EXTM3U\n#PLAYLIST: Phao Hoa TV ({now_str})\n"
    vlc_content = f"#EXTM3U\n#PLAYLIST: Phao Hoa TV ({now_str})\n"

    def make_entry(m, stream_url, blv_name, quality):
        blv_qual = f"{blv_name} - {quality}" if blv_name else ""
        title    = build_title(m, blv_qual)
        entry_id = generate_id(m["url"] + blv_qual)
        group    = "LIVE" if m["is_live"] else "UPCOMING"
        stream   = stream_url or "http://0.0.0.0/not-live"
        referer  = BASE_DOMAIN + "/"

        ch_json = {
            "id":      f"ch-{entry_id}",
            "name":    f"⚽ {title}",
            "type":    "single",
            "display": "thumbnail-only",
            "image": {
                "url":              m["img_url"],
                "display":          "contain",
                "padding":          1,
                "background_color": "#ececec",
            },
            "sources": [{
                "id": f"src-{entry_id}",
                "contents": [{
                    "id": f"ct-{entry_id}",
                    "streams": [{
                        "stream_links": [{
                            "url":  stream_url or "",
                            "type": "hls",
                            "request_headers": [
                                {"key": "Referer",    "value": referer},
                                {"key": "Origin",     "value": BASE_DOMAIN},
                                {"key": "User-Agent", "value": "Mozilla/5.0"},
                            ]
                        }]
                    }]
                }]
            }]
        }
        m3u = (
            f'#EXTINF:-1 tvg-id="{entry_id}" '
            f'group-title="{group}", {title}\n'
            f'#EXTVLCOPT:http-referrer={referer}\n'
            f'#EXTVLCOPT:http-user-agent=Mozilla/5.0\n'
            f'{stream}\n'
        )
        vlc_title = re.sub(r' {2,}', ' ',
                           title.replace("[", "").replace("]", "")
                                .replace(" - ", " ")).strip()
        vlc = (
            f'#EXTINF:-1 tvg-id="{entry_id}" '
            f'group-title="{group}", ⚽ {vlc_title}\n'
            f'#EXTVLCOPT:network-caching=1000\n'
            f'#EXTVLCOPT:http-referrer={referer}\n'
            f'#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36\n'
            f'{stream}\n'
        )
        return ch_json, m3u, vlc

    total_entries = 0
    for m in match_data:
        group_idx = 0 if m["is_live"] else 1
        if m["is_live"] and m["streams"]:
            for s in m["streams"]:
                cj, ml, vl = make_entry(m, s["stream_url"], s["blv_name"], s["quality"])
                json_output["groups"][group_idx]["channels"].append(cj)
                m3u_content += ml
                vlc_content += vl
                total_entries += 1
        else:
            cj, ml, vl = make_entry(m, "", "", "")
            json_output["groups"][group_idx]["channels"].append(cj)
            m3u_content += ml
            vlc_content += vl
            total_entries += 1

    with open("phaohoa.json", "w", encoding="utf-8") as f:
        json.dump(json_output, f, ensure_ascii=False, indent=4)
    with open("phaohoa_iptv.txt", "w", encoding="utf-8") as f:
        f.write(m3u_content)
    with open("phaohoa_vlc.txt", "w", encoding="utf-8") as f:
        f.write(vlc_content)

    live_count     = sum(1 for m in match_data if m["is_live"])
    upcoming_count = len(match_data) - live_count
    print(f"\n✅ Hoan thanh luc: {now_str} (Gio VN)")
    print(f"   🔴 Live: {live_count} tran  |  🗓 Sắp diễn ra: {upcoming_count} tran")
    print(f"   📺 Tong entries (BLV x chat luong): {total_entries}")
    print(f"   📄 Da xuat: phaohoa.json | phaohoa_iptv.txt | phaohoa_vlc.txt")


if __name__ == "__main__":
    asyncio.run(main())
