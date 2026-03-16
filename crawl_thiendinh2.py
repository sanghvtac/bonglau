import json
import asyncio
import re
from playwright.async_api import async_playwright
from datetime import datetime, timedelta

TARGET_URL = "https://sv1.thiendinh.live/lich-thi-dau/bong-da?by=state&value=live"

def clean_title(text):
    # 1. Chuẩn hóa thời gian (cộng 7h) và ép cách HH:MM DD/MM
    def adjust_time(match):
        t = datetime.strptime(match.group(1), "%H:%M")
        return (t + timedelta(hours=7)).strftime("%H:%M")
    text = re.sub(r'(\d{2}:\d{2})\s*(\d{2}/\d{2})', r'\1 \2', text)
    text = re.sub(r'(\d{2}:\d{2})', adjust_time, text)
    
    # 2. Tách BLV (lấy đoạn cuối)
    blv_match = re.search(r'(BLV.*)', text, flags=re.IGNORECASE)
    blv_str = f" {blv_match.group(1).strip()}" if blv_match else ""
    text_clean = re.sub(r'(BLV.*)', '', text, flags=re.IGNORECASE).strip()

    # 3. Loại bỏ giải đấu và rác
    leagues = ['Indian Super League', 'UEFA Champions League', 'UEFA Youth League', 'A-League', 'Liga I', 'First League', 'Premier League', 'Serie A', 'Bundesliga', 'La Liga', '1. Lig', 'V.League']
    for l in leagues:
        text_clean = re.sub(re.escape(l), '', text_clean, flags=re.IGNORECASE)
    
    # Loại bỏ: ●, Live, Sắp diễn ra, VS, tỷ số, ký tự đặc biệt
    text_clean = re.sub(r'(●|Live|Sắp diễn ra|Sắp bắt đầu|VS|H\d\s*-\s*\d+\'?|\d-\d|-)', ' ', text_clean, flags=re.IGNORECASE)
    
    # 4. Tìm thời gian và phần còn lại
    time_match = re.search(r'\d{2}:\d{2}\s*\d{2}/\d{2}', text_clean)
    time_str = time_match.group(0) if time_match else "00:00 00/00"
    content = text_clean.replace(time_str, "").strip()
    
    # 5. Dọn dẹp khoảng trắng dư thừa
    content = re.sub(r'\s+', ' ', content).strip()
    
    # Kết quả cuối: Giờ Ngày Tên Đội 1 Tên Đội 2 BLV Tên BLV
    return f"{time_str} {content}{blv_str}"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
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
            final_name = clean_title(raw_name)
            
            if not any(d['url'] == full_url for d in match_data):
                match_data.append({"title": final_name, "url": full_url, "stream": ""})

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

        # Xuất file IPTV
        with open("thiendinh_iptv.txt", "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for ch in match_data:
                group = "🔴 Live" if ch['stream'] else "🗓 Sắp diễn ra"
                f.write(f'#EXTINF:-1 group-title="{group}",{ch["title"]}\n')
                if ch['stream']: f.write(f'{ch["stream"]}|Referer={ch["url"]}\n')
                else: f.write(f'#\n')

        # Xuất file VLC
        with open("thiendinh_vlc.txt", "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for ch in match_data:
                f.write(f'#EXTINF:-1,{ch["title"]}\n{ch["stream"] if ch["stream"] else "#"}\n')

        # Xuất JSON
        json_output = {"name": "Thiên Đỉnh TV", "groups": [{"name": "🔴 Live", "channels": []}, {"name": "🗓 Sắp diễn ra", "channels": []}]}
        for ch in match_data:
            channel = {"name": ch['title'], "sources": [{"contents": [{"streams": [{"stream_links": [{"url": ch['stream'], "request_headers": [{"key": "Referer", "value": ch['url']}]}]}]}]}]}
            if ch['stream']: json_output["groups"][0]["channels"].append(channel)
            else: json_output["groups"][1]["channels"].append(channel)
        with open("thiendinh.json", "w", encoding="utf-8") as f:
            json.dump(json_output, f, ensure_ascii=False, indent=4)
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
