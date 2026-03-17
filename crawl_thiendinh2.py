import json
import asyncio
import re
import hashlib
import base64
import requests
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import BytesIO
from PIL import Image
from playwright.async_api import async_playwright

TARGET_URL = "https://sv1.thiendinh.live/lich-thi-dau/bong-da?by=state&value=live"

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
# ẢNH: Ghép 2 logo thành 1 ảnh base64
# Dùng ThreadPoolExecutor để chạy song song,
# không block asyncio event loop khi crawl stream.
# ──────────────────────────────────────────────
def _fetch_logo(url):
    """Tải 1 logo về dạng PIL Image. Dùng wsrv.nl để chuẩn hóa kích thước."""
    try:
        proxy = f"https://images.weserv.nl/?url={url}&w=100&h=100&fit=contain&output=png&bg=ececec"
        res = requests.get(proxy, headers={'User-Agent': 'Mozilla/5.0'}, timeout=8)
        return Image.open(BytesIO(res.content)).convert("RGBA")
    except:
        return None

def _build_combined_image(logo_a_url, logo_b_url):
    """
    Ghép logo A (trái) và logo B (phải) thành 1 ảnh PNG 220x100,
    trả về chuỗi base64 data URI sẵn dùng làm "url" trong JSON.
    Hàm này chạy trong thread riêng nên không block event loop.
    """
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
        # Fallback: nếu lỗi thì trả URL proxy đơn của logo_a
        if logo_a_url:
            return f"https://images.weserv.nl/?url={logo_a_url}&w=100&h=100&fit=contain&output=png"
        return ""

async def make_combined_image_async(logo_a_url, logo_b_url, executor):
    """Wrapper async: chạy _build_combined_image trong thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, _build_combined_image, logo_a_url, logo_b_url)

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
# TIÊU ĐỀ: Làm sạch raw text → tên trận đấu
# ──────────────────────────────────────────────
def clean_title(text, time_offset=0, team_names_dom=None):
    """
    Làm sạch raw text → tiêu đề trận đấu chuẩn: "HH:MM DD/MM Đội A VS Đội B [BLV ...]"

    team_names_dom: list [team_a, team_b] lấy trực tiếp từ DOM (chính xác hơn).
                    Nếu không có (None hoặc rỗng), fallback về parse text.
    """
    # 1. Nhận diện Live — \b để không bắt "Live" trong "Liverpool"
    is_live_origin = bool(re.search(r'(?i)\bLive\b|●', text))

    # 2. Chuẩn hóa giờ/ngày dính nhau: "03:0019/03" → "03:00 19/03"
    text = re.sub(r'(\d{2}:\d{2})\s*(\d{2}/\d{2})', r'\1 \2', text)
    time_match = re.search(r'\d{2}:\d{2} \d{2}/\d{2}', text)
    raw_time_str = time_match.group(0) if time_match else ""
    time_str = adjust_time_str(raw_time_str, time_offset)

    # 3. Tách BLV (từ raw text gốc trước khi xóa)
    blv_match = re.search(r'(BLV\s+\S.*?)(?:\n|$)', text, flags=re.IGNORECASE)
    blv_str = f" {blv_match.group(1).strip()}" if blv_match else ""

    # ── Nhánh A: Dùng tên đội từ DOM (ưu tiên — chính xác nhất) ──
    if team_names_dom and len(team_names_dom) == 2:
        team_a, team_b = team_names_dom[0].strip(), team_names_dom[1].strip()
        final_teams = f"{team_a} VS {team_b}"
        return f"{time_str} {final_teams}{blv_str}".strip(), final_teams, is_live_origin

    # ── Nhánh B: Fallback — parse từ text ──
    # 4. Xóa tất cả rác: Live, null, phút đang chơi (+10', 45+2'...), icon, trạng thái
    clean = re.sub(
        r"(?i)\bLive\b"           # từ "Live" đứng độc lập
        r"|\bnull\b"              # chữ "null" rác
        r"|\+\d+['']?"           # phút live: +10' +45'
        r"|\d{1,3}['']"          # phút đơn: 10' 45'
        r"|H\d\s*[-–]\s*\d+"    # hiệp: H1-0, H2-1
        r"|\d+\s*[-–]\s*\d+"    # tỉ số: 2-1, 0-0
        r"|●|Sắp diễn ra|Sắp bắt đầu"
        r"|[''`]",               # dấu nháy lẻ rác
        ' ', text
    )

    # 5. Xóa tên giải đấu (dài trước)
    for league in sorted(LEAGUE_BLACKLIST, key=len, reverse=True):
        clean = re.sub(rf'(?i)\b{re.escape(league)}\b', ' ', clean)

    # 6. Xóa giờ/ngày và BLV
    clean = clean.replace(raw_time_str, "").replace(blv_str.strip(), "")

    # 7. Tách tên đội dính (CamelCase): "SportingCPBodo" → "Sporting CP VS Bodo"
    clean = re.sub(r'([a-z])([A-Z])', r'\1 VS \2', clean)
    clean = re.sub(r'(\d)([A-Z])', r'\1 VS \2', clean)

    # 8. Chuẩn hóa dấu phân cách VS còn sót
    clean = re.sub(r'\s+VS\s+', ' VS ', clean, flags=re.IGNORECASE)
    clean = re.sub(r'\s{2,}', ' ', clean).strip()

    # 9. Tách đội A / đội B qua "VS" nếu có, không thì dùng mid
    if re.search(r'\bVS\b', clean, re.IGNORECASE):
        parts = re.split(r'\bVS\b', clean, maxsplit=1, flags=re.IGNORECASE)
        team_a = parts[0].strip()
        team_b = parts[1].strip() if len(parts) > 1 else ""
    else:
        words = [w for w in clean.split() if w]
        mid = len(words) // 2
        team_a = " ".join(words[:mid])
        team_b = " ".join(words[mid:])

    final_teams = f"{team_a} VS {team_b}" if team_b else team_a
    return f"{time_str} {final_teams}{blv_str}".strip(), final_teams, is_live_origin

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
# MAIN
# ──────────────────────────────────────────────
async def main():
    now_utc = datetime.now(timezone.utc)
    vn_time = now_utc + timedelta(hours=7)
    now_str = vn_time.strftime("%H:%M %d/%m/%Y")
    time_offset = detect_time_offset()

    # ThreadPoolExecutor cho việc tải & ghép ảnh song song
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

            elements = await page.query_selector_all("a[href*='/xem-truc-tiep/']")
            match_data = []

            for el in elements:
                url = await el.get_attribute("href")
                full_url = "https://sv1.thiendinh.live" + url if url.startswith('/') else url
                raw_text = (await el.text_content()).strip()

                # Lấy tên 2 đội trực tiếp từ DOM để tránh lỗi tách đội bằng mid
                # Tên đội thường nằm trong thẻ .team-name, span, hoặc p trong element
                team_nodes = await el.query_selector_all(
                    ".team-name, .name, [class*='team'] span, [class*='name']"
                )
                team_names_raw = []
                for node in team_nodes:
                    t = (await node.text_content()).strip()
                    if t and len(t) > 1:
                        team_names_raw.append(t)
                # Lọc trùng, giữ tối đa 2 tên đội
                seen = []
                for t in team_names_raw:
                    if t not in seen:
                        seen.append(t)
                team_names_dom = seen[:2]  # [team_a, team_b] nếu có

                full_title, teams_only, is_live = clean_title(
                    raw_text,
                    time_offset=time_offset,
                    team_names_dom=team_names_dom
                )

                imgs = await el.query_selector_all("img")
                logos = [await img.get_attribute("data-src") or await img.get_attribute("src") for img in imgs]
                logos = [l for l in logos if l and "http" in l and "30aaqin.png" not in l]

                match_data.append({
                    "title":        full_title,
                    "url":          full_url,
                    "logo_a":       logos[0] if len(logos) >= 1 else "",
                    "logo_b":       logos[1] if len(logos) >= 2 else "",
                    "combined_img": "",   # điền sau
                    "is_live":      is_live,
                    "stream":       ""    # điền sau
                })

            # ── Bước 2: Khởi động tất cả image tasks NGAY (chạy song song trong threads) ──
            image_tasks = [
                make_combined_image_async(ch["logo_a"], ch["logo_b"], executor)
                for ch in match_data
            ]

            # ── Bước 3: Trong khi ảnh đang tải, crawl stream song song ──
            for item in match_data:
                if item['is_live']:
                    stream_page = await context.new_page()
                    try:
                        item['stream'] = await fetch_stream_url(stream_page, item['url'])
                    finally:
                        await stream_page.close()

            # ── Bước 4: Thu kết quả ảnh (đã chạy song song lúc crawl stream) ──
            image_results = await asyncio.gather(*image_tasks)
            for ch, img in zip(match_data, image_results):
                ch['combined_img'] = img

            executor.shutdown(wait=False)

            # ── Bước 5: Xuất file ──
            json_output = {
                "name": f"Thiên Đỉnh TV ({now_str})",
                "groups": [
                    {"id": "live",     "name": "🔴 Live",         "channels": []},
                    {"id": "upcoming", "name": "🗓 Sắp diễn ra", "channels": []}
                ]
            }
            m3u_content = f"#EXTM3U\n#PLAYLIST: Thiên Đỉnh TV ({now_str})\n"
            vlc_content = f"#EXTM3U\n#PLAYLIST: Thiên Đỉnh TV ({now_str})\n"

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
                        "url":              img_url,   # 1 URL duy nhất chứa cả 2 logo ghép
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

            with open("thiendinh.json",     "w", encoding="utf-8") as f:
                json.dump(json_output, f, ensure_ascii=False, indent=4)
            with open("thiendinh_iptv.txt", "w", encoding="utf-8") as f:
                f.write(m3u_content)
            with open("thiendinh_vlc.txt",  "w", encoding="utf-8") as f:
                f.write(vlc_content)

            live_count     = sum(1 for ch in match_data if ch['is_live'])
            upcoming_count = sum(1 for ch in match_data if not ch['is_live'])
            print(f"✅ Hoàn thành lúc: {now_str} (Giờ VN)")
            print(f"   🔴 Live: {live_count} trận  |  🗓 Sắp diễn ra: {upcoming_count} trận")

        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
