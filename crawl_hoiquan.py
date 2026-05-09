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

TARGET_URL  = "https://sv2.hoiquan3.live/lich-thi-dau/bong-da"
BASE_DOMAIN = "https://sv2.hoiquan3.live"

# Danh sách giải đấu cần xóa khỏi tiêu đề (sort dài trước để tránh xóa nhầm)
LEAGUE_BLACKLIST = [
    "UEFA Champions League", "UEFA Youth League", "UEFA Europa League", "UEFA Conference League",
    "AFC Champions League Elite", "AFC Champions League Two", "AFC Champions League",
    "Champions League", "Youth League", "Europa League", "Conference League",
    "Premier League", "Ngoại Hạng Anh", "La Liga", "Serie A", "Bundesliga", "Ligue 1",
    "V-League", "K League 1", "Asian Cup Women", "AFC Cup", "UEFA",
    "Cup", "Vòng loại", "Giao hữu",
]
# Sort dài trước để tránh "UEFA" xóa trước rồi "UEFA Champions League" còn thừa "Champions League"
LEAGUE_BLACKLIST.sort(key=len, reverse=True)


def generate_id(text):
    return hashlib.md5(text.encode()).hexdigest()[:12]


# ──────────────────────────────────────────────
# TIMEZONE: Tự phát hiện môi trường để fix giờ
# GitHub Actions chạy UTC+0, máy VN UTC+7
# ──────────────────────────────────────────────
def detect_time_offset():
    """
    Trả về số giờ cần cộng thêm vào giờ trận đấu hiển thị trên trang.
    - Máy VN (UTC+7): trang đã hiển thị đúng giờ VN → cộng 0
    - GitHub Actions (UTC+0): trang hiển thị giờ UTC → cộng +7
    """
    local_offset = datetime.now().astimezone().utcoffset()
    if local_offset is None:
        return 7  # fallback an toàn
    local_hours = local_offset.total_seconds() / 3600
    if local_hours >= 6.5:   # đang ở UTC+7 (VN)
        return 0
    return 7                  # đang ở UTC+0 (GitHub) → bù +7


TIME_OFFSET_HOURS = detect_time_offset()


def shift_time_str(time_str: str) -> str:
    """
    Cộng TIME_OFFSET_HOURS vào chuỗi giờ "HH:MM dd/MM" hoặc "HH:MM".
    Trả về chuỗi cùng định dạng. Nếu không parse được, trả nguyên.
    """
    if TIME_OFFSET_HOURS == 0:
        return time_str
    try:
        # Có thể là "HH:MM dd/MM" hoặc chỉ "HH:MM"
        m = re.match(r"^(\d{1,2}):(\d{2})(?:\s+(\d{1,2})/(\d{1,2}))?$", time_str.strip())
        if not m:
            return time_str
        h, mi = int(m.group(1)), int(m.group(2))
        if m.group(3):
            d, mo = int(m.group(3)), int(m.group(4))
            # Dùng năm hiện tại để tính
            year = datetime.now().year
            dt = datetime(year, mo, d, h, mi)
        else:
            today = datetime.now()
            dt = datetime(today.year, today.month, today.day, h, mi)
        dt = dt + timedelta(hours=TIME_OFFSET_HOURS)
        if m.group(3):
            return dt.strftime("%H:%M %d/%m")
        return dt.strftime("%H:%M")
    except Exception:
        return time_str


# ──────────────────────────────────────────────
# ẢNH: Ghép 2 logo thành 1 ảnh base64
# ──────────────────────────────────────────────
def _fetch_logo(url):
    """Tải 1 logo về dạng PIL Image. Dùng wsrv.nl để chuẩn hóa kích thước."""
    try:
        proxy = f"https://images.weserv.nl/?url={url}&w=100&h=100&fit=contain&output=png&bg=ececec"
        res = requests.get(proxy, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        return Image.open(BytesIO(res.content)).convert("RGBA")
    except Exception:
        return None


def _build_combined_image(logo_a_url, logo_b_url):
    """
    Ghép logo A (trái) và logo B (phải) thành 1 ảnh PNG 220x100,
    trả về chuỗi base64 data URI sẵn dùng làm "url" trong JSON.
    """
    try:
        canvas = Image.new("RGBA", (220, 100), (236, 236, 236, 255))
        for idx, url in enumerate([logo_a_url, logo_b_url]):
            if not url:
                continue
            logo = _fetch_logo(url)
            if logo is None:
                continue
            x = 10 if idx == 0 else 110
            canvas.paste(logo, (x, 0), logo)
        buf = BytesIO()
        canvas.save(buf, format="PNG", optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode()
        return f"data:image/png;base64,{b64}"
    except Exception:
        return ""


# ──────────────────────────────────────────────
# CLEAN TITLE: Xóa tên giải đấu khỏi tiêu đề
# ──────────────────────────────────────────────
def clean_title(raw_title: str) -> str:
    """Xóa tên giải đấu (LEAGUE_BLACKLIST) khỏi raw_title, trả về tiêu đề gọn."""
    text = raw_title.strip()
    for league in LEAGUE_BLACKLIST:
        # \b để chỉ match từ trọn vẹn, không xóa "Live" trong "Liverpool"
        text = re.sub(r"\b" + re.escape(league) + r"\b", "", text, flags=re.IGNORECASE)
    # Dọn khoảng trắng thừa và dấu phân cách thừa
    text = re.sub(r"\s{2,}", " ", text).strip(" -|,•·")
    return text


# ──────────────────────────────────────────────
# LẤY DANH SÁCH TRẬN ĐẤU TỪ TRANG LỊCH
# ──────────────────────────────────────────────
async def find_match_links(page):
    """
    Trả về danh sách dict:
    {url, raw_title, time_str, logo_a, logo_b, is_live}
    """
    matches = []
    # Selector: thẻ <a> trỏ tới /truc-tiep/, /xem-truc-tiep/, /match/...
    LIVE_PATTERNS = [
        "a[href*='/truc-tiep/']",
        "a[href*='/xem-truc-tiep/']",
        "a[href*='/match/']",
        "a[href*='/live/']",
    ]
    cards = []
    for sel in LIVE_PATTERNS:
        found = await page.query_selector_all(sel)
        if found:
            cards.extend(found)
    # Dedupe theo href
    seen_href = set()
    for card in cards:
        try:
            href = await card.get_attribute("href")
            if not href:
                continue
            if href.startswith("/"):
                href = BASE_DOMAIN + href
            if href in seen_href:
                continue
            seen_href.add(href)

            # Lấy text & ảnh trong card
            text_content = (await card.inner_text()) or ""
            text_content = text_content.strip()

            # Lấy 2 logo (img bên trong card)
            imgs = await card.query_selector_all("img")
            logos = []
            for img in imgs[:4]:  # tối đa 4 ảnh, lấy 2 cái đầu là logo team
                src = (await img.get_attribute("src")) or (await img.get_attribute("data-src")) or ""
                if src and ("logo" in src.lower() or "team" in src.lower() or src.endswith((".png", ".webp", ".jpg"))):
                    logos.append(src)
                if len(logos) >= 2:
                    break
            logo_a = logos[0] if len(logos) > 0 else ""
            logo_b = logos[1] if len(logos) > 1 else ""

            # Trích giờ trận: tìm "HH:MM" trong text
            m_time = re.search(r"(\d{1,2}:\d{2}(?:\s+\d{1,2}/\d{1,2})?)", text_content)
            time_str = m_time.group(1) if m_time else ""
            time_str = shift_time_str(time_str)

            # is_live: card có chữ LIVE / TRỰC TIẾP / class chứa "live"
            class_attr = (await card.get_attribute("class")) or ""
            is_live = bool(
                re.search(r"\b(LIVE|TRỰC TIẾP|TRUC TIEP)\b", text_content, re.IGNORECASE)
                or "live" in class_attr.lower()
            )

            # raw_title: phần text bỏ giờ
            raw_title = text_content
            if time_str:
                raw_title = raw_title.replace(time_str, "")
            raw_title = re.sub(r"\s{2,}", " ", raw_title).strip()

            matches.append({
                "url": href,
                "raw_title": raw_title,
                "time_str": time_str,
                "logo_a": logo_a,
                "logo_b": logo_b,
                "is_live": is_live,
            })
        except Exception as e:
            print(f"  [WARN] parse card lỗi: {e}")
            continue
    return matches


# ──────────────────────────────────────────────
# LẤY STREAM URL CỦA 1 TRẬN
# ──────────────────────────────────────────────
async def fetch_stream_url(context, match_url: str, timeout_sec: int = 12) -> str:
    """
    Mở trang trận đấu, bắt response có .m3u8, trả URL đầu tiên hợp lệ.
    Mỗi trận dùng 1 page riêng, listener gắn rồi gỡ ngay → không bị race.
    """
    page = await context.new_page()
    m3u8_list = []

    def on_response(res):
        url = res.url
        if ".m3u8" in url:
            m3u8_list.append(url)

    page.on("response", on_response)
    try:
        try:
            await page.goto(match_url, wait_until="domcontentloaded", timeout=20000)
        except Exception:
            pass
        # Cho player load thêm
        for _ in range(timeout_sec):
            if m3u8_list:
                break
            await asyncio.sleep(1)
        # Lọc playlist chính (master.m3u8 hoặc index.m3u8 hoặc đầu tiên)
        for url in m3u8_list:
            if "master" in url or "index" in url or "playlist" in url:
                return url
        return m3u8_list[0] if m3u8_list else ""
    except Exception as e:
        print(f"  [WARN] fetch_stream_url {match_url[:60]}: {e}")
        return ""
    finally:
        try:
            page.remove_listener("response", on_response)
        except Exception:
            pass
        await page.close()


# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────
async def main():
    vn_tz   = timezone(timedelta(hours=7))
    now_vn  = datetime.now(vn_tz)
    now_str = now_vn.strftime("%H:%M %d/%m/%Y")

    print(f"[INFO] Bắt đầu lúc: {now_str} (Giờ VN)")
    print(f"[INFO] Time offset: +{TIME_OFFSET_HOURS}h (môi trường: "
          f"{'GitHub UTC' if TIME_OFFSET_HOURS else 'Máy VN'})")
    print(f"[INFO] Đang tải: {TARGET_URL}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/122.0.0.0 Safari/537.36"),
            viewport={"width": 1366, "height": 900},
        )
        page = await context.new_page()

        try:
            # Bước 1: Load trang lịch thi đấu
            try:
                await page.goto(TARGET_URL, wait_until="networkidle", timeout=30000)
            except Exception:
                await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(4)

            # Scroll để load lazy
            for _ in range(6):
                await page.mouse.wheel(0, 2000)
                await asyncio.sleep(1.2)
            await asyncio.sleep(2)

            # Bước 2: Lấy danh sách trận
            match_data = await find_match_links(page)
            print(f"[INFO] Tìm được {len(match_data)} trận đấu")

            # Bước 3: Lấy stream URL cho từng trận live (song song có giới hạn)
            sem = asyncio.Semaphore(3)  # 3 trận đồng thời

            async def _process(ch):
                if not ch["is_live"]:
                    ch["stream"] = ""
                    return
                async with sem:
                    print(f"  [LIVE] {ch['raw_title'][:50]}")
                    ch["stream"] = await fetch_stream_url(context, ch["url"])

            await asyncio.gather(*[_process(ch) for ch in match_data])

            # Bước 4: Build ảnh ghép logo (chạy song song trong thread pool)
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor(max_workers=6) as pool:
                img_tasks = [
                    loop.run_in_executor(pool, _build_combined_image, ch["logo_a"], ch["logo_b"])
                    for ch in match_data
                ]
                imgs = await asyncio.gather(*img_tasks)
            for ch, img in zip(match_data, imgs):
                ch["combined_img"] = img

            # Bước 5: Build output
            json_output = {
                "updated_at": now_str,
                "groups": [
                    {"name": "🔴 Đang trực tiếp", "channels": []},
                    {"name": "🗓 Sắp diễn ra",   "channels": []},
                ],
            }
            m3u_content = "#EXTM3U\n"
            vlc_content = "#EXTM3U\n"

            for ch in match_data:
                ch["title"] = clean_title(ch["raw_title"])
                match_id   = generate_id(ch["url"])
                group_idx  = 0 if ch["is_live"] else 1
                group_name = json_output["groups"][group_idx]["name"]
                title_disp = (f"{ch['time_str']} {ch['title']}".strip()
                              if ch["time_str"] else ch["title"])
                stream     = ch.get("stream", "") or ""

                # JSON channel
                json_output["groups"][group_idx]["channels"].append({
                    "id":       match_id,
                    "title":    ch["title"],
                    "time":     ch["time_str"],
                    "is_live":  ch["is_live"],
                    "url":      ch["url"],
                    "stream":   stream,
                    "logo_a":   ch["logo_a"],
                    "logo_b":   ch["logo_b"],
                    "image":    ch["combined_img"],
                })

                # Chỉ xuất IPTV/VLC khi có stream (live)
                if not stream:
                    continue

                # IPTV M3U
                m3u_content += (
                    f'#EXTINF:-1 tvg-id="{match_id}" tvg-logo="{ch["logo_a"]}" '
                    f'group-title="{group_name}", ⚽ {title_disp}\n'
                    f'#EXTVLCOPT:http-referrer={ch["url"]}\n'
                    f'#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    f'AppleWebKit/537.36\n'
                    f'{stream}\n'
                )
                # VLC M3U (full options cho VLC desktop)
                vlc_content += (
                    f'#EXTINF:-1 tvg-id="{match_id}" tvg-logo="{ch["logo_a"]}" '
                    f'group-title="{group_name}", ⚽ {title_disp}\n'
                    f'#EXTVLCOPT:network-caching=1000\n'
                    f'#EXTVLCOPT:http-referrer={ch["url"]}\n'
                    f'#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    f'AppleWebKit/537.36\n'
                    f'{stream}\n'
                )

            # Bước 6: Ghi file
            with open("hoiquan.json",      "w", encoding="utf-8") as f:
                json.dump(json_output, f, ensure_ascii=False, indent=4)
            with open("hoiquan_iptv.txt",  "w", encoding="utf-8") as f:
                f.write(m3u_content)
            with open("hoiquan_vlc.txt",   "w", encoding="utf-8") as f:
                f.write(vlc_content)

            live_count     = sum(1 for ch in match_data if ch["is_live"])
            upcoming_count = sum(1 for ch in match_data if not ch["is_live"])
            stream_count   = sum(1 for ch in match_data if ch.get("stream"))
            print(f"\n✅ Hoàn thành lúc: {now_str} (Giờ VN)")
            print(f"   🔴 Live: {live_count} trận  |  🗓 Sắp diễn ra: {upcoming_count} trận")
            print(f"   📡 Lấy được stream: {stream_count}/{live_count} trận live")
            print(f"   📄 Đã xuất: hoiquan.json | hoiquan_iptv.txt | hoiquan_vlc.txt")

        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
