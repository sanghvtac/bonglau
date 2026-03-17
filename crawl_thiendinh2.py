import json
import asyncio
import re
from playwright.async_api import async_playwright
from datetime import datetime

TARGET_URL = "https://sv1.thiendinh.live/lich-thi-dau/bong-da?by=state&value=live"

def clean_title(text):
    # 1. Chuẩn hóa thời gian (Giữ nguyên giờ VN từ web)
    text = re.sub(r'(\d{2}:\d{2})\s*(\d{2}/\d{2})', r'\1 \2', text)
    
    # 2. Tách BLV
    blv_match = re.search(r'(BLV.*)', text, flags=re.IGNORECASE)
    blv_str = f" {blv_match.group(1).strip()}" if blv_match else ""
    text_clean = re.sub(r'(BLV.*)', '', text, flags=re.IGNORECASE).strip()

    # 3. Lọc giải đấu
    leagues = ['Asian Cup Women', 'Indian Super League', 'UEFA Champions League', 'UEFA Youth League', 'A-League', 'K League 1', 'Liga I', 'First League', 'Premier League', 'Serie A', 'Bundesliga', 'La Liga', 'V.League']
    for l in leagues:
        text_clean = re.sub(re.escape(l), '', text_clean, flags=re.IGNORECASE)
    
    text_clean = re.sub(r'(●|Live|Sắp diễn ra|Sắp bắt đầu|VS|H\d\s*-\s*\d+\'?|\d-\d|-)', ' ', text_clean, flags=re.IGNORECASE)
    
    time_match = re.search(r'\d{2}:\d{2}\s*\d{2}/\d{2}', text_clean)
    time_str = time_match.group(0) if time_match else ""
    content = text_clean.replace(time_str, "").strip()

    # 4. LOGIC TÁCH ĐỘI (Sửa lỗi dính chữ và U19/U21)
    # Tách khi: Chữ thường dính Chữ hoa (ArsenalBayer -> Arsenal VS Bayer)
    content = re.sub(r'([a-z])([A-Z])', r'\1 VS \2', content)
    # Tách khi: Chữ số dính Chữ hoa (U19Club -> U19 VS Club)
    content = re.sub(r'(\d)([A-Z])', r'\1 VS \2', content)
    # Tách khi: Chữ cái dính Số (VillarrealU19 -> Villarreal VS U19) - Chỉ nếu chưa có VS
    if ' VS ' not in content:
        content = re.sub(r'([A-Za-z])(\d)', r'\1 VS \2', content)

    content = re.sub(r'\s+', ' ', content).strip()
    
    # Đảm bảo có VS ở giữa
    if ' VS ' not in content:
        words = content.split()
        if len(words) >= 2:
            mid = len(words) // 2
            content = f"{' '.join(words[:mid])} VS {' '.join(words[mid:])}"

    return f"{time_str} {content}{blv_str}"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(TARGET_URL, wait_until="domcontentloaded")
        
        for _ in range(5):
            await page.mouse.wheel(0, 2000)
            await asyncio.sleep(1)
        
        elements = await page.query_selector_all("a[href*='/xem-truc-tiep/']")
        match_data = []

        for el in elements:
            url = await el.get_attribute("href")
            full_url = "https://sv1.thiendinh.live" + url if url.startswith('/') else url
            raw_name = (await el.text_content()).strip()
            
            imgs = await el.query_selector_all("img")
            logo_main = ""
            for img in imgs:
                src = await img.get_attribute("src")
                if src and "http" in src:
                    logo_main = src
                    break

            match_data.append({
                "title": clean_title(raw_name),
                "url": full_url,
                "logo": logo_main,
                "stream": ""
            })

        for item in match_data:
            if any(word in item['title'].upper() for word in ["LIVE", "TRỰC TIẾP"]):
                m3u8_list = []
                page.on("response", lambda res: m3u8_list.append(res.url) if ".m3u8" in res.url else None)
                try:
                    await page.goto(item['url'], timeout=6000)
                    await asyncio.sleep(2)
                    if m3u8_list: item['stream'] = max(m3u8_list, key=len)
                except: pass

        # --- CẤU TRÚC JSON TỐI ƯU CHO APP TV ---
        json_output = {
            "name": "Thiên Đỉnh TV",
            "groups": [
                {
                    "name": "🔴 Live",
                    "channels": []
                },
                {
                    "name": "🗓 Sắp diễn ra",
                    "channels": []
                }
            ]
        }

        for ch in match_data:
            # Cấu trúc channel tinh gọn nhưng đầy đủ các cấp độ stream chuẩn
            channel = {
                "name": ch['title'],
                "logo": ch['logo'],
                "sources": [
                    {
                        "contents": [
                            {
                                "streams": [
                                    {
                                        "stream_links": [
                                            {
                                                "url": ch['stream'] if ch['stream'] else "",
                                                "request_headers": [
                                                    {
                                                        "key": "Referer",
                                                        "value": ch['url']
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
            
            if ch['stream']:
                json_output["groups"][0]["channels"].append(channel)
            else:
                json_output["groups"][1]["channels"].append(channel)

        with open("thiendinh.json", "w", encoding="utf-8") as f:
            json.dump(json_output, f, ensure_ascii=False, indent=4)
        
        # File IPTV cho mục đích dự phòng
        with open("thiendinh_iptv.txt", "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for ch in match_data:
                f.write(f'#EXTINF:-1 group-title="{"🔴 Live" if ch["stream"] else "🗓 Sắp diễn ra"}" tvg-logo="{ch["logo"]}",{ch["title"]}\n')
                f.write(f'{ch["stream"] if ch["stream"] else "#"}|Referer={ch["url"]}\n')

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
