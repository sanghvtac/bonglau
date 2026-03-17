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

# Danh sách các cụm từ giải đấu cần loại bỏ hoàn toàn
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
    # 1. Xác định trạng thái Live
    is_live_origin = any(word in text.upper() for word in ["LIVE", "●"])
    
    # 2. Fix Ngày/Giờ (Đảm bảo khoảng cách)
    text = re.sub(r'(\d{2}:\d{2})\s*(\d{2}/\d{2})', r'\1 \2', text)
    
    # 3. Tách BLV
    blv_match = re.search(r'(BLV.*)', text, flags=re.IGNORECASE)
    blv_str = f" {blv_match.group(1).strip()}" if blv_match else ""
    text_clean = re.sub(r'(BLV.*)', '', text, flags=re.IGNORECASE).strip()

    # --- BƯỚC BẢO VỆ LIVERPOOL ---
    text_clean = re.sub(r'(?i)Liverpool', 'LVP_PROTECTED', text_clean)

    # 4. LỌC GIẢI ĐẤU (Xóa không nương tay)
    for league in LEAGUE_BLACKLIST:
        text_clean = re.sub(rf'(?i){re.escape(league)}', ' ', text_clean)

    # 5. Lấy mốc thời gian bảo vệ
    time_match = re.search(r'\d{2}:\d{2}\s*\d{2}/\d{2}', text_clean)
    time_str = time_match.group(0) if time_match else ""
    
    # 6. Xử lý nội dung core
    # Xóa icon ●, chữ Live đứng lẻ, tỉ số, hiệp đấu
    core = re.sub(r'(●|\bLive\b|Sắp diễn ra|Sắp bắt đầu|VS|H\d\s*-\s*\d+\'?|\d-\d|-)', ' ', text_clean, flags=re.IGNORECASE)
    core = core.replace(time_str, "").strip()
    
    # 7. LOGIC TÁCH CHỮ DÍNH (CamelCase)
    core = re.sub(r'([a-z])([A-Z])', r'\1 VS \2', core)
    core = re.sub(r'(\d)([A-Z])', r'\1 VS \2', core)
    if ' VS ' not in core:
        core = re.sub(r'([a-zA-Z])(\d)', r'\1 VS \2', core)
    
    # --- PHỤC HỒI LIVERPOOL ---
    core = core.replace('LVP_PROTECTED', 'Liverpool')

    # 8. Làm sạch khoảng trắng và nối lại
    teams = [t.strip() for t in core.split(' VS ') if t.strip()]
    if len(teams) >= 2:
        final_teams = f"{teams[0]} VS {teams[1]}"
    else:
        final_teams = core

    # Kiểm tra lần cuối để xóa chữ VS thừa ở đầu (như ví dụ "UEFA VS Sporting" của bạn)
    final_teams = re.sub(r'^VS\s+', '', final_teams).strip()

    return f"{time_str} {final_teams}{blv_str}".strip(), final_teams, is_live_origin

async def main():
    # Ép chuẩn múi giờ Việt Nam dù chạy trên GitHub hay Local
    # GitHub Action mặc định chạy giờ UTC (GMT+0)
    vn_timezone = timezone(timedelta(hours=7))
    vn_now = datetime.now(vn_timezone)
    now_str = vn_now.strftime("%H:%M %d/%m/%Y")

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(user_agent="Mozilla/5.0")
        page = await context.new_page()
        page.set_default_timeout(60000)
        
        try:
            await page.goto(TARGET_URL, wait_until="domcontentloaded")
            for _ in range(5): 
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
                        await page.goto(item['url'], wait_until="domcontentloaded", timeout=15000)
                        await asyncio.sleep(4)
                        if m3u8_list: item['stream'] = max(m3u8_list, key=len)
                    except: pass

            # --- XUẤT FILE ---
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

                m3u_content += f'#EXTINF:-1 tvg-id="{match_id}" tvg-logo="{ch["logo_a"]}" group-title="{group}", {ch["title"]}\n'
                m3u_content += f'#EXTVLCOPT:http-user-agent=Mozilla/5.0\n'
                m3u_content += f'#EXTVLCOPT:http-referrer={ch["url"]}\n'
                m3u_content += f'{stream}\n'

            with open("thiendinh.json", "w", encoding="utf-8") as f: json.dump(json_output, f, ensure_ascii=False, indent=4)
            with open("thiendinh_iptv.txt", "w", encoding="utf-8") as f: f.write(m3u_content)
            with open("thiendinh_vlc.txt", "w", encoding="utf-8") as f: f.write(m3u_content)
            
            print(f"Hoàn tất lúc: {now_str} (Giờ VN chuẩn)")
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
