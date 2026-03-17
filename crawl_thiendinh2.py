import json
import asyncio
import re
from playwright.async_api import async_playwright
from datetime import datetime

TARGET_URL = "https://sv1.thiendinh.live/lich-thi-dau/bong-da?by=state&value=live"

def clean_title(text):
    text = re.sub(r'(\d{2}:\d{2})\s*(\d{2}/\d{2})', r'\1 \2', text)
    blv_match = re.search(r'(BLV.*)', text, flags=re.IGNORECASE)
    blv_str = f" {blv_match.group(1).strip()}" if blv_match else ""
    text_clean = re.sub(r'(BLV.*)', '', text, flags=re.IGNORECASE).strip()

    leagues = ['Asian Cup Women', 'Indian Super League', 'UEFA Champions League', 'UEFA Youth League', 'A-League', 'K League 1', 'Liga I', 'First League', 'Premier League', 'Serie A', 'Bundesliga', 'La Liga', 'V.League']
    for l in leagues:
        text_clean = re.sub(re.escape(l), '', text_clean, flags=re.IGNORECASE)
    
    text_clean = re.sub(r'(●|Live|Sắp diễn ra|Sắp bắt đầu|VS|H\d\s*-\s*\d+\'?|\d-\d|-)', ' ', text_clean, flags=re.IGNORECASE)
    time_match = re.search(r'\d{2}:\d{2}\s*\d{2}/\d{2}', text_clean)
    time_str = time_match.group(0) if time_match else ""
    content = text_clean.replace(time_str, "").strip()

    # Logic tách đội
    content = re.sub(r'([a-z])([A-Z])', r'\1 VS \2', content)
    content = re.sub(r'(\d)([A-Z])', r'\1 VS \2', content)
    if ' VS ' not in content:
        content = re.sub(r'([A-Za-z])(\d)', r'\1 VS \2', content)

    content = re.sub(r'\s+', ' ', content).strip()
    return f"{time_str} {content}{blv_str}", content

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(TARGET_URL, wait_until="networkidle")
        
        for _ in range(5):
            await page.mouse.wheel(0, 1500)
            await asyncio.sleep(0.5)
        
        elements = await page.query_selector_all("a[href*='/xem-truc-tiep/']")
        match_data = []

        for el in elements:
            url = await el.get_attribute("href")
            full_url = "https://sv1.thiendinh.live" + url if url.startswith('/') else url
            raw_name = (await el.text_content()).strip()
            
            # --- LẤY 2 LOGO ---
            imgs = await el.query_selector_all("img")
            logos = []
            for img in imgs:
                src = await img.get_attribute("data-src") or await img.get_attribute("src")
                if src and "http" in src and "30aaqin.png" not in src:
                    logos.append(src)
            
            logo_a = logos[0] if len(logos) >= 1 else ""
            logo_b = logos[1] if len(logos) >= 2 else ""

            full_title, teams_only = clean_title(raw_name)
            t_split = teams_only.split(" VS ")
            team_a = t_split[0].strip() if len(t_split) > 0 else ""
            team_b = t_split[1].strip() if len(t_split) > 1 else ""

            match_data.append({
                "title": full_title,
                "team_a": team_a,
                "team_b": team_b,
                "url": full_url,
                "logo_a": logo_a,
                "logo_b": logo_b,
                "stream": ""
            })

        for item in match_data:
            if "LIVE" in item['title'].upper():
                m3u8_list = []
                page.on("response", lambda res: m3u8_list.append(res.url) if ".m3u8" in res.url else None)
                try:
                    await page.goto(item['url'], timeout=6000)
                    await asyncio.sleep(2)
                    if m3u8_list: item['stream'] = max(m3u8_list, key=len)
                except: pass

        # --- CẤU TRÚC JSON THEO MẪU APP TV ---
        json_output = {"name": "Thiên Đỉnh TV", "groups": [{"name": "🔴 Live", "channels": []}, {"name": "🗓 Sắp diễn ra", "channels": []}]}
        
        for ch in match_data:
            channel = {
                "name": ch['title'],
                "image": {"url": ch['logo_a']}, # App thường dùng logo đội 1 làm ảnh nền
                "sources": [{
                    "contents": [{
                        "streams": [{
                            "stream_links": [{
                                "url": ch['stream'] if ch['stream'] else "",
                                "request_headers": [{"key": "Referer", "value": ch['url']}]
                            }]
                        }]
                    }]
                }],
                "org_metadata": {
                    "team_a": ch['team_a'],
                    "team_b": ch['team_b'],
                    "logo_a": ch['logo_a'],
                    "logo_b": ch['logo_b']
                }
            }
            if ch['stream']: json_output["groups"][0]["channels"].append(channel)
            else: json_output["groups"][1]["channels"].append(channel)

        with open("thiendinh.json", "w", encoding="utf-8") as f:
            json.dump(json_output, f, ensure_ascii=False, indent=4)
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
