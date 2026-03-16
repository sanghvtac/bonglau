import json
import asyncio
import re
from playwright.async_api import async_playwright
from datetime import datetime, timedelta

TARGET_URL = "https://sv1.thiendinh.live/lich-thi-dau/bong-da?by=state&value=live"

def process_match_info(text):
    # 1. Tách giờ và ngày (VD: 17:30 16/03)
    time_match = re.search(r'(\d{2}:\d{2})\s*(\d{2}/\d{2})', text)
    time_str = f"{time_match.group(1)} {time_match.group(2)}" if time_match else ""
    
    # 2. Xử lý tên đội và tỷ số: loại bỏ các phần rác bằng cách tìm vị trí VS hoặc tỷ số
    # Cắt từ đoạn sau ngày tháng
    content = re.sub(r'\d{2}:\d{2}\s*\d{2}/\d{2}', '', text)
    # Tìm tỷ số (ví dụ: 0-0) hoặc chữ "VS"
    separator = re.search(r'(\d-\d|VS)', content)
    
    if separator:
        parts = re.split(r'\d-\d|VS', content)
        # Lấy phần trước và sau dấu phân cách, dọn dẹp các ký tự rác xung quanh
        team1 = re.sub(r'(Live|Sắp diễn ra|null|H1|H2|BLV.*)', '', parts[0]).strip()
        team2 = re.sub(r'(Live|Sắp diễn ra|null|H1|H2|BLV.*)', '', parts[1]).split('BLV')[0].strip()
        return f"{time_str} {team1} {separator.group(1)} {team2}"
    return text.strip()

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(user_agent="Mozilla/5.0")
        await page.goto(TARGET_URL, wait_until="domcontentloaded")
        await asyncio.sleep(3) # Đợi tải danh sách
        
        elements = await page.query_selector_all("a[href*='/xem-truc-tiep/']")
        match_data = []
        
        for el in elements:
            # Lấy logo chuẩn nhất
            logos = await el.evaluate("el => Array.from(el.querySelectorAll('img')).filter(i => i.className.includes('w-[48px]')).map(i => i.src)")
            
            raw_text = (await el.text_content()).strip()
            # Loại bỏ giải đấu để không bị dính vào tên đội
            leagues = ['Ligue 1', 'Serie A', 'Bundesliga', 'Premier League', 'La Liga', '1. Lig', 'V.League']
            for l in leagues: raw_text = raw_text.replace(l, '')
            
            processed_name = process_match_info(raw_text)
            
            match_data.append({
                "name": processed_name,
                "logo": logos[0] if logos else "",
                "url": "https://sv1.thiendinh.live" + await el.get_attribute("href")
            })

        # Đào stream
        for item in match_data:
            m3u8 = None
            def intercept(res):
                nonlocal m3u8
                if ".m3u8" in res.url and not m3u8: m3u8 = res.url
            page.on("response", intercept)
            try:
                await page.goto(item['url'], wait_until="domcontentloaded", timeout=5000)
                await asyncio.sleep(2)
                item['stream'] = m3u8 if m3u8 else ""
            except: item['stream'] = ""
            page.remove_listener("response", intercept)

        # Xuất JSON
        final_channels = [{"name": m['name'], "logo": m['logo'], "url": m['stream']} for m in match_data if m['stream']]
        with open("thiendinh.json", "w", encoding="utf-8") as f:
            json.dump({"channels": final_channels}, f, ensure_ascii=False, indent=4)
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
