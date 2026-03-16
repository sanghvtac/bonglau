import json
import asyncio
import re
from playwright.async_api import async_playwright
from datetime import datetime, timedelta

TARGET_URL = "https://sv1.thiendinh.live/lich-thi-dau/bong-da?by=state&value=live"

def clean_title(text):
    # 1. Trích xuất thời gian (HH:MM và DD/MM)
    time_match = re.search(r'(\d{2}:\d{2})\s*(\d{2}/\d{2})', text)
    time_str = f"{time_match.group(1)} {time_match.group(2)}" if time_match else ""
    
    # 2. Trích xuất tỷ số (0-0, 1-2, v.v.)
    score_match = re.search(r'(\d-\d)', text)
    score_str = score_match.group(1) if score_match else ""
    
    # 3. Trích xuất tên 2 đội (Dựa trên cấu trúc thường gặp: [Đội 1] VS/Score [Đội 2])
    # Loại bỏ rác khỏi tên đội
    clean_base = re.sub(r'(Live|Sắp bắt đầu|Sắp diễn ra|null|H1|H2|BLV.*)', '', text, flags=re.IGNORECASE)
    teams = re.split(r'\d-\d|VS', clean_base)
    team1 = teams[0].strip() if len(teams) > 0 else ""
    team2 = teams[1].strip() if len(teams) > 1 else ""
    
    # 4. Tên hiển thị chuẩn: HH:MM DD/MM Team1 score Team2
    new_title = f"{time_str} {team1} {score_str} {team2}".strip()
    return new_title

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(user_agent="Mozilla/5.0")
        await page.goto(TARGET_URL, wait_until="domcontentloaded")
        for _ in range(5):
            await page.mouse.wheel(0, 1000)
            await asyncio.sleep(1)
        
        elements = await page.query_selector_all("a[href*='/xem-truc-tiep/']")
        match_data = []
        
        for el in elements:
            url = await el.get_attribute("href")
            full_url = "https://sv1.thiendinh.live" + url if url.startswith('/') else url
            logo_list = await el.evaluate("el => Array.from(el.querySelectorAll('img')).filter(i => i.className.includes('w-[48px]')).map(i => i.src)")
            raw_name = (await el.text_content()).strip()
            
            match_data.append({
                "name": clean_title(raw_name),
                "logo": logo_list[0] if len(logo_list) > 0 else "",
                "stream": "",
                "referer": full_url
            })

        # Đào stream
        for item in match_data:
            m3u8_list = []
            def intercept(res):
                if ".m3u8" in res.url: m3u8_list.append(res.url)
            page.on("response", intercept)
            try:
                await page.goto(item['referer'], wait_until="domcontentloaded", timeout=5000)
                await asyncio.sleep(2)
                if m3u8_list: item['stream'] = max(m3u8_list, key=len)
            except: pass
            page.remove_listener("response", intercept)

        # Xuất JSON cấu trúc "Flat" (App Tivi thích cái này)
        final_json = {"channels": [m for m in match_data if m['stream']]}
        with open("thiendinh.json", "w", encoding="utf-8") as f:
            json.dump(final_json, f, ensure_ascii=False, indent=4)
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
