import json
import asyncio
import re
import hashlib
import base64
import requests
from urllib.parse import urlparse, parse_qs, unquote
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import BytesIO
from PIL import Image
from playwright.async_api import async_playwright

TARGET_URL  = "https://live19.chuoichientv.com/lich-thi-dau"
BASE_DOMAIN = "https://live19.chuoichientv.com"
COVER_IMAGE = "https://live19.chuoichientv.com/_nuxt/img/09caa87.png"

# ──────────────────────────────────────────────
# ID
# ──────────────────────────────────────────────
def generate_id(text):
    return hashlib.md5(text.encode()).hexdigest()[:12]

# ──────────────────────────────────────────────
# ẢNH: Ghép 2 logo thành 1 ảnh base64
# ──────────────────────────────────────────────
def _fetch_logo(url):
    try:
        proxy = f"https://images.weserv.nl/?url={url}&w=100&h=100&fit=contain&output=png&bg=ececec"
        res = requests.get(proxy, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        return Image.open(BytesIO(res.content)).convert("RGBA")
    except:
        return None

def _build_combined_image(logo_a_url, logo_b_url):
    try:
        canvas = Image.new("RGBA", (220, 100), (236, 236, 236, 255))
        img_a = _fetch_logo(logo_a_url) if logo_a_url else None
        img_b = _fetch_logo(logo_b_url) if logo_b_url else None
        if img_a:
            canvas.paste(img_a, (0, 0), img_a)
        if img_b:
            canvas.paste(img_b, (110, 0), img_b)
        buf = BytesIO()
        canvas.save(buf, format="PNG", optimize=True)
        return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"
    except:
        if logo_a_url:
            return f"https://images.weserv.nl/?url={logo_a_url}&w=100&h=100&fit=contain&output=png"
        return ""

async def make_combined_image_async(logo_a_url, logo_b_url, executor):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, _build_combined_image, logo_a_url, logo_b_url)

# ──────────────────────────────────────────────
# TIMEZONE
# ──────────────────────────────────────────────
def detect_time_offset():
    local_now = datetime.now()
    utc_now   = datetime.now(timezone.utc).replace(tzinfo=None)
    diff_hours = round((local_now - utc_now).total_seconds() / 3600)
    needed_offset = 7 - diff_hours
    print(f"[INFO] Local timezone: UTC+{diff_hours} -> Cong {needed_offset}h vao gio tran")
    return needed_offset

def iso_to_vn_time(iso_str: str) -> str:
    """
    Chuyen ISO 8601 UTC -> gio Viet Nam (UTC+7), dang 'HH:MM DD/MM'.
    Luon cong dung 7h vi API tra ve UTC chuan, khong phu thuoc timezone may chay.
    """
    try:
        dt = datetime.strptime(iso_str, "%Y-%m-%dT%H:%M:%SZ")
        dt = dt + timedelta(hours=7)   # UTC -> VN, co dinh, khong dung detect_time_offset
        return dt.strftime("%H:%M %d/%m")
    except:
        return ""

# ──────────────────────────────────────────────
# TIEU DE
# ──────────────────────────────────────────────
def build_title(time_str: str, home: str, away: str, blv_and_quality: str = "") -> str:
    """
    "HH:MM DD/MM Doi A VS Doi B [BLV - HD1]"
    blv_and_quality: e.g. "Chuoi Vang - HD1"  hoac "" neu upcoming
    """
    parts = []
    if time_str:
        parts.append(time_str)
    if home and away:
        parts.append(f"{home} VS {away}")
    elif home:
        parts.append(home)
    if blv_and_quality:
        parts.append(f"[{blv_and_quality}]")
    return " ".join(parts)

# ──────────────────────────────────────────────
# PARSE API /v2/livestreams/match/{id}
# ──────────────────────────────────────────────
def parse_livestreams_api(data: dict) -> list[dict]:
    """
    Cau truc thuc te:
      branchStreamUrls[].username / .name / .streams[]{label, url}
    Chi lay .m3u8 (HLS), bo .flv.
    """
    results = []
    if not data or not isinstance(data, dict):
        return results
    blv_list = data.get("branchStreamUrls") or data.get("branchStreamUrlsBonglau") or []
    for blv in blv_list:
        if not isinstance(blv, dict):
            continue
        blv_id   = blv.get("username") or blv.get("id") or ""
        blv_name = blv.get("name")     or blv_id
        hls = [s for s in blv.get("streams", [])
               if isinstance(s, dict) and s.get("url", "").endswith(".m3u8")]
        hd_url  = next((s["url"] for s in hls if s.get("label","").upper().startswith("HD")),  "")
        fhd_url = next((s["url"] for s in hls if s.get("label","").upper().startswith("FHD")), "")
        if hd_url or fhd_url:
            results.append({"blv_id": blv_id, "blv_name": blv_name,
                            "stream_hd": hd_url, "stream_fhd": fhd_url})
    return results

def parse_match_info(data: dict) -> dict:
    """Trich xuat ten doi, logo, gio, thumbnail tu /v2/livestreams/match/{id}."""
    info = {"home": "", "away": "", "logo_home": "", "logo_away": "",
            "start_time_iso": "", "thumbnail": ""}
    if not data or not isinstance(data, dict):
        return info
    match = data.get("match", {})
    teams = match.get("teams", {})
    info["home"]           = teams.get("home", {}).get("name", "")
    info["away"]           = teams.get("away", {}).get("name", "")
    info["logo_home"]      = teams.get("home", {}).get("logo", "")
    info["logo_away"]      = teams.get("away", {}).get("logo", "")
    info["start_time_iso"] = data.get("startTime") or match.get("matchTime", "")
    info["thumbnail"]      = data.get("thumbnail", "")
    return info

def parse_match_info_external(data: dict) -> dict:
    """
    Trich xuat tu /v2/matches/external/{id}.
    Cau truc: {"match": {"teams": {...}, "matchTime": "..."}, "thumbnail": ...}
    hoac truc tiep {"teams": {...}, "matchTime": "..."}
    """
    info = {"home": "", "away": "", "logo_home": "", "logo_away": "",
            "start_time_iso": "", "thumbnail": ""}
    if not data or not isinstance(data, dict):
        return info
    # Thu nhieu cap cau truc
    match = data.get("match") or data.get("data") or data
    if not isinstance(match, dict):
        return info
    teams = match.get("teams", {})
    info["home"]           = teams.get("home", {}).get("name", "")
    info["away"]           = teams.get("away", {}).get("name", "")
    info["logo_home"]      = teams.get("home", {}).get("logo", "")
    info["logo_away"]      = teams.get("away", {}).get("logo", "")
    info["start_time_iso"] = (data.get("startTime") or data.get("matchTime")
                              or match.get("matchTime") or match.get("startTime", ""))
    info["thumbnail"]      = data.get("thumbnail") or match.get("thumbnail", "")
    return info

# ──────────────────────────────────────────────
# HELPER: parse streamUrl tu embed URL (fallback)
# ──────────────────────────────────────────────
def extract_m3u8_from_embed(url: str) -> tuple:
    if not url:
        return "", ""
    if url.endswith(".m3u8") or (".m3u8?" in url and "embed" not in url):
        return "", url
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        blv_id     = params.get("blvID", [""])[0]
        stream_raw = params.get("streamUrl", [""])[0]
        if stream_raw:
            decoded = unquote(stream_raw)
            if ".m3u8" in decoded:
                return blv_id, decoded
    except Exception:
        pass
    m = re.search(r'https?://[^\s"\'&]+\.m3u8(?:[^\s"\'&]*)?', url)
    if m:
        return "", unquote(m.group(0))
    return "", ""

# ──────────────────────────────────────────────
# LAY TAT CA STREAMS + THONG TIN TRAN (1 page)
# ──────────────────────────────────────────────
async def fetch_match_page(page, item_url: str, match_id: str) -> dict:
    """
    Intercept 2 API:
      /v2/livestreams/match/{id}  -> streams + match_info (tran live)
      /v2/matches/external/{id}   -> match_info (tran upcoming, khong co streams)
    Luon tra match_info du co stream hay khong.
    """
    livestream_bodies = []   # /v2/livestreams/match/{id}
    external_bodies   = []   # /v2/matches/external/{id}
    embed_urls        = []   # fallback embed URL

    async def on_response(res):
        url = res.url
        if f"/v2/livestreams/match/{match_id}" in url:
            try:
                livestream_bodies.append(await res.json())
            except Exception:
                try:
                    livestream_bodies.append(json.loads(await res.text()))
                except Exception:
                    pass
        elif f"/v2/matches/external/{match_id}" in url:
            try:
                external_bodies.append(await res.json())
            except Exception:
                try:
                    external_bodies.append(json.loads(await res.text()))
                except Exception:
                    pass
        elif "chuoichien.tv/embed" in url and "streamUrl" in url:
            embed_urls.append(url)

    page.on("response", on_response)
    try:
        await page.goto(item_url, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(5)

        streams    = []
        match_info = {}

        # --- Lay match_info: uu tien livestream API, fallback external API ---
        if livestream_bodies:
            body       = livestream_bodies[0]
            streams    = parse_livestreams_api(body)
            match_info = parse_match_info(body)
        elif external_bodies:
            # /v2/matches/external tra ve cau truc khac: {match: {teams, matchTime}}
            body       = external_bodies[0]
            match_info = parse_match_info_external(body)

        # In ket qua streams
        if streams:
            print(f"     [API-live] {len(streams)} BLV: "
                  + ", ".join(f"{s['blv_id']}(HD{'v' if s['stream_hd'] else 'x'}"
                              f"/FHD{'v' if s['stream_fhd'] else 'x'})"
                              for s in streams))
        elif external_bodies:
            print(f"     [API-ext] match_info: {match_info.get('home','?')} vs {match_info.get('away','?')}")

        # Neu co match_info (bat ky tu nguon nao) -> return ngay
        if match_info.get("home"):
            return {"streams": streams, "match_info": match_info}

        # --- Fallback: embed URL ---
        for eurl in embed_urls:
            blv_id, m3u8 = extract_m3u8_from_embed(eurl)
            if m3u8:
                print(f"     [Embed] BLV={blv_id} stream={m3u8[:60]}")
                return {"streams": [{"blv_id": blv_id, "blv_name": blv_id,
                                     "stream_hd": m3u8, "stream_fhd": ""}],
                        "match_info": match_info}

        # --- Fallback: iframe DOM ---
        iframes = await page.query_selector_all("iframe[src], iframe[data-src]")
        for iframe in iframes:
            src = (await iframe.get_attribute("src")
                   or await iframe.get_attribute("data-src") or "")
            blv_id, m3u8 = extract_m3u8_from_embed(src)
            if m3u8:
                print(f"     [DOM iframe] BLV={blv_id} stream={m3u8[:60]}")
                return {"streams": [{"blv_id": blv_id, "blv_name": blv_id,
                                     "stream_hd": m3u8, "stream_fhd": ""}],
                        "match_info": match_info}

        print(f"     [WARN] Khong tim duoc stream hoac match_info")
        return {"streams": streams, "match_info": match_info}

    except Exception as e:
        print(f"  [WARN] fetch_match_page loi: {e}")
        return {"streams": [], "match_info": {}}
    finally:
        page.remove_listener("response", on_response)

# ──────────────────────────────────────────────
# LAY DANH SACH TRAN TU TRANG LICH THI DAU
# ──────────────────────────────────────────────
MATCH_LINK_SELECTORS = [
    "a[href*='/live/']",
    "a[href*='/xem-truc-tiep/']",
    "a[href*='/truc-tiep/']",
    "a[href*='/match/']",
    "a[href*='/tran-dau/']",
]
SKIP_HREFS = ["#", "javascript", "mailto", "/login", "/register",
              "/home", "/about", "/contact", "/tin-tuc", "/news",
              "/lich-thi-dau", "/ket-qua", "/highlights"]

# Dung look-around de tranh match 'Live' ben trong ten doi (Liverpool, Oliverpool...)
# (?<![A-Za-z])Live(?![a-z]) : 'Live' khong bi bao quanh boi chu cai
LIVE_PATTERNS = re.compile(r'(?<![A-Za-z])Live(?![a-z])|LIVE|●')

async def find_match_links(page) -> list[dict]:
    matches   = []
    seen_urls = set()
    for sel in MATCH_LINK_SELECTORS:
        els = await page.query_selector_all(sel)
        for el in els:
            try:
                href = await el.get_attribute("href") or ""
                if not href:
                    continue
                # Chỉ skip các href KHÔNG phải trang trận đấu
                # (kiểm tra chính xác bằng startswith thay vì 'in')
                if any(href == s or href.startswith(s + "?")
                       for s in SKIP_HREFS):
                    continue
                # Bỏ các link ngoài domain không liên quan
                if href.startswith("http") and BASE_DOMAIN not in href:
                    continue
                full_url = (BASE_DOMAIN + href) if href.startswith("/") else href
                if full_url in seen_urls:
                    continue
                seen_urls.add(full_url)

                raw_text = (await el.text_content() or "").strip()
                is_live  = bool(LIVE_PATTERNS.search(raw_text))

                # match_id từ URL /live/{id}/slug
                mid_m    = re.search(r'/live/(\d+)/', href)
                match_id = mid_m.group(1) if mid_m else ""

                # Bỏ qua nếu không có match_id (không phải trang trận)
                if not match_id:
                    continue

                matches.append({"url": full_url, "is_live": is_live,
                                 "match_id": match_id})
            except Exception as ex:
                print(f"  [WARN] find_match_links: {ex}")
                continue

    if matches:
        live_n = sum(1 for m in matches if m["is_live"])
        print(f"[INFO] Selector 'a[href*/live/]' -> {len(matches)} tran"
              f" ({live_n} live, {len(matches)-live_n} sap dien ra)")
    else:
        print("[WARN] Khong tim duoc tran nao! Thu selector khac...")
        # Fallback: lay tat ca <a href> roi loc bang match_id
        all_els = await page.query_selector_all("a[href]")
        for el in all_els:
            try:
                href = await el.get_attribute("href") or ""
                mid_m = re.search(r'/live/(\d+)/', href)
                if not mid_m:
                    continue
                full_url = (BASE_DOMAIN + href) if href.startswith("/") else href
                if full_url in seen_urls:
                    continue
                seen_urls.add(full_url)
                raw_text = (await el.text_content() or "").strip()
                is_live  = bool(LIVE_PATTERNS.search(raw_text))
                matches.append({"url": full_url, "is_live": is_live,
                                 "match_id": mid_m.group(1)})
            except Exception:
                continue
        print(f"[INFO] Fallback -> {len(matches)} tran")

    return matches

# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────
async def main():
    now_utc     = datetime.now(timezone.utc)
    vn_time     = now_utc + timedelta(hours=7)
    now_str     = vn_time.strftime("%H:%M %d/%m/%Y")
    time_offset = detect_time_offset()
    executor    = ThreadPoolExecutor(max_workers=8)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        try:
            # Buoc 1: Load trang lich thi dau
            # Dung networkidle cho Vue SPA hydrate xong moi co DOM
            print(f"[INFO] Dang tai: {TARGET_URL}")
            try:
                await page.goto(TARGET_URL, wait_until="networkidle", timeout=30000)
            except Exception:
                # Fallback neu networkidle timeout (mang cham)
                try:
                    await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    pass
                await asyncio.sleep(5)

            # Scroll xuong de lazy-load, sau do scroll len lai cho chac
            for _ in range(5):
                await page.mouse.wheel(0, 2000)
                await asyncio.sleep(1.5)
            await page.evaluate("window.scrollTo(0, 0)")
            await asyncio.sleep(2)

            # Debug: in so the <a> tim thay
            _debug_els = await page.query_selector_all("a[href*='/live/']")
            print(f"[INFO] Tim thay {len(_debug_els)} the <a href*/live/> trong DOM")

            # Buoc 2: Lay danh sach link tran
            # Neu lan dau tra ve 0, cho them va thu lai toi da 3 lan
            match_links = []
            for attempt in range(1, 4):
                match_links = await find_match_links(page)
                if match_links:
                    break
                print(f"  [WARN] Lan thu {attempt}: 0 tran, cho them 3s...")
                await asyncio.sleep(3)
                # Scroll them de kich thich Vue render
                await page.mouse.wheel(0, 3000)
                await asyncio.sleep(1)
                await page.evaluate("window.scrollTo(0, 0)")
                await asyncio.sleep(1)

            live_links     = [m for m in match_links if m["is_live"]]
            upcoming_links = [m for m in match_links if not m["is_live"]]
            print(f"[INFO] Tong {len(match_links)} tran"
                  f" ({len(live_links)} live, {len(upcoming_links)} sap dien ra)")

            # Buoc 3: Crawl tat ca tran song song (LIVE + Upcoming)
            # Mo toi da MAX_CONCURRENT tab cung luc thay vi tung tab mot
            MAX_CONCURRENT = 4   # tang len neu may manh, giam xuong neu bi rate-limit

            async def crawl_one(item: dict, is_live: bool) -> dict:
                stream_page = await context.new_page()
                try:
                    result = await fetch_match_page(
                        stream_page, item["url"], item["match_id"]
                    )
                    if is_live:
                        return {**item, **result}
                    else:
                        return {**item,
                                "streams":    [],
                                "match_info": result.get("match_info", {})}
                finally:
                    await stream_page.close()

            async def crawl_batch(items: list[dict], is_live: bool,
                                  label: str) -> list[dict]:
                """Chay song song theo lo MAX_CONCURRENT tab."""
                results = []
                total = len(items)
                print(f"[INFO] {label}: {total} tran (song song {MAX_CONCURRENT} tab)...")
                for i in range(0, total, MAX_CONCURRENT):
                    batch = items[i : i + MAX_CONCURRENT]
                    urls  = " | ".join(b["url"].split("/")[-1] for b in batch)
                    print(f"  -> Lo {i//MAX_CONCURRENT + 1}: {urls}")
                    batch_results = await asyncio.gather(
                        *[crawl_one(item, is_live) for item in batch]
                    )
                    results.extend(batch_results)
                return results

            live_results     = await crawl_batch(live_links,     True,  "LIVE")
            upcoming_results = await crawl_batch(upcoming_links, False, "Sắp diễn ra")
            # Giu nguyen thu tu: live truoc, upcoming sau
            match_data = live_results + upcoming_results

            # Buoc 4: Ghep anh song song
            image_tasks = []
            for ch in match_data:
                mi = ch.get("match_info", {})
                image_tasks.append(
                    make_combined_image_async(
                        mi.get("logo_home", ""),
                        mi.get("logo_away", ""),
                        executor
                    )
                )
            image_results = await asyncio.gather(*image_tasks)
            for ch, img in zip(match_data, image_results):
                ch["combined_img"] = img
            executor.shutdown(wait=False)

            # Buoc 5: Xuat file
            json_output = {
                "name": f"Chuoi Chien TV ({now_str})",
                "image": {
                    "url": COVER_IMAGE,
                },
                "groups": [
                    {"id": "live",     "name": "🔴 Live",        "channels": []},
                    {"id": "upcoming", "name": "🗓 Sắp diễn ra", "channels": []}
                ]
            }
            m3u_content = f"#EXTM3U\n#PLAYLIST: Chuoi Chien TV ({now_str})\n"
            vlc_content = f"#EXTM3U\n#PLAYLIST: Chuoi Chien TV ({now_str})\n"

            def make_entry(ch: dict, stream_url: str, blv_name: str,
                           quality_label: str, img_url: str):
                mi       = ch.get("match_info", {})
                home     = mi.get("home", "")
                away     = mi.get("away", "")
                time_str = iso_to_vn_time(mi.get("start_time_iso", ""))

                blv_qual = f"{blv_name} - {quality_label}" if blv_name else ""
                title    = build_title(time_str, home, away, blv_qual)

                entry_id  = generate_id(ch["url"] + blv_qual)
                group     = "LIVE" if ch["is_live"] else "UPCOMING"
                stream    = stream_url or "http://0.0.0.0/not-live"
                referer   = ("https://live.chuoichien.tv/"
                             if stream_url and "chuoichien" not in stream_url
                             else ch["url"])
                # Anh channel: dung anh ghep 2 logo (combined_img), fallback COVER_IMAGE
                channel_img = img_url or COVER_IMAGE

                ch_json = {
                    "id":      f"ch-{entry_id}",
                    "name":    f"⚽ {title}",
                    "type":    "single",
                    "display": "thumbnail-only",
                    "image": {
                        "url":              channel_img,
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
                # VLC title: bo cac ky tu [, ], - vi VLC khong hien thi duoc
                vlc_title = title.replace("[", " ").replace("]", " ").replace(" - ", " ")
                # Don dep khoang trang thua
                import re as _re
                vlc_title = _re.sub(r' {2,}', ' ', vlc_title).strip()

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
            for ch in match_data:
                img_url   = ch.get("combined_img", "")
                group_idx = 0 if ch["is_live"] else 1

                if ch["is_live"] and ch.get("streams"):
                    for s in ch["streams"]:
                        blv = s["blv_name"] or s["blv_id"]
                        # Neu co FHD1 thi chi xuat FHD1, bo HD1
                        # Neu chi co HD1 thi xuat HD1
                        if s["stream_fhd"]:
                            pairs = [("FHD1", s["stream_fhd"])]
                        else:
                            pairs = [("HD1",  s["stream_hd"])] if s["stream_hd"] else []
                        for qual, url in pairs:
                            cj, ml, vl = make_entry(ch, url, blv, qual, img_url)
                            json_output["groups"][group_idx]["channels"].append(cj)
                            m3u_content += ml
                            vlc_content += vl
                            total_entries += 1
                else:
                    cj, ml, vl = make_entry(ch, "", "", "", img_url)
                    json_output["groups"][group_idx]["channels"].append(cj)
                    m3u_content += ml
                    vlc_content += vl
                    total_entries += 1

            with open("chuoichien.json",     "w", encoding="utf-8") as f:
                json.dump(json_output, f, ensure_ascii=False, indent=4)
            with open("chuoichien_iptv.txt", "w", encoding="utf-8") as f:
                f.write(m3u_content)
            with open("chuoichien_vlc.txt",  "w", encoding="utf-8") as f:
                f.write(vlc_content)

            live_count     = sum(1 for ch in match_data if ch["is_live"])
            upcoming_count = sum(1 for ch in match_data if not ch["is_live"])
            print(f"\n✅ Hoan thanh luc: {now_str} (Gio VN)")
            print(f"   🔴 Live: {live_count} tran  |  🗓 Sắp diễn ra: {upcoming_count} tran")
            print(f"   📺 Tong entries (BLV x chat luong): {total_entries}")
            print(f"   📄 Da xuat: chuoichien.json | chuoichien_iptv.txt | chuoichien_vlc.txt")

        except Exception as e:
            print(f"[ERROR] {e}")
            raise
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
