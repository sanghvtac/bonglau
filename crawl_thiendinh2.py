import json
import asyncio
import re
from playwright.async_api import async_playwright
from datetime import datetime, timedelta

TARGET_URL = "https://sv1.thiendinh.live/lich-thi-dau/bong-da?by=state&value=live"

def adjust_time(text):
    def replace_time(match):
        t_str = match.group(0)
        t = datetime.strptime(t_str, "%H:%M")
        new_t = t + timedelta(hours=7)
        return new_t.strftime("%H:%M")
    return re.sub(r'\d{2}:\d{2}', replace_time, text)

def clean_title(text):
    # 1. Xóa "Live" / "Sắp diễn ra"
    text = re.sub(r'(Live|Sắp diễn ra)', '', text, flags=re.IGNORECASE).strip()
    # 2. Tách giờ với ngày: 23:1515/03 -> 23:15 15/03
    text = re.sub(r'(\d{2}:\d{2})(\d{2}/\d{2})', r'\1 \2', text)
    # 3. TÁCH DẤU CÁCH NÂNG CAO: Xử lý cả dấu chấm như "16/031.Lig" -> "16/03 1.Lig"
    text = re.sub(r'(\d{2}/\d{2})([A-ZÀ-Ỹa-zà-ỹ0-9\.])', r'\1 \2', text)
    # 4. Xóa giải đấu
    leagues = ['Ligue1', 'Serie A', 'Bundesliga', 'Premier League', 'La Liga', 'Champions League', 'Europa League']
    for league in leagues:
        text = re.sub(re.escape(league), '', text, flags=re.IGNORECASE)
    return text.strip()

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(TARGET_URL, wait_until="domcontentloaded")
        
        for _ in range(8):
            await page.mouse.wheel(0, 2000)
            await asyncio.sleep(1)
        
        # Lấy danh sách các thẻ cha chứa cả trận đấu
        elements = await page.query_selector_all("a[href*='/xem-truc-tiep/']")
        match_data = []
        
        for el in elements:
            # Lấy thông tin cơ bản
            url = await el.get_attribute("href")
            full_url = "https://sv1.thiendinh.live" + url if url.startswith('/') else url
            
            # LẤY LOGO: Dùng get_attribute('src') trực tiếp trên các ảnh con
            # Cách này ổn định hơn vì nó tìm trong phạm vi của thẻ 'el'
            logo_urls = await el.evaluate_handle("el => Array.from(el.querySelectorAll('img')).map(img => img.src)")
            logo_list = await logo_urls.json_value()
            
            logo_home = logo_list[0] if len(logo_list) > 0 else ""
            logo_away = logo_list[1] if len(logo_list) > 1 else ""
            
            raw_name = (await el.text_content()).strip()
            final_title = clean_title(adjust_time(raw_name))
            
            if not any(d['url'] == full_url for d in match_data):
                match_data.append({
                    "title": final_title, 
                    "url": full_url, 
                    "logo_home": logo_home,
                    "logo_away": logo_away,
                    "stream": ""
                })

        # ... (Phần đào link stream và xuất file giữ nguyên như cũ)
        # (Bạn vẫn để phần xuất file cũ của bạn ở đây nhé)
