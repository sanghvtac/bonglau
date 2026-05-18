import json
import asyncio
import re
import hashlib
import os
import requests
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import BytesIO
from PIL import Image
from playwright.async_api import async_playwright

TARGET_URL    = "https://sv2.hoiquan3.live/lich-thi-dau/bong-da"
BASE_DOMAIN   = "https://sv2.hoiquan3.live"
COVER_IMAGE   = "https://scontent.fhan14-4.fna.fbcdn.net/v/t39.30808-6/588617536_122151436196909707_6590699633917049497_n.jpg?_nc_cat=102&ccb=1-7&_nc_sid=1d70fc&_nc_eui2=AeFwmaoCNoFxNWW23s5_TIyKLbLYW9bIGWgtsthb1sgZaJ-KSALwzqa0JahbQepJMxhb8q9WkBQn1XZ3y0dArDFV&_nc_ohc=yQhm66r6iBIQ7kNvwHVC1bs&_nc_oc=Adqhdcp_J9MgFsVQEUz_VvjQpoG8uxKTfEp6HWNTufexU0562FiSnzRRUlrJR2W4uWXTXeHRMtIsckNlndDcIVEs&_nc_zt=23&_nc_ht=scontent.fhan14-4.fna&_nc_gid=rjYA_QxTObuhFLABssEdSg&_nc_ss=7b2a8&oh=00_Af4ML_R3kIYQtimfXzhtH6Gu8Xh1Gyhb-CSq1EhPlOwlEA&oe=6A04DA1E"
GITHUB_REPO   = "sanghvtac/bonglau"
GITHUB_BRANCH = "main"
THUMBS_DIR    = "thumbs"

# Danh sách giải đấu cần xóa
LEAGUE_BLACKLIST = [
    "UEFA Champions League", "UEFA Youth League", "UEFA Europa League", "UEFA Conference League",
    "Champions League", "Youth League", "Europa League", "Conference League", "UEFA",
    "AFC Champions League", "AFC Cup", "Premier League", "Ngoại Hạng Anh", "La Liga", "Serie A",
    "Bundesliga", "Ligue 1", "V-League", "K League 1", "Asian Cup Women", "Cup", "Vòng loại", "Giao hữu"
]

def generate_id(text):
    return hashlib.md5(text.encode()).hexdigest()[:12]

# ──────────────────────────────────────────────
# ẢNH: Ghép 2 logo -> lưu file PNG -> trả URL
# ──────────────────────────────────────────────
def _fetch_logo(url):
    try:
        proxy = f"https://images.weserv.nl/?url={url}&w=100&h=100&fit=contain&output=png&bg=ececec"
        res = requests.get(proxy, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        return Image.open(BytesIO(res.content)).convert("RGBA")
    except:
        return None

def _build_and_save_thumb(logo_a_url, logo_b_url, match_id):
    """Ghép 2 logo -> lưu thumbs/<match_id>.png -> trả URL GitHub raw."""
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
    return f"https://raw.githubusercontent.com/{GITHUB_REPO}/refs/heads/{GITHUB_BRANCH}/{path}"

async def make_thumb_async(logo_a_url, logo_b_url, match_id, executor):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        executor, _build_and_save_thumb, logo_a_url, logo_b_url, match_id
    )

# ──────────────────────────────────────────────
# GIỜ: Tự động phát hiện timezone offset
# ──────────────────────────────────────────────
def detect_time_offset():
    """
    So sánh local time vs UTC để tự tính số giờ cần cộng vào giờ trận đấu.
    - GitHub Actions (UTC+0): cộng +7
    - Máy VN (UTC+7): cộng +0
    """
    local_now = datetime.now()
    utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
    diff_hours = round((local_now - utc_now).total_seconds() / 3600)
    needed_offset = 7 - diff_hours
    print(f"[INFO] Local timezone: UTC+{diff_hours} → Cộng {needed_offset}h vào giờ trận")
    return needed_offset

def adjust_time_str(time_str, offset_hours):
    """Cộng offset_hours vào time_str dạng 'HH:MM DD/MM'."""
    if not time_str or offset_hours == 0:
        return time_str
    try:
        dt = datetime.strptime(time_str.strip(), "%H:%M %d/%m")
        dt = dt.replace(year=datetime.now(timezone.utc).year)
        return (dt + timedelta(hours=offset_hours)).strftime("%H:%M %d/%m")
    except:
        return time_str

# ──────────────────────────────────────────────
# TIÊU ĐỀ: Build từ DOM (đã có sẵn time, teams, blv, is_live)
# ──────────────────────────────────────────────
def build_title(time_str, team_a, team_b, blv_str, time_offset=0):
    """
    Build tiêu đề chuẩn: "HH:MM DD/MM Đội A VS Đội B [BLV ...]"
    Tất cả thông tin lấy thẳng từ DOM, không cần regex parse text rác.
    """
    time_str = adjust_time_str(time_str, time_offset)
    final_teams = f"{team_a} VS {team_b}" if team_b else team_a
    blv_part = f" {blv_str}" if blv_str else ""
    full_title = f"{time_str} {final_teams}{blv_part}".strip()
    return full_title, final_teams

# ──────────────────────────────────────────────
# STREAM: Lấy URL m3u8 cho từng trận live
# ──────────────────────────────────────────────
async def fetch_stream_url(page, item_url):
    """
    Mở page mới riêng, lắng nghe response để bắt URL .m3u8.
    Luôn remove_listener sau khi xong để tránh chồng chất.
    """
    m3u8_list = []

    def on_response(res):
        if ".m3u8" in res.url:
            m3u8_list.append(res.url)

    page.on("response", on_response)
    try:
        await page.goto(item_url, wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(4)
        return max(m3u8_list, key=len) if m3u8_list else ""
    except:
        return ""
    finally:
        page.remove_listener("response", on_response)

# ──────────────────────────────────────────────
# PARSE 1 CARD: lấy time, teams, logos, blv, is_live trực tiếp từ DOM
# ──────────────────────────────────────────────
async def parse_match_card(el):
    """
    Trích xuất thông tin từ 1 thẻ <a> card trận đấu của hoiquan3.
    Trả về dict các trường đã làm sạch.
    """
    href = await el.get_attribute("href")
    full_url = BASE_DOMAIN + href if href and href.startswith("/") else href

    # 1. is_live: card live có div .animate-pulse (icon live đỏ) hoặc text "H1/H2 - XX'"
    pulse_node = await el.query_selector(".animate-pulse")
    is_live = pulse_node is not None
    if not is_live:
        # Fallback: kiểm tra text có "H1 - XX", "H2 - XX" (đang đá) hoặc "Live"/"TRỰC TIẾP"
        # Không dùng \b vì text_content nối liền các span (vd "...FCH2 - 80'")
        raw_text = (await el.text_content()) or ""
        is_live = bool(
            re.search(r"H[12]\s*-\s*\d+", raw_text)
            or re.search(r"(?i)\b(Live|TRỰC TIẾP)\b", raw_text)
        )

    # 2. Tên 2 đội: span có class chứa "truncate" và "font-medium"
    #    (loại trừ span.truncate của BLV để không nhầm)
    team_nodes = await el.query_selector_all("span.truncate.font-medium")
    if not team_nodes:
        team_nodes = await el.query_selector_all("span.truncate")
    team_names = []
    for node in team_nodes:
        t = ((await node.text_content()) or "").strip()
        # Loại tên BLV (thường có "BLV" ở đầu) và chuỗi quá ngắn
        if t and len(t) > 1 and not re.match(r"(?i)^\s*BLV\b", t):
            team_names.append(t)
    # Dedupe giữ thứ tự
    seen = []
    for t in team_names:
        if t not in seen:
            seen.append(t)
    team_a = seen[0] if len(seen) >= 1 else ""
    team_b = seen[1] if len(seen) >= 2 else ""

    # 3. Time + Date: ở hoiquan3 chia làm 2 span riêng nối liền nhau
    #    "14:30" + "09/05/2026" → text "14:3009/05/2026" → ghép "14:30 09/05"
    time_str = ""
    full_text = (await el.text_content()) or ""
    # Bắt combo "HH:MM" theo sau là "DD/MM" (có thể kèm /YYYY)
    m_combo = re.search(r"(\d{1,2}:\d{2})\s*(\d{1,2}/\d{1,2})(?:/\d{2,4})?", full_text)
    if m_combo:
        time_str = f"{m_combo.group(1)} {m_combo.group(2)}"
        # Pad 2 chữ số (2 → 02) cho khớp format adjust_time_str
        try:
            dt = datetime.strptime(time_str, "%H:%M %d/%m")
            time_str = dt.strftime("%H:%M %d/%m")
        except:
            pass
    else:
        # Fallback: chỉ có giờ, không có ngày
        m_time = re.search(r"(\d{1,2}:\d{2})", full_text)
        if m_time:
            time_str = m_time.group(1)

    # 4. BLV: span.truncate trong khối .bg-blue-500 có chứa avatar BLV
    blv_str = ""
    blv_node = await el.query_selector(".bg-blue-500 span.truncate, .bg-blue-500 span.font-medium")
    if blv_node:
        blv_text = ((await blv_node.text_content()) or "").strip()
        if blv_text:
            blv_str = blv_text if blv_text.upper().startswith("BLV") else f"BLV {blv_text}"

    # 5. Logo 2 đội: img có class "w-12 h-12" (kích thước cố định riêng cho team logo)
    team_logo_imgs = await el.query_selector_all("img.w-12.h-12")
    logos = []
    for img in team_logo_imgs:
        src = (await img.get_attribute("data-src")) or (await img.get_attribute("src")) or ""
        if src and src.startswith("http"):
            logos.append(src)
    # Fallback: nếu không có .w-12.h-12 thì lấy tất cả img và lọc
    if len(logos) < 2:
        all_imgs = await el.query_selector_all("img")
        for img in all_imgs:
            src = (await img.get_attribute("data-src")) or (await img.get_attribute("src")) or ""
            if (src and src.startswith("http")
                and "30aaqin.png" not in src       # icon "Bóng đá"
                and "image2url.com" not in src     # avatar BLV
                and "i.imgur.com" not in src       # logo giải đấu (UtyP0MT.jpeg...)
                and src not in logos):
                logos.append(src)
            if len(logos) >= 2:
                break

    return {
        "url":      full_url,
        "team_a":   team_a,
        "team_b":   team_b,
        "time_str": time_str,
        "blv_str":  blv_str,
        "logo_a":   logos[0] if len(logos) >= 1 else "",
        "logo_b":   logos[1] if len(logos) >= 2 else "",
        "is_live":  is_live,
    }

# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────
async def main():
    now_utc = datetime.now(timezone.utc)
    vn_time = now_utc + timedelta(hours=7)
    now_str = vn_time.strftime("%H:%M %d/%m/%Y")
    time_offset = detect_time_offset()
    executor = ThreadPoolExecutor(max_workers=8)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(user_agent="Mozilla/5.0")
        page = await context.new_page()

        try:
            # ── Bước 1: Lấy danh sách trận ──
            await page.goto(TARGET_URL, wait_until="domcontentloaded")
            for _ in range(3):
                await page.mouse.wheel(0, 2000)
                await asyncio.sleep(1)

            # Selector đúng cho hoiquan3: href bắt đầu bằng "/truc-tiep/"
            elements = await page.query_selector_all("a[href*='/truc-tiep/']")
            print(f"[INFO] Tìm được {len(elements)} thẻ card trận đấu")

            match_data = []
            for el in elements:
                info = await parse_match_card(el)
                if not info["url"] or not info["team_a"]:
                    continue   # bỏ card hỏng

                full_title, teams_only = build_title(
                    info["time_str"],
                    info["team_a"], info["team_b"],
                    info["blv_str"],
                    time_offset=time_offset
                )

                match_data.append({
                    "title":        full_title,
                    "url":          info["url"],
                    "logo_a":       info["logo_a"],
                    "logo_b":       info["logo_b"],
                    "combined_img": "",   # điền sau
                    "is_live":      info["is_live"],
                    "stream":       ""    # điền sau
                })

            # ── Bước 2: Khởi động tạo thumbnail song song (PIL -> thumbs/) ──
            thumb_tasks = [
                make_thumb_async(
                    ch["logo_a"], ch["logo_b"],
                    generate_id(ch["url"]),
                    executor
                )
                for ch in match_data
            ]

            # ── Bước 3: Crawl stream song song (MAX 4 tab cung luc) ──
            MAX_CONCURRENT = 4
            live_items = [ch for ch in match_data if ch['is_live']]

            async def fetch_one(item):
                stream_page = await context.new_page()
                try:
                    item['stream'] = await fetch_stream_url(stream_page, item['url'])
                finally:
                    await stream_page.close()

            for i in range(0, len(live_items), MAX_CONCURRENT):
                batch = live_items[i : i + MAX_CONCURRENT]
                await asyncio.gather(*[fetch_one(item) for item in batch])

            # ── Bước 4: Thu kết quả thumbnail ──
            thumb_results = await asyncio.gather(*thumb_tasks)
            for ch, img_url in zip(match_data, thumb_results):
                ch["combined_img"] = img_url or ch.get("logo_a", "")

            executor.shutdown(wait=False)

            # ── Bước 5: Xuất file ──
            json_output = {
                "name": f"Hội Quán TV ({now_str})",
                "image": {"url": COVER_IMAGE},
                "groups": [
                    {"id": "live",     "name": "🔴 Live",         "channels": []},
                    {"id": "upcoming", "name": "🗓 Sắp diễn ra", "channels": []}
                ]
            }
            m3u_content = f"#EXTM3U\n#PLAYLIST: Hội Quán TV ({now_str})\n"
            vlc_content = f"#EXTM3U\n#PLAYLIST: Hội Quán TV ({now_str})\n"

            for ch in match_data:
                match_id = generate_id(ch['url'])
                stream   = ch['stream'] if ch['stream'] else "http://0.0.0.0/not-live"
                group    = "LIVE" if ch['is_live'] else "UPCOMING"
                img_url  = ch['combined_img'] or ch['logo_a']

                # JSON cho app TV (SportTV, MonPlayer...)
                channel_json = {
                    "id":      f"ch-{match_id}",
                    "name":    f"⚽ {ch['title']}",
                    "type":    "single",
                    "display": "thumbnail-only",
                    "image": {
                        "url":              img_url,
                        "display":          "contain",
                        "padding":          1,
                        "background_color": "#ececec"
                    },
                    "sources": [{
                        "id": f"src-{match_id}",
                        "contents": [{
                            "id": f"ct-{match_id}",
                            "streams": [{
                                "stream_links": [{
                                    "url":  ch['stream'] if ch['stream'] else "",
                                    "type": "hls",
                                    "request_headers": [
                                        {"key": "Referer",    "value": ch['url']},
                                        {"key": "User-Agent", "value": "Mozilla/5.0"}
                                    ]
                                }]
                            }]
                        }]
                    }]
                }
                if ch['is_live']:
                    json_output["groups"][0]["channels"].append(channel_json)
                else:
                    json_output["groups"][1]["channels"].append(channel_json)

                # IPTV M3U (TiviMate, GSE Smart IPTV...) — không cần tvg-logo
                m3u_content += (
                    f'#EXTINF:-1 tvg-id="{match_id}" '
                    f'group-title="{group}", {ch["title"]}\n'
                    f'#EXTVLCOPT:http-referrer={ch["url"]}\n'
                    f'#EXTVLCOPT:http-user-agent=Mozilla/5.0\n'
                    f'{stream}\n'
                )

                # VLC M3U — không cần tvg-logo
                vlc_content += (
                    f'#EXTINF:-1 tvg-id="{match_id}" '
                    f'group-title="{group}", ⚽ {ch["title"]}\n'
                    f'#EXTVLCOPT:network-caching=1000\n'
                    f'#EXTVLCOPT:http-referrer={ch["url"]}\n'
                    f'#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36\n'
                    f'{stream}\n'
                )

            with open("hoiquan.json",     "w", encoding="utf-8") as f:
                json.dump(json_output, f, ensure_ascii=False, indent=4)
            with open("hoiquan_iptv.txt", "w", encoding="utf-8") as f:
                f.write(m3u_content)
            with open("hoiquan_vlc.txt",  "w", encoding="utf-8") as f:
                f.write(vlc_content)

            live_count     = sum(1 for ch in match_data if ch['is_live'])
            upcoming_count = sum(1 for ch in match_data if not ch['is_live'])
            print(f"✅ Hoàn thành lúc: {now_str} (Giờ VN)")
            print(f"   🔴 Live: {live_count} trận  |  🗓 Sắp diễn ra: {upcoming_count} trận")

        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(main())

# === Upload kết quả lên Cloudflare R2 ===
from r2_upload import upload_many

upload_many({
    'hoiquan.json': 'hoiquan.json',
    'hoiquan_iptv.txt': 'hoiquan_iptv.txt',
    'hoiquan_vlc.txt': 'hoiquan_vlc.txt',
})
