import json
import asyncio
import re
import hashlib
import base64
import requests
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

def get_base64_combined_image(logo_a_url, logo_b_url):
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        combined = Image.new("RGBA", (450, 200), (236, 236, 236, 255))
        for i, url in enumerate([logo_a_url, logo_b_url]):
            if url:
                try:
                    proxy = f"https://images.weserv.nl/?url={url}&w=200&h=200&fit=contain&output=png"
                    res = requests.get(proxy, headers=headers, timeout=10)
                    img = Image.open(BytesIO(res.content)).convert("RGBA")
                    combined.paste(img, (20 if i == 0 else 230, 0), img)
                except: pass
        buffered = BytesIO()
        combined.save(buffered, format="PNG")
        return f"data:image/png;base64,{base64.b64encode(buffered.getvalue()).decode()}"
    except: return ""

def clean_title(text):
    # 1. Nhận diện Live (trước khi xóa chữ)
    is_live_origin = any(word in text.upper() for word in ["LIVE", "●"])
    
    # 2. Chuẩn hóa Ngày/Giờ: Xử lý dính chữ và đảm bảo format HH:MM DD/MM
    text = re.sub(r'(\d{2}:\d{2})\s*(\d{2}/\d{2})', r'\1 \2', text)
    time_match = re.search(r'\d{2}:\d{2}\s*\d{2}/\d{2}', text)
    time_str = time_match.group(0) if time_match else ""

    # 3. Tách BLV
    blv_match = re.search(r'(BLV.*)', text, flags=re.IGNORECASE)
    blv_str = f" {blv_match.group(1).strip()}" if blv_match else ""
    
    # 4. LÀM SẠCH TIÊU ĐỀ (Xóa Live, icon, giải đấu)
    # Xóa chữ Live và các khoảng trắng lạ xung quanh nó
    clean = re.sub(r'(?i)Live|●|Sắp diễn ra|Sắp bắt đầu', ' ', text)
    
    # Xóa các giải đấu trong Blacklist
    for league in LEAGUE_BLACKLIST:
        clean = re.sub(rf'(?i){re.escape(league)}', ' ', clean)

    # 5. Xử lý dính chữ đặc biệt cho Liverpool (tránh Liverpool VS Galatasaray dính nhau)
    clean = re.sub(r'(Liverpool)([A-Z])', r'\1 VS \2', clean, flags=re.IGNORECASE)

    # 6. Xử lý CamelCase chung và cô lập tên đội
    clean = clean.replace(time_str, "").replace(blv_str.strip(), "")
    clean = re.sub(r'([a-z])([A-Z])', r'\1 VS \2', clean)
    clean = re.sub(r'(\d)([A-Z])', r'\1 VS \2', clean)
    
    # Xóa tỉ số, hiệp đấu, dấu gạch ngang rác
    clean = re.sub(r'(H\d\s*-\s*\d+\'?|\d-\d|-|VS)', ' ', clean, flags=re.IGNORECASE)
    
    # 7. Tách vế để lấy Đội A VS Đội B
    parts = [p.strip() for p in clean.split() if p.strip()]
    if len(parts) >= 2:
        # Nếu có từ 2 cụm trở lên, ta coi cụm đầu là Đội A, phần còn lại là Đội B (sau khi đã lọc sạch giải)
        # Tuy nhiên để an toàn và giống sáng nay:
        mid = len(parts) // 2
        team_a = " ".join(parts[:mid])
        team_b = " ".join(parts[mid:])
        final_teams = f"{team_a} VS {team_b}"
    else:
        final_teams = " ".join(parts)

    return f"{time_str} {final_teams}{blv_str}".strip(), final_teams, is_live_origin

async def main():
    # CÁCH ĐƠN GIẢN NHẤT CHO GITHUB: Lấy giờ UTC hiện tại và +7
    now_utc = datetime.now(timezone.utc)
    vn_time = now_utc + timedelta(hours=7)
    now_str = vn_time.strftime("%H:%M %d/%m/%Y")

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
                full_title, teams_only, is_live = clean_title(raw_text)

                imgs = await el.query_selector_all("img")
                logos = [await img.get_attribute("data-src") or await img.get_attribute("src") for img in imgs]
                logos = [l for l in logos if l and "http" in l and "30aaqin.png" not in l]
                
                match_data.append({
                    "title": full_title, "url": full_url, "logo_a": logos[0] if len(logos) >=1 else "", 
                    "combined_img": get_base64_combined_image(logos[0] if len(logos) >=1 else "", logos[1] if len(logos) >=2 else ""), 
                    "is_live": is_live, "stream": ""
                })

            for item in match_data:
                if item['is_live']:
                    m3u8_list = []
                    page.on("response", lambda res: m3u8_list.append(res.url) if ".m3u8" in res.url else None)
                    try:
                        await page.goto(item['url'], wait_until="domcontentloaded", timeout=10000)
                        await asyncio.sleep(4)
                        if m3u8_list: item['stream'] = max(m3u8_list, key=len)
                    except: pass

            # XUẤT FILE
            json_output = {"name": f"Thiên Đỉnh TV ({now_str})", "groups": [{"id": "live", "name": "🔴 Live", "channels": []}, {"id": "upcoming", "name": "🗓 Sắp diễn ra", "channels": []}]}
            m3u_content = f"#EXTM3U\n#PLAYLIST: Thiên Đỉnh TV ({now_str})\n"

            for ch in match_data:
                match_id = generate_id(ch['url'])
                stream = ch['stream'] if ch['stream'] else "http://0.0.0.0/not-live"
                group = "LIVE" if ch['is_live'] else "UPCOMING"
                
                channel_json = {
                    "id": f"ch-{match_id}", "name": f"⚽ {ch['title']}", "type": "single", "display": "thumbnail-only",
                    "image": {"url": ch['combined_img'] if ch['combined_img'] else ch['logo_a'], "display": "contain", "padding": 1, "background_color": "#ececec"},
                    "sources": [{"id": f"src-{match_id}", "contents": [{"id": f"ct-{match_id}", "streams": [{"stream_links": [{"url": ch['stream'] if ch['stream'] else "", "type": "hls", "request_headers": [{"key": "Referer", "value": ch['url']}, {"key": "User-Agent", "value": "Mozilla/5.0"}]}]}]}]}]
                }
                if ch['is_live']: json_output["groups"][0]["channels"].append(channel_json)
                else: json_output["groups"][1]["channels"].append(channel_json)

                m3u_content += f'#EXTINF:-1 tvg-id="{match_id}" tvg-logo="{ch["logo_a"]}" group-title="{group}", {ch["title"]}\n{stream}\n'

            with open("thiendinh.json", "w", encoding="utf-8") as f: json.dump(json_output, f, ensure_ascii=False, indent=4)
            with open("thiendinh_iptv.txt", "w", encoding="utf-8") as f: f.write(m3u_content)
            
            print(f"Hoàn thành lúc: {now_str} (Giờ VN)")
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
