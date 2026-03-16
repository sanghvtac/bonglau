import json
import asyncio
import re
from playwright.async_api import async_playwright
from datetime import datetime, timedelta

TARGET_URL = "https://sv1.thiendinh.live/lich-thi-dau/bong-da?by=state&value=live"

# Hàm cộng 7 giờ cho múi giờ Việt Nam
def adjust_time(text):
    def replace_time(match):
        t_str = match.group(0)
        t = datetime.strptime(t_str, "%H:%M")
        new_t = t + timedelta(hours=7)
        return new_t.strftime("%H:%M")
    return re.sub(r'\d{2}:\d{2}', replace_time, text)

# Hàm làm sạch tiêu đề theo yêu cầu mới
def clean_title(text):
    # 1. Xóa "Live" / "Sắp diễn ra"
    text = re.sub(r'(Live|Sắp diễn ra)', '', text, flags=re.IGNORECASE).strip()
    # 2. Tách giờ với ngày (HH:MMDD/MM -> HH:MM DD/MM)
    text = re.sub(r'(\d{2}:\d{2})(\d{2}/\d{2})', r'\1 \2', text)
    # 3. Xóa tên giải đấu
    leagues = ['Ligue1', 'Serie A', 'Bundesliga', 'Premier League', 'La Liga', 'Champions League', 'Europa League']
    for league in leagues:
        text = re.sub(re.escape(league), '', text, flags=re.IGNORECASE)
    return text.strip()

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        # Chặn hình ảnh/font để crawl nhanh hơn
        await page.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "font", "media"] else route.continue_())

        await page.goto(TARGET_URL, wait_until="domcontentloaded")
        for _ in range(8):
            await page.mouse.wheel(0, 2000)
            await asyncio.sleep(1)
        
        elements = await page.query_selector_all("a[href*='/xem-truc-tiep/']")
        match_data = []
        
        for el in elements:
            url = await el.get_attribute("href")
            full_url = "https://sv1.thiendinh.live" + url if url.startswith('/') else url
            raw_name = (await el.text_content()).strip()
            
            # --- Yêu cầu mới: Lấy URL Logo đội bóng ---
            # Lưu ý: Cần kiểm tra class CSS trên web, ví dụ: .home-logo, .away-logo
            logo_home = await el.evaluate("el => el.querySelector('.home-logo')?.src || ''")
            logo_away = await el.evaluate("el => el.querySelector('.away-logo')?.src || ''")

            # --- Xử lý giờ và làm sạch tiêu đề ---
            adjusted_name = adjust_time(raw_name)
            final_title = clean_title(adjusted_name)
            
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

        # 1. Xuất file IPTV
        with open("thiendinh_iptv.txt", "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for ch in match_data:
                group = "🔴 Live" if ch['stream'] else "🗓 Sắp diễn ra"
                f.write(f'#EXTINF:-1 group-title="{group}" tvg-logo="{ch["logo_home"]}",{ch["title"]}\n')
                if ch['stream']: f.write(f'{ch["stream"]}|Referer={ch["url"]}\n')
                else: f.write(f'#\n')

        # 2. Xuất file VLC
        with open("thiendinh_vlc.txt", "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for ch in match_data:
                f.write(f'#EXTINF:-1,{ch["title"]}\n{ch["stream"] if ch["stream"] else "#"}\n')

        # 3. Xuất file JSON (Bổ sung logo_home, logo_away)
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
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())