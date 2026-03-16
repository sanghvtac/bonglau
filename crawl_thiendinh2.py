import json
import asyncio
import re
import os
from playwright.async_api import async_playwright
from datetime import datetime, timedelta

TARGET_URL = "https://sv1.thiendinh.live/lich-thi-dau/bong-da?by=state&value=live"

# Hàm cộng 7 giờ (Việt Nam)
def adjust_time(text):
    def replace_time(match):
        t_str = match.group(0)
        t = datetime.strptime(t_str, "%H:%M")
        new_t = t + timedelta(hours=7)
        return new_t.strftime("%H:%M")
    return re.sub(r'\d{2}:\d{2}', replace_time, text)

# Hàm làm sạch tiêu đề
def clean_title(text):
    # Xóa "Live" / "Sắp diễn ra"
    text = re.sub(r'(Live|Sắp diễn ra)', '', text, flags=re.IGNORECASE).strip()
    # Tách giờ với ngày: 23:1515/03 -> 23:15 15/03
    text = re.sub(r'(\d{2}:\d{2})(\d{2}/\d{2})', r'\1 \2', text)
    # Tách khoảng trắng giữa ngày (ví dụ 16/03) và tên đội/giải (16/031.Lig -> 16/03 1.Lig)
    text = re.sub(r'(\d{2}/\d{2})([A-ZÀ-Ỹa-zà-ỹ0-9\.])', r'\1 \2', text)
    # Xóa tên giải đấu
    leagues = ['Ligue1', 'Serie A', 'Bundesliga', 'Premier League', 'La Liga', 'Champions League', 'Europa League']
    for league in leagues:
        text = re.sub(re.escape(league), '', text, flags=re.IGNORECASE)
    return text.strip()

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        # Thêm user_agent để giả lập trình duyệt thật, tránh bị chặn
        page = await browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        
        await page.goto(TARGET_URL, wait_until="domcontentloaded")
        
        # Cuộn trang để nội dung tải hết
        for _ in range(8):
            await page.mouse.wheel(0, 2000)
            await asyncio.sleep(1)
        
        elements = await page.query_selector_all("a[href*='/xem-truc-tiep/']")
        match_data = []
        
        for el in elements:
            url = await el.get_attribute("href")
            full_url = "https://sv1.thiendinh.live" + url if url.startswith('/') else url
            raw_name = (await el.text_content()).strip()
            
            # Lấy logo bằng cách tìm tất cả ảnh <img> bên trong thẻ <a> hiện tại
            logo_list = await el.evaluate("el => Array.from(el.querySelectorAll('img')).map(img => img.src)")
            logo_home = logo_list[0] if len(logo_list) > 0 else ""
            logo_away = logo_list[1] if len(logo_list) > 1 else ""

            final_title = clean_title(adjust_time(raw_name))
            
            if not any(d['url'] == full_url for d in match_data):
                match_data.append({
                    "title": final_title, 
                    "url": full_url, 
                    "logo_home": logo_home,
                    "logo_away": logo_away,
                    "stream": ""
                })

        # Đào link stream
        for item in match_data:
            if "LIVE" in item['title'].upper() or "TRỰC TIẾP" in item['title'].upper():
                m3u8_list = []
                def intercept(res):
                    if ".m3u8" in res.url: m3u8_list.append(res.url)
                page.on("response", intercept)
                try:
                    await page.goto(item['url'], wait_until="domcontentloaded", timeout=8000)
                    await asyncio.sleep(2)
                    if m3u8_list: item['stream'] = max(m3u8_list, key=len)
                except: pass
                page.remove_listener("response", intercept)

        # Xuất file
        # 1. IPTV
        with open("thiendinh_iptv.txt", "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for ch in match_data:
                group = "🔴 Live" if ch['stream'] else "🗓 Sắp diễn ra"
                f.write(f'#EXTINF:-1 group-title="{group}" tvg-logo="{ch["logo_home"]}",{ch["title"]}\n')
                if ch['stream']: f.write(f'{ch["stream"]}|Referer={ch["url"]}\n')
                else: f.write(f'#\n')

        # 2. VLC
        with open("thiendinh_vlc.txt", "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for ch in match_data:
                f.write(f'#EXTINF:-1,{ch["title"]}\n{ch["stream"] if ch["stream"] else "#"}\n')

        # 3. JSON
        json_output = {"name": "Thiên Đỉnh TV", "groups": [{"name": "🔴 Live", "channels": []}, {"name": "🗓 Sắp diễn ra", "channels": []}]}
        for ch in match_data:
            channel = {
                "name": ch['title'],
                "logo_home": ch['logo_home'],
                "logo_away": ch['logo_away'],
                "sources": [{"contents": [{"streams": [{"stream_links": [{"url": ch['stream'], "request_headers": [{"key": "Referer", "value": ch['url']}]}]}]}]}]
            }
            if ch['stream']: json_output["groups"][0]["channels"].append(channel)
            else: json_output["groups"][1]["channels"].append(channel)
        
        with open("thiendinh.json", "w", encoding="utf-8") as f:
            json.dump(json_output, f, ensure_ascii=False, indent=4)
        
        print(f"Hoàn thành: {len(match_data)} trận đấu.")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
