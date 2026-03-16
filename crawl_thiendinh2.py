import json
import asyncio
import re
from playwright.async_api import async_playwright
from datetime import datetime, timedelta

TARGET_URL = "https://sv1.thiendinh.live/lich-thi-dau/bong-da?by=state&value=live"

def adjust_time(text):
    def replace_time(match):
        t = datetime.strptime(match.group(0), "%H:%M")
        return (t + timedelta(hours=7)).strftime("%H:%M")
    return re.sub(r'\d{2}:\d{2}', replace_time, text)

def clean_title(text):
    # Loại bỏ rác
    text = re.sub(r'(Live|Sắp diễn ra|Sắp bắt đầu|null|H1|H2|\')', ' ', text, flags=re.IGNORECASE)
    leagues = ['Ligue 1', 'Serie A', 'Bundesliga', 'Premier League', 'La Liga', '1. Lig', 'V.League']
    for l in leagues: text = re.sub(re.escape(l), '', text, flags=re.IGNORECASE)
    
    # Ép buộc khoảng trắng giữa Ngày/Giờ và Chữ cái
    text = re.sub(r'(\d{2}:\d{2})(\d{2}/\d{2})', r'\1 \2', text)
    text = re.sub(r'([0-9/])([A-Za-zÀ-Ỹà-ỹ])', r'\1 \2', text)
    
    # Tách Đội 1 - Tỷ số - Đội 2
    text = re.sub(r'([a-zA-ZÀ-Ỹa-zà-ỹ]+)(\d-\d|VS)([a-zA-ZÀ-Ỹa-zà-ỹ]+)', r'\1 \2 \3', text)
    return " ".join(text.split())

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(user_agent="Mozilla/5.0")
        await page.goto(TARGET_URL, wait_until="domcontentloaded")
        for _ in range(8):
            await page.mouse.wheel(0, 2000)
            await asyncio.sleep(1)
        
        elements = await page.query_selector_all("a[href*='/xem-truc-tiep/']")
        match_data = []
        
        for el in elements:
            url = await el.get_attribute("href")
            # Tách riêng logo_home và logo_away
            logo_list = await el.evaluate("el => Array.from(el.querySelectorAll('img')).filter(i => i.className.includes('w-[48px]')).map(i => i.src)")
            
            raw_name = (await el.text_content()).strip()
            final_title = clean_title(adjust_time(raw_name))
            
            match_data.append({
                "title": final_title,
                "url": "https://sv1.thiendinh.live" + url if url.startswith('/') else url,
                "logo_home": logo_list[0] if len(logo_list) > 0 else "",
                "logo_away": logo_list[1] if len(logo_list) > 1 else "",
                "stream": ""
            })

        for item in match_data:
            if any(k in item['title'].upper() for k in ["LIVE", "TRỰC TIẾP"]):
                m3u8_list = []
                def intercept_response(res):
                    if ".m3u8" in res.url: m3u8_list.append(res.url)
                page.on("response", intercept_response)
                try:
                    await page.goto(item['url'], wait_until="domcontentloaded", timeout=5000)
                    await asyncio.sleep(2)
                    if m3u8_list: item['stream'] = max(m3u8_list, key=len)
                except: pass
                page.remove_listener("response", intercept_response)

        # JSON chuẩn cho Tivi (sử dụng trường 'logo' để hiển thị)
        json_output = {"name": "Thiên Đỉnh TV", "channels": []}
        for ch in match_data:
            json_output["channels"].append({
                "name": ch['title'],
                "logo": ch['logo_home'] if ch['logo_home'] else ch['logo_away'], 
                "stream": ch['stream'],
                "referer": ch['url']
            })
        with open("thiendinh.json", "w", encoding="utf-8") as f:
            json.dump(json_output, f, ensure_ascii=False, indent=4)

        # IPTV
        with open("thiendinh_iptv.txt", "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for ch in match_data:
                f.write(f'#EXTINF:-1 tvg-logo="{ch["logo_home"]}",{ch["title"]}\n{ch["stream"] if ch["stream"] else "#"}\n')

        # VLC
        with open("thiendinh_vlc.txt", "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for ch in match_data:
                f.write(f'#EXTINF:-1,{ch["title"]}\n{ch["stream"] if ch["stream"] else "#"}\n')

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
