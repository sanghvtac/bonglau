import json
import asyncio
import re
import hashlib
from datetime import datetime, timedelta, timezone
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

def make_combined_image_url(logo_a_url, logo_b_url):
    """
    Thay vì tải + encode ảnh về base64 (nặng, chậm), dùng images.weserv.nl
    để trả về 1 URL proxy — client tự fetch khi cần hiển thị, script không
    tốn thêm RAM hay thời gian crawl.

    wsrv.nl hỗ trợ tham số 'af' (append filename) để ghép overlay,
    nhưng cách đơn giản nhất là trả về URL ảnh đôi qua canvas phía client.
    Ở đây ta trả về URL logo_a được proxy (chuẩn hóa kích thước), và lưu
    logo_b riêng — app JSON tự render 2 ảnh cạnh nhau.
    """
    def proxy(url):
        return f"https://images.weserv.nl/?url={url}&w=100&h=100&fit=contain&output=webp" if url else ""
    return proxy(logo_a_url), proxy(logo_b_url)

def adjust_time_str(time_str, offset_hours=7):
    """
    FIX BUG GIỜ GITHUB:
    Trang web trả về giờ theo UTC+0 khi chạy trên GitHub Actions (UTC).
    Hàm này cộng thêm offset_hours (mặc định +7) để chuyển sang giờ VN (UTC+7).
    Trên máy cá nhân (UTC+7), thời gian đã đúng nhưng hàm này vẫn an toàn vì
    chúng ta sẽ dùng TZ_OFFSET được tính tự động từ môi trường.
    """
    if not time_str:
        return ""
    try:
        # time_str có dạng "HH:MM DD/MM"
        time_str = time_str.strip()
        dt = datetime.strptime(time_str, "%H:%M %d/%m")
        # Dùng năm hiện tại (UTC) để tránh lỗi năm
        now_utc = datetime.now(timezone.utc)
        dt = dt.replace(year=now_utc.year)
        dt_adjusted = dt + timedelta(hours=offset_hours)
        return dt_adjusted.strftime("%H:%M %d/%m")
    except:
        return time_str  # Nếu parse lỗi, trả về nguyên bản

def clean_title(text, time_offset=0):
    """
    Làm sạch tiêu đề trận đấu từ raw text của trang web.
    time_offset: số giờ cần cộng thêm vào giờ trận đấu (0 = không đổi, 7 = cộng 7 tiếng)
    """
    # 1. Nhận diện Live (trước khi xóa chữ)
    is_live_origin = any(word in text.upper() for word in ["LIVE", "●"])

    # 2. Chuẩn hóa Ngày/Giờ: Xử lý dính chữ và đảm bảo format HH:MM DD/MM
    text = re.sub(r'(\d{2}:\d{2})\s*(\d{2}/\d{2})', r'\1 \2', text)
    time_match = re.search(r'\d{2}:\d{2}\s*\d{2}/\d{2}', text)
    raw_time_str = time_match.group(0).strip() if time_match else ""

    # FIX: Cộng offset giờ vào time_str lấy từ trang web
    time_str = adjust_time_str(raw_time_str, offset_hours=time_offset) if raw_time_str else ""

    # 3. Tách BLV
    blv_match = re.search(r'(BLV.*)', text, flags=re.IGNORECASE)
    blv_str = f" {blv_match.group(1).strip()}" if blv_match else ""

    # 4. Làm sạch tiêu đề (Xóa Live, icon, giải đấu)
    # Dùng \b (word boundary) để chỉ xóa từ "Live" đứng độc lập,
    # KHÔNG xóa "Live" bên trong tên đội như "Liverpool"
    clean = re.sub(r'(?i)\bLive\b|●|Sắp diễn ra|Sắp bắt đầu', ' ', text)

    # Xóa các giải đấu trong Blacklist (sắp xếp dài trước để tránh xóa nhầm chuỗi con)
    for league in sorted(LEAGUE_BLACKLIST, key=len, reverse=True):
        clean = re.sub(rf'(?i){re.escape(league)}', ' ', clean)

    # 5. Xử lý dính chữ đặc biệt cho Liverpool
    clean = re.sub(r'(Liverpool)([A-Z])', r'\1 VS \2', clean, flags=re.IGNORECASE)

    # 6. Xóa ngày giờ và BLV khỏi phần tên đội
    clean = clean.replace(raw_time_str, "").replace(blv_str.strip(), "")

    # 7. Xử lý CamelCase chung (tên đội dính nhau)
    clean = re.sub(r'([a-z])([A-Z])', r'\1 VS \2', clean)
    clean = re.sub(r'(\d)([A-Z])', r'\1 VS \2', clean)

    # 8. Xóa tỉ số, hiệp đấu, dấu gạch ngang rác
    clean = re.sub(r'(H\d\s*-\s*\d+\'?|\d-\d|-|VS)', ' ', clean, flags=re.IGNORECASE)

    # 9. Tách vế để lấy Đội A VS Đội B
    parts = [p.strip() for p in clean.split() if p.strip()]
    if len(parts) >= 2:
        mid = len(parts) // 2
        team_a = " ".join(parts[:mid])
        team_b = " ".join(parts[mid:])
        final_teams = f"{team_a} VS {team_b}"
    else:
        final_teams = " ".join(parts)

    return f"{time_str} {final_teams}{blv_str}".strip(), final_teams, is_live_origin

def detect_time_offset():
    """
    Tự động phát hiện offset cần cộng vào giờ của trang web.
    - Nếu chạy trên GitHub Actions (UTC+0): cần cộng +7
    - Nếu chạy trên máy tính VN (UTC+7): cộng +0
    Cách phát hiện: so sánh local time với UTC time.
    """
    local_now = datetime.now()
    utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
    diff_hours = round((local_now - utc_now).total_seconds() / 3600)
    # Trang web luôn trả về UTC+0 → ta cần bù thêm để ra giờ VN (UTC+7)
    # local là UTC+7 → diff_hours = 7 → offset cần thêm = 7 - 7 = 0 (đã đúng)
    # local là UTC+0 (GitHub) → diff_hours = 0 → offset cần thêm = 7 - 0 = 7 (cần cộng)
    needed_offset = 7 - diff_hours
    print(f"[INFO] Local timezone offset: UTC+{diff_hours} → Cần cộng {needed_offset} giờ vào giờ trang web")
    return needed_offset

async def fetch_stream_url(page, item_url):
    """
    FIX RACE CONDITION:
    Tạo page mới riêng cho mỗi trận để tránh listener chồng chất.
    Trả về URL m3u8 dài nhất tìm được, hoặc "" nếu không có.
    """
    m3u8_list = []

    def on_response(res):
        if ".m3u8" in res.url:
            m3u8_list.append(res.url)

    page.on("response", on_response)
    try:
        await page.goto(item_url, wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(4)
        result = max(m3u8_list, key=len) if m3u8_list else ""
    except:
        result = ""
    finally:
        # Luôn gỡ listener sau khi dùng xong để tránh chồng chất
        page.remove_listener("response", on_response)
    return result

async def main():
    # Tính giờ VN hiện tại (dùng UTC + 7)
    now_utc = datetime.now(timezone.utc)
    vn_time = now_utc + timedelta(hours=7)
    now_str = vn_time.strftime("%H:%M %d/%m/%Y")

    # Phát hiện offset giờ cần cộng vào giờ trận đấu từ trang web
    time_offset = detect_time_offset()

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(user_agent="Mozilla/5.0")
        page = await context.new_page()

        try:
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

                # Truyền time_offset vào clean_title để fix giờ
                full_title, teams_only, is_live = clean_title(raw_text, time_offset=time_offset)

                imgs = await el.query_selector_all("img")
                logos = [await img.get_attribute("data-src") or await img.get_attribute("src") for img in imgs]
                logos = [l for l in logos if l and "http" in l and "30aaqin.png" not in l]

                logo_a_px, logo_b_px = make_combined_image_url(
                    logos[0] if len(logos) >= 1 else "",
                    logos[1] if len(logos) >= 2 else ""
                )
                match_data.append({
                    "title": full_title,
                    "url": full_url,
                    "logo_a": logo_a_px,
                    "logo_b": logo_b_px,
                    "is_live": is_live,
                    "stream": ""
                })

            # FIX RACE CONDITION: Dùng page riêng cho mỗi trận live
            # và chạy tuần tự (tránh quá tải server)
            for item in match_data:
                if item['is_live']:
                    stream_page = await context.new_page()
                    try:
                        item['stream'] = await fetch_stream_url(stream_page, item['url'])
                    finally:
                        await stream_page.close()

            # === XUẤT FILE ===
            json_output = {
                "name": f"Thiên Đỉnh TV ({now_str})",
                "groups": [
                    {"id": "live", "name": "🔴 Live", "channels": []},
                    {"id": "upcoming", "name": "🗓 Sắp diễn ra", "channels": []}
                ]
            }
            m3u_content = f"#EXTM3U\n#PLAYLIST: Thiên Đỉnh TV ({now_str})\n"
            vlc_content = f"#EXTM3U\n#PLAYLIST: Thiên Đỉnh TV ({now_str})\n"

            for ch in match_data:
                match_id = generate_id(ch['url'])
                stream = ch['stream'] if ch['stream'] else "http://0.0.0.0/not-live"
                group = "LIVE" if ch['is_live'] else "UPCOMING"

                # --- JSON (cho app có hỗ trợ JSON playlist) ---
                channel_json = {
                    "id": f"ch-{match_id}",
                    "name": f"⚽ {ch['title']}",
                    "type": "single",
                    "display": "thumbnail-only",
                    "image": {
                        # Lưu cả 2 URL proxy — app tự render 2 logo cạnh nhau, không cần base64
                        "logo_a": ch['logo_a'],
                        "logo_b": ch['logo_b'],
                        "display": "contain",
                        "padding": 1,
                        "background_color": "#ececec"
                    },
                    "sources": [{
                        "id": f"src-{match_id}",
                        "contents": [{
                            "id": f"ct-{match_id}",
                            "streams": [{
                                "stream_links": [{
                                    "url": ch['stream'] if ch['stream'] else "",
                                    "type": "hls",
                                    "request_headers": [
                                        {"key": "Referer", "value": ch['url']},
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

                # --- IPTV M3U (cho các app IPTV như TiviMate, GSE) ---
                m3u_content += (
                    f'#EXTINF:-1 tvg-id="{match_id}" tvg-logo="{ch["logo_a"]}" '
                    f'group-title="{group}", {ch["title"]}\n'
                    f'#EXTVLCOPT:http-referrer={ch["url"]}\n'
                    f'#EXTVLCOPT:http-user-agent=Mozilla/5.0\n'
                    f'{stream}\n'
                )

                # --- VLC M3U (cho VLC Media Player) ---
                vlc_content += (
                    f'#EXTINF:-1 tvg-id="{match_id}" tvg-logo="{ch["logo_a"]}" '
                    f'group-title="{group}", ⚽ {ch["title"]}\n'
                    f'#EXTVLCOPT:network-caching=1000\n'
                    f'#EXTVLCOPT:http-referrer={ch["url"]}\n'
                    f'#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36\n'
                    f'{stream}\n'
                )

            with open("thiendinh.json", "w", encoding="utf-8") as f:
                json.dump(json_output, f, ensure_ascii=False, indent=4)
            with open("thiendinh_iptv.txt", "w", encoding="utf-8") as f:
                f.write(m3u_content)
            with open("thiendinh_vlc.txt", "w", encoding="utf-8") as f:
                f.write(vlc_content)

            live_count = sum(1 for ch in match_data if ch['is_live'])
            upcoming_count = sum(1 for ch in match_data if not ch['is_live'])
            print(f"✅ Hoàn thành lúc: {now_str} (Giờ VN)")
            print(f"   🔴 Live: {live_count} trận | 🗓 Sắp diễn ra: {upcoming_count} trận")

        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
