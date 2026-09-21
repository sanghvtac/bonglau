import sys
import json
import re
import hashlib
import os
import requests
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import BytesIO
from urllib.parse import urlencode
from PIL import Image

# ──────────────────────────────────────────────
# CAU HINH
# ──────────────────────────────────────────────
# Site doi ten/domain lien tuc. Doi domain -> chi sua 1 dong BASE_DOMAIN duoi day.
#   phaohoa1.live (cu)  ->  khandai1.link (hien tai)
BASE_DOMAIN   = "https://khandai1.link"
API_MATCHES   = f"{BASE_DOMAIN}/api/matches/"
COVER_IMAGE   = f"{BASE_DOMAIN}/images/logo.png"

# Ten hien thi trong app TV / playlist
SITE_NAME     = "Khan Dai TV"
# Tien to MOI ten file xuat ra + file debug:
#   khandai.json | khandai_iptv.txt | khandai_vlc.txt | khandai_debug_api.json
OUT_PREFIX    = "khandai"

GITHUB_REPO   = "sanghvtac/bonglau"
GITHUB_BRANCH = "main"
THUMBS_DIR    = "thumbs"

DAYS_TO_CRAWL = 2        # Hom nay + ngay mai (luon kem ca hom qua, xem fetch_matches)
# Dung DUNG page_size ma chinh trang web dung (18) va lat trang bang 'next'.
# Xem ghi chu "BAT CHUOC APP" o muc GOI API ben duoi.
PAGE_SIZE     = 18
HTTP_TIMEOUT  = 20
HTTP_RETRY    = 3

# CHI LAY BONG DA. Dat None neu muon lay tat ca cac mon.
# Gia tri lay tu truong 'sport_slug' cua API: football | bong-chuyen | ...
SPORT_FILTER: str | None = "football"

# status cua API: 'live' | 'scheduled' | (ket thuc)
LIVE_STATUSES     = {"live"}
FINISHED_STATUSES = {"finished", "ended", "ft", "full_time",
                     "canceled", "cancelled", "postponed", "abandoned"}

SCHEDULE_PAGE = f"{BASE_DOMAIN}/lich-truc-tiep"

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/122.0.0.0 Safari/537.36")

# Header mo phong trinh duyet that. Cloudflare cua site chan /api/ kha gat:
# request thieu cac header sec-* / Accept-Language thuong bi tra 403.
API_HEADERS = {
    "User-Agent":       USER_AGENT,
    "Accept":           "application/json, text/plain, */*",
    "Accept-Language":  "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding":  "gzip, deflate",
    "Referer":          SCHEDULE_PAGE,
    "Origin":           BASE_DOMAIN,
    "sec-ch-ua":        '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
    "sec-ch-ua-mobile":   "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest":   "empty",
    "Sec-Fetch-Mode":   "cors",
    "Sec-Fetch-Site":   "same-origin",
    "Connection":       "keep-alive",
}


# ──────────────────────────────────────────────
# CO DEBUG
#   py crawl_khandai.py --dump     (hoac set KHANDAI_DUMP=1)
# Ghi nguyen JSON API ra file de soi khi site doi cau truc.
# ──────────────────────────────────────────────
def _flag(env_name: str, argv_name: str) -> bool:
    if os.getenv(env_name, "").strip() in ("1", "true", "True", "yes"):
        return True
    return argv_name in sys.argv


DEBUG_DUMP     = _flag("KHANDAI_DUMP", "--dump")
DEBUG_DUMP_FILE = f"{OUT_PREFIX}_debug_api.json"


def generate_id(text):
    return hashlib.md5(text.encode()).hexdigest()[:12]


def abs_url(path: str) -> str:
    """'/media/teams/logos/x.jpg' -> 'https://khandai1.link/media/teams/logos/x.jpg'"""
    if not path:
        return ""
    if path.startswith("http"):
        return path
    return BASE_DOMAIN + (path if path.startswith("/") else "/" + path)


# ──────────────────────────────────────────────
# TIMEZONE
# ──────────────────────────────────────────────
VN_TZ = timezone(timedelta(hours=7))


def vn_now() -> datetime:
    """Gio VN, dung ca tren may VN (UTC+7) lan GitHub Actions (UTC+0)."""
    return datetime.now(VN_TZ)


def parse_start(iso_str: str) -> datetime | None:
    """'2026-09-21T14:00:00+07:00' -> datetime co tzinfo, quy ve gio VN."""
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=VN_TZ)
    return dt.astimezone(VN_TZ)


# ──────────────────────────────────────────────
# ANH: Ghep 2 logo doi -> luu file PNG -> tra URL
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


# ──────────────────────────────────────────────
# GOI API
#
# Da do truc tiep tren khandai1.link (21/09/2026): trang /lich-truc-tiep
# goi dung 2 endpoint duoi day, va ban than DANH SACH da kem san
# 'commentators[].stream_url' (link .m3u8 that).
#
# Cach cu (mo trang tran roi rinh request .m3u8) KHONG chay duoc tren
# GitHub Actions: Chromium headless khong tu phat video nen HLS.js khong
# bao gio goi file .m3u8, ket qua la stream rong. Day la ly do doi sang API.
#
# ─── BAT CHUOC APP ───────────────────────────────────────────────────
# Cloudflare cua site chan /api/ tu IP datacenter, NHUNG khong chan tat ca:
# chinh trang web van goi API duoc tu IP GitHub (bang chung: trang render
# ra 6 card that). Da hook window.fetch tren trang that de xem app goi gi:
#
#   GET /api/matches/?page_size=18&page=1&ordering=smart&start_time__date=...
#   headers: { "Content-Type": "application/json" }        <- chi co thoi
#
# Khong token, khong header rieng. Khac biet DUY NHAT giua truy van cua app
# va truy van tung bi 403 cua ta la tham so 'status=live'. Vi vay:
#   - KHONG dung endpoint '?status=live' nua
#   - dung dung page_size=18 va lat trang bang 'next' nhu app
#   - gui dung header 'Content-Type: application/json' nhu app
# Tran dang live van nhan ra duoc qua truong 'status' trong tung ban ghi.
#
# NHAT KY GO LOI (21/09/2026, cung 1 repo GitHub Actions):
#   15:18  Ban Playwright cu, DE TRANG TU GOI API  -> LAY DUOC 5 tran (OK)
#   15:37  Them tang 'requests' goi thang /api/    -> 403
#   15:45  'requests' truoc, roi Chromium          -> 403 CA HAI
# => Chromium headless KHONG phai van de (15:18 da chay ngon). Cai moi
#    xuat hien la tang 'requests'; nhieu kha nang no bi Cloudflare gan co
#    va lam IP bi chan luon, khien Chromium chay sau bi va lay.
# => Mac dinh CHI dung trinh duyet, va de trang tu tai binh thuong truoc
#    (giong het kich ban 15:18) roi moi fetch them trong trang.
#    Muon thu lai tang requests: dat bien moi truong KHANDAI_USE_REQUESTS=1
# ──────────────────────────────────────────────
USE_REQUESTS = os.getenv("KHANDAI_USE_REQUESTS", "").strip() in ("1", "true", "yes")


# ── Tang phu (MAC DINH TAT): requests thuan ─────────────────────────
class RequestsTransport:
    name = "requests"

    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update(API_HEADERS)

    def open(self):
        # Vao trang HTML truoc de nhan cookie cua Cloudflare/Django,
        # sau do moi goi /api/ giong het trinh duyet that.
        try:
            self.s.get(SCHEDULE_PAGE,
                       headers={"Accept": "text/html,application/xhtml+xml",
                                "Sec-Fetch-Dest": "document",
                                "Sec-Fetch-Mode": "navigate",
                                "Sec-Fetch-Site": "none"},
                       timeout=HTTP_TIMEOUT)
        except Exception:
            pass

    def get_json(self, url: str, params: dict | None = None):
        r = self.s.get(url, params=params, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass


# ── Tang chinh: goi API TU BEN TRONG trinh duyet ────────────────────
class BrowserTransport:
    """Mo trang bang Chromium roi chay fetch() ngay trong trang.

    Quan trong: de trang TU TAI BINH THUONG truoc (chinh no se goi /api/
    de ve danh sach tran). Neu buoc nay ra duoc card thi chac chan IP dang
    goi API duoc -> sau do fetch them moi an toan. Day dung la kich ban
    da chay duoc tren GitHub Actions luc 15:18 ngay 21/09/2026.
    """
    name = "trinh duyet (Playwright)"

    # Header y HET cai chinh trang web dung (da bat duoc bang cach hook
    # window.fetch tren trang that): chi mot dong Content-Type, khong token.
    _JS = """
    async (u) => {
      const r = await fetch(u, { headers: { 'Content-Type': 'application/json' } });
      if (!r.ok) return { __status: r.status };
      return await r.json();
    }
    """

    _COUNT_CARDS = """
    () => new Set(
      Array.from(document.querySelectorAll("a[href*='/truc-tiep/']"))
           .map(a => a.getAttribute('href'))
    ).size
    """

    # ── BAY GHI LAI PHAN HOI API CUA CHINH APP ──────────────────────
    # Cai TRUOC khi trang tai. Khong tu goi API, chi nghe len ket qua ma
    # app nhan duoc. Do la duong DA CHUNG MINH chay duoc tu IP GitHub.
    _CAPTURE_INIT = """
    (() => {
      if (window.__khdCap) return;
      window.__khdCap = [];
      const keep = (u, t) => {
        try { if (u && /\\/api\\/matches/.test(u) && t)
                window.__khdCap.push({ url: String(u), text: String(t) }); }
        catch (e) {}
      };
      const of = window.fetch;
      window.fetch = function (...a) {
        const p = of.apply(this, a);
        try {
          const u = typeof a[0] === 'string' ? a[0] : (a[0] && a[0].url) || '';
          if (/\\/api\\/matches/.test(u)) {
            p.then(r => { try { r.clone().text().then(t => keep(u, t)); } catch (e) {} });
          }
        } catch (e) {}
        return p;
      };
      const xo = XMLHttpRequest.prototype.open;
      const xs = XMLHttpRequest.prototype.send;
      XMLHttpRequest.prototype.open = function (m, u, ...r) {
        this.__khdUrl = u; return xo.call(this, m, u, ...r);
      };
      XMLHttpRequest.prototype.send = function (...r) {
        this.addEventListener('load', () => {
          try { keep(this.__khdUrl, this.responseText); } catch (e) {}
        });
        return xs.apply(this, r);
      };
    })();
    """

    def __init__(self):
        self._pw = self._browser = self._ctx = self._page = None
        self.cards_on_load = 0
        self.direct_api_ok = None      # None = chua thu

    def open(self):
        from playwright.sync_api import sync_playwright
        self._pw      = sync_playwright().start()
        # Giu nguyen cach khoi dong da chay duoc luc 15:18, chi them vai
        # co lam giam dau hieu tu dong hoa.
        self._browser = self._pw.chromium.launch(args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ])
        self._ctx = self._browser.new_context(
            user_agent=USER_AGENT, locale="vi-VN",
            timezone_id="Asia/Ho_Chi_Minh",
            viewport={"width": 1440, "height": 900},
            extra_http_headers={"Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7"})
        self._ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        self._ctx.add_init_script(self._CAPTURE_INIT)      # bay phai cai TRUOC
        self._page = self._ctx.new_page()

        self._page.goto(SCHEDULE_PAGE, wait_until="domcontentloaded", timeout=60000)
        # Cho chinh trang tu goi API roi render card (toi da ~30s).
        # Dieu kien dung: da nghe duoc phan hoi API, HOAC da co card.
        caps = 0
        for _ in range(30):
            self._page.wait_for_timeout(1000)
            caps = self._cap_count()
            try:
                n = self._page.evaluate(self._COUNT_CARDS)
            except Exception:
                n = 0
            if n:
                self.cards_on_load = n
            if caps and n:
                break
        if caps:
            print(f"[INFO] Trang tu goi API {caps} lan, render {self.cards_on_load} "
                  f"card -> nghe len duoc")
        elif self.cards_on_load:
            print(f"[WARN] Co {self.cards_on_load} card nhung khong nghe duoc "
                  "loi goi API nao (app co the lay tu bo nho dem).")
        else:
            print("[WARN] Trang khong render duoc card nao. Co the /api/ dang "
                  "bi chan hoan toan hoac site dang cham.")

    @staticmethod
    def _with_format_json(url: str, params: dict | None) -> str:
        """Ghep '?...&format=json'. Django REST se tra JSON tho thay vi trang HTML."""
        if params:
            return url + "?" + urlencode({**params, "format": "json"})
        if "format=json" in url:
            return url
        return url + ("&" if "?" in url else "?") + "format=json"

    def _ensure_on_site(self):
        """fetch() phai chay tu mot trang cua chinh site (cung goc)."""
        try:
            cur = self._page.url or ""
        except Exception:
            cur = ""
        if (not cur.startswith(BASE_DOMAIN)) or "/api/" in cur:
            self._page.goto(SCHEDULE_PAGE, wait_until="domcontentloaded",
                            timeout=45000)
            self._page.wait_for_timeout(1500)

    def get_json(self, url: str, params: dict | None = None):
        """Goi API GIONG HET cach chinh trang web goi (xem ghi chu BAT CHUOC APP).

        Cach 1 (chinh): fetch() trong trang, header y het app.
        Cach 2 (du phong): dieu huong toi URL co '?format=json'.
        """
        full = url + ("?" + urlencode(params) if params else "")
        last = None

        for attempt in range(3):
            if attempt:
                self._page.wait_for_timeout(2000 * attempt)      # cho lui dan
            try:
                self._ensure_on_site()
                data = self._page.evaluate(self._JS, full)
                if isinstance(data, dict) and "__status" in data:
                    last = RuntimeError(f"HTTP {data['__status']} (fetch) "
                                        f"{full[len(BASE_DOMAIN):][:80]}")
                    continue
                self._page.wait_for_timeout(500)   # goi thua thot cho lich su
                return data
            except Exception as e:
                last = e

        # Du phong: mo thang URL API nhu mo mot trang
        try:
            nav  = self._with_format_json(url, params)
            resp = self._page.goto(nav, wait_until="domcontentloaded", timeout=45000)
            if resp is None or resp.status < 400:
                txt = self._page.evaluate(
                    "document.body ? document.body.innerText : ''")
                return json.loads(txt)
            last = RuntimeError(f"HTTP {resp.status} khi mo "
                                f"{nav[len(BASE_DOMAIN):][:80]}")
        except Exception as e:
            last = e
        raise last

    # ── CHE DO NGHE LEN: de app tu goi API, ta chi doc ket qua ──────
    _DAY_TABS_XPATH = (
        "xpath=//*[normalize-space(text())='Hôm Nay' or "
        "normalize-space(text())='Ngày Mai' or "
        "normalize-space(text())='T2' or normalize-space(text())='T3' or "
        "normalize-space(text())='T4' or normalize-space(text())='T5' or "
        "normalize-space(text())='T6' or normalize-space(text())='T7' or "
        "normalize-space(text())='CN']")

    def _cap_count(self) -> int:
        """So phan hoi API da nghe duoc (dung de biet app da goi xong chua)."""
        try:
            return int(self._page.evaluate("(window.__khdCap || []).length"))
        except Exception:
            return 0

    def _wait_new_capture(self, before: int, seconds: int = 20) -> bool:
        """Cho den khi app goi them it nhat 1 request API nua.

        QUAN TRONG: khong duoc cho theo 'da co card chua' - card cua ngay
        truoc van con hien tren man hinh nen dieu kien do dung ngay lap tuc,
        trong khi app chua kip goi API cho ngay moi.
        """
        for _ in range(seconds):
            self._page.wait_for_timeout(1000)
            if self._cap_count() > before:
                self._page.wait_for_timeout(800)   # cho body ve not
                return True
        return False

    def _read_captured(self) -> list[dict]:
        """Doc kho phan hoi API ma bay da ghi lai, tach ra tung tran."""
        out, seen = [], set()
        try:
            caps = self._page.evaluate("window.__khdCap || []")
        except Exception:
            return out
        for c in caps:
            try:
                data = json.loads(c.get("text") or "")
            except Exception:
                continue
            if isinstance(data, dict) and data.get("results") is not None:
                items = data["results"]
            elif isinstance(data, dict) and data.get("slug"):
                items = [data]
            else:
                continue
            for m in items:
                key = m.get("slug") or m.get("id")
                if key and key not in seen:
                    seen.add(key)
                    out.append(m)
        return out

    def capture_matches(self, days: int) -> list[dict]:
        """Bam lan luot cac tab ngay de app tu goi API, roi gom ket qua.

        Khong he tu phat request nao toi /api/ -> khong dinh luat chan.
        """
        self._ensure_on_site()
        try:
            tabs   = self._page.locator(self._DAY_TABS_XPATH)
            n_tabs = tabs.count()
        except Exception:
            n_tabs = 0

        today_idx = 0
        for i in range(n_tabs):
            try:
                if (tabs.nth(i).inner_text()).strip() == "Hôm Nay":
                    today_idx = i
                    break
            except Exception:
                continue

        # Hom qua (bat tran bat dau khuya ma gio van da) -> hom nay -> cac ngay sau
        for step in range(-1, days):
            idx = today_idx + step
            if idx < 0 or idx >= n_tabs:
                continue
            before = self._cap_count()
            try:
                nhan = (tabs.nth(idx).inner_text()).strip() or f"#{idx}"
                tabs.nth(idx).click(timeout=8000)
            except Exception as e:
                print(f"  [WARN] Khong bam duoc tab #{idx}: {e}")
                continue
            moi = self._wait_new_capture(before)
            got = len(self._read_captured())
            trang_thai = "app da goi API" if moi else "KHONG thay app goi API"
            print(f"[INFO] Tab '{nhan}': {trang_thai} -> tong nghe duoc {got} tran")

        return self._read_captured()

    def close(self):
        for obj in (self._ctx, self._browser):
            try:
                obj and obj.close()
            except Exception:
                pass
        try:
            self._pw and self._pw.stop()
        except Exception:
            pass


TRANSPORT = None        # duoc gan trong open_transport()


def open_transport():
    """Mac dinh chi dung trinh duyet. Tang requests phai bat bang bien
    moi truong KHANDAI_USE_REQUESTS=1 (xem ghi chu o dau muc nay)."""
    # Probe dung DUNG dang truy van ma trang web that su goi (xem BAT CHUOC APP).
    # TUYET DOI khong dung 'status=live' o day: do la tham so duy nhat ta tung
    # dung ma app khong dung, va moi lan dung deu bi 403 tu IP GitHub.
    probe = {"page_size": PAGE_SIZE, "page": 1, "ordering": "smart",
             "start_time__date": vn_now().date().strftime("%Y-%m-%d")}

    if USE_REQUESTS:
        t = RequestsTransport()
        try:
            t.open()
            data = t.get_json(API_MATCHES, probe)
            if isinstance(data, dict) and "results" in data:
                print(f"[INFO] Transport: {t.name}")
                return t
            raise RuntimeError("API tra ve du lieu la")
        except Exception as e:
            print(f"[WARN] Goi API bang requests that bai: {e}")
            print("[WARN] Luu y: lan goi hong nay co the lam IP bi gan co.")
            t.close()

    t = BrowserTransport()
    try:
        t.open()
    except Exception as e:
        print(f"[ERROR] Khong mo duoc trinh duyet: {e}")
        try:
            t.close()
        except Exception:
            pass
        return None

    # KHONG probe, KHONG tu goi API khi chay bang trinh duyet.
    #
    # Ly do (log GitHub 16:58 ngay 21/09/2026):
    #   probe {page_size:18, page:1, ordering:smart, start_time__date:21/09} -> OK
    #   ngay sau do, truy van Y HET dang do cho 20/09, 21/09, 22/09 -> 403 het
    # Cung dang truy van, chi khac thu tu goi => khong phai tham so nao sai,
    # ma la BI GIOI HAN SO LAN GOI: vai request dau qua, sau do chan.
    # Moi request ta tu phat deu dot han muc va co the lam IP bi gan co,
    # trong khi che do nghe len khong ton request nao.
    t.direct_api_ok = False
    print(f"[INFO] Transport: {t.name} - CHE DO NGHE LEN "
          "(khong tu phat request nao toi /api/)")
    return t


def api_get(params: dict) -> list[dict]:
    """Goi /api/matches/ va tra ve toan bo results (tu lat trang neu con)."""
    out, url, first = [], API_MATCHES, True
    for _ in range(20):                      # chan vong lap vo han
        data = None
        for attempt in range(HTTP_RETRY):
            try:
                data = TRANSPORT.get_json(url, params if first else None)
                break
            except Exception as e:
                if attempt == HTTP_RETRY - 1:
                    print(f"  [ERROR] API {params or url} -> {e}")
                    return out
        first = False
        if not isinstance(data, dict):
            break
        out.extend(data.get("results") or [])
        url = data.get("next")
        if not url:
            break
    return out


def fetch_matches() -> list[dict]:
    """Gom tran theo ngay + tran dang live, gop lai theo slug."""
    raw, seen = [], set()

    def absorb(items, tag):
        added = 0
        for m in items:
            key = m.get("slug") or m.get("id")
            if key in seen:
                continue
            seen.add(key)
            raw.append(m)
            added += 1
        print(f"[INFO] {tag}: {len(items)} tran -> them {added} moi")

    # CHE DO NGHE LEN: khong tu goi API, de app goi roi doc lai ket qua.
    if getattr(TRANSPORT, "direct_api_ok", True) is False:
        absorb(TRANSPORT.capture_matches(DAYS_TO_CRAWL), "Nghe len tu trang web")
    else:
        # KHONG dung endpoint '?status=live' (xem ghi chu BAT CHUOC APP).
        # Tran dang live van lay duoc vi moi ban ghi deu co truong 'status',
        # con tran bat dau khuya hom qua ma gio van da thi nam o ngay hom qua.
        today = vn_now().date()
        for i in range(-1, DAYS_TO_CRAWL):
            d = (today + timedelta(days=i)).strftime("%Y-%m-%d")
            nhan = "hom qua" if i == -1 else ("hom nay" if i == 0 else f"+{i} ngay")
            absorb(api_get({"page_size": PAGE_SIZE, "page": 1, "ordering": "smart",
                            "start_time__date": d}), f"API ngay {d} ({nhan})")

    if DEBUG_DUMP:
        with open(DEBUG_DUMP_FILE, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
        print(f"[INFO] Da ghi {os.path.abspath(DEBUG_DUMP_FILE)}")

    return raw


# ──────────────────────────────────────────────
# PARSE 1 TRAN TU API
# ──────────────────────────────────────────────
def parse_match(m: dict) -> dict | None:
    slug   = m.get("slug") or ""
    status = str(m.get("status", "")).lower().strip()
    if status in FINISHED_STATUSES:
        return None

    match_id = str(m.get("api_football_id") or m.get("id") or generate_id(slug))
    start_dt = parse_start(m.get("start_time", ""))

    streams = []
    for c in (m.get("commentators") or []):
        url = (c.get("stream_url") or "").strip()
        if not url:
            url = (c.get("backup_stream_url") or "").strip()
        if not url:
            continue
        streams.append({
            "blv_name":   (c.get("name") or "").strip() or "BLV",
            "stream_url": url,
            "blv_live":   bool(c.get("is_live")),
            "avatar":     abs_url(c.get("avatar_url") or ""),
        })

    avatar = streams[0]["avatar"] if streams else ""
    if not avatar:
        for c in (m.get("commentators") or []):
            if c.get("avatar_url"):
                avatar = abs_url(c["avatar_url"])
                break

    return {
        "match_id":  match_id,
        "url":       f"{BASE_DOMAIN}/truc-tiep/{slug}",
        "home":      (m.get("home_team_name") or "").strip(),
        "away":      (m.get("away_team_name") or "").strip(),
        "logo_home": abs_url(m.get("home_team_logo") or ""),
        "logo_away": abs_url(m.get("away_team_logo") or ""),
        "avatar":    avatar,
        "league":    (m.get("tournament_name") or "").strip(),
        "sport":     (m.get("sport_slug") or "").strip(),
        "sport_name": (m.get("sport_name") or "").strip(),
        "start_dt":  start_dt,
        "time_str":  start_dt.strftime("%H:%M %d/%m") if start_dt else "",
        "is_live":   status in LIVE_STATUSES,
        "streams":   streams,
        "img_url":   "",
    }


# ──────────────────────────────────────────────
# TIEU DE
# ──────────────────────────────────────────────
def build_entries(m: dict) -> list[tuple[str, str]]:
    """Tra ve danh sach (stream_url, blv_name) de xuat ra playlist.

    CANH BAO QUAN TRONG: API tra 'stream_url' cho CA tran chua da
    (status='scheduled'), va do la kenh co dinh cua BLV. Vi du thuc te
    (21/09/2026): BLV KaKa dung chung URL .../phaohoa7/index.m3u8 cho
    ca tran dang live LAN tran 17:20 chua bat dau. Neu gan link cho tran
    chua da, nguoi xem bam vao se thay NHAM tran khac dang phat.
    => Chi gan link khi tran dang live VA BLV do dang len song.
    """
    if not m["streams"]:
        return [("", "")]

    if m["is_live"]:
        onair = [s for s in m["streams"] if s["blv_live"]]
        if not onair:                      # API chua kip cap nhat co is_live
            onair = m["streams"]
        return [(s["stream_url"], s["blv_name"]) for s in onair]

    # Tran chua da: van hien ten BLV trong tieu de, nhung KHONG gan link
    return [("", m["streams"][0]["blv_name"])]


def build_title(m: dict, blv_name: str = "") -> str:
    parts = []
    if m["time_str"]:
        parts.append(m["time_str"])
    if m["league"]:
        parts.append(m["league"])
    if m["home"] and m["away"]:
        parts.append(f"{m['home']} VS {m['away']}")
    elif m["home"]:
        parts.append(m["home"])
    if blv_name:
        parts.append(f"[{blv_name}]")
    return " ".join(parts)


# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────
def main():
    global TRANSPORT
    now      = vn_now()
    now_str  = now.strftime("%H:%M %d/%m/%Y")
    print(f"[INFO] Bat dau luc {now_str} (Gio VN) | host chay: "
          f"UTC{datetime.now().astimezone().utcoffset()}")

    TRANSPORT = open_transport()
    if TRANSPORT is None:
        print("[ERROR] Khong goi duoc API. Dung lai, KHONG ghi de file cu.")
        sys.exit(1)
    try:
        raw = fetch_matches()
    finally:
        TRANSPORT.close()

    if not raw:
        print("[ERROR] Khong lay duoc du lieu tu API. Dung lai, KHONG ghi de file cu.")
        sys.exit(1)

    all_matches = [x for x in (parse_match(m) for m in raw) if x]

    if SPORT_FILTER:
        before     = len(all_matches)
        match_data = [m for m in all_matches if m["sport"] == SPORT_FILTER]
        print(f"[INFO] Loc mon '{SPORT_FILTER}': bo {before - len(match_data)} tran, "
              f"con {len(match_data)}")
    else:
        match_data = all_matches

    match_data.sort(key=lambda m: m["start_dt"] or datetime.max.replace(tzinfo=VN_TZ))

    live_count     = sum(1 for m in match_data if m["is_live"])
    no_stream_live = sum(1 for m in match_data if m["is_live"] and not m["streams"])
    print(f"[INFO] {len(match_data)} tran: {live_count} live, "
          f"{len(match_data) - live_count} sap dien ra")
    for m in match_data:
        if m["is_live"]:
            tag = f"{len(m['streams'])} stream" if m["streams"] else "KHONG co stream"
            print(f"   🔴 {m['home']} vs {m['away']}: {tag}")
    if no_stream_live:
        print(f"[WARN] {no_stream_live} tran live khong co stream_url tu API")

    # Ghep anh 2 logo doi (API co san logo ca 2 doi)
    executor = ThreadPoolExecutor(max_workers=8)
    thumbs = list(executor.map(
        lambda m: (_build_and_save_thumb(m["logo_home"], m["logo_away"], m["match_id"])
                   if (m["logo_home"] and m["logo_away"]) else ""),
        match_data))
    executor.shutdown(wait=True)
    for m, img_url in zip(match_data, thumbs):
        m["img_url"] = img_url or m["avatar"] or m["logo_home"] or COVER_IMAGE

    # ── Xuat file ──
    json_output = {
        "name": f"{SITE_NAME} ({now_str})",
        "image": {"url": COVER_IMAGE},
        "groups": [
            {"id": "live",     "name": "🔴 Live",        "channels": []},
            {"id": "upcoming", "name": "🗓 Sắp diễn ra", "channels": []}
        ]
    }
    m3u_content = f"#EXTM3U\n#PLAYLIST: {SITE_NAME} ({now_str})\n"
    vlc_content = f"#EXTM3U\n#PLAYLIST: {SITE_NAME} ({now_str})\n"

    def make_entry(m, stream_url, blv_name):
        title    = build_title(m, blv_name)
        entry_id = generate_id(m["url"] + blv_name)
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
        entries = build_entries(m)
        for stream_url, blv in entries:
            cj, ml, vl = make_entry(m, stream_url, blv)
            json_output["groups"][group_idx]["channels"].append(cj)
            m3u_content += ml
            vlc_content += vl
            total_entries += 1

    with open(f"{OUT_PREFIX}.json", "w", encoding="utf-8") as f:
        json.dump(json_output, f, ensure_ascii=False, indent=4)
    with open(f"{OUT_PREFIX}_iptv.txt", "w", encoding="utf-8") as f:
        f.write(m3u_content)
    with open(f"{OUT_PREFIX}_vlc.txt", "w", encoding="utf-8") as f:
        f.write(vlc_content)

    with_stream = sum(1 for g in json_output["groups"] for ch in g["channels"]
                      if ch["sources"][0]["contents"][0]["streams"][0]["stream_links"][0]["url"])
    print(f"\n✅ Hoan thanh luc: {now_str} (Gio VN)")
    print(f"   🔴 Live: {live_count} tran  |  🗓 Sắp diễn ra: "
          f"{len(match_data) - live_count} tran")
    print(f"   📺 Tong entries: {total_entries} (co link stream: {with_stream})")
    print(f"   📄 Da xuat: {OUT_PREFIX}.json | {OUT_PREFIX}_iptv.txt | {OUT_PREFIX}_vlc.txt")


if __name__ == "__main__":
    main()
