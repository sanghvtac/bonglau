import json
import asyncio
import re
import hashlib
import base64
import requests
from io import BytesIO
from PIL import Image
from playwright.async_api import async_playwright

TARGET_URL = "https://sv1.thiendinh.live/lich-thi-dau/bong-da?by=state&value=live"

def generate_id(text):
    return hashlib.md5(text.encode()).hexdigest()[:12]

def get_base64_combined_image(logo_a_url, logo_b_url):
    """Tải logo, ghép lại và trả về chuỗi Base64"""
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        # Tạo khung ảnh 450x200 nền xám nhạt
        combined = Image.new("RGBA", (450, 200), (236, 236, 236, 255))
        
        # Tải logo A
        if logo_a_url:
            try:
                # Dùng weserv để ép về PNG vì Pillow không đọc trực tiếp được SVG
                url_a = f"https://images.weserv.nl/?url={logo_a_url}&w=200&h=200&fit=contain&output=png"
                res_a = requests.get(url_a, headers=headers, timeout=10)
                img_a = Image.open(BytesIO(res_a.content)).convert("RGBA")
                combined.paste(img_a, (20, 0), img_a)
            except: pass

        # Tải logo B
        if logo_b_url:
            try:
                url_b = f"https://images.weserv.nl/?url={logo_b_url}&w=200&h=200&fit=contain&output=png"
                res_b = requests.get(url_b, headers=headers, timeout=10)
                img_b = Image.open(BytesIO(res_b.content)).convert("RGBA")
                combined.paste(img_b, (230, 0), img_b)
            except: pass
            
        buffered = BytesIO()
        combined.save(buffered, format="PNG")
        img_str = base64.b64encode(buffered.getvalue()).decode()
        return f"data:image/png;base64,{img_str}"
    except Exception as e:
        print(f"Lỗi ghép ảnh: {e}")
        return ""

def clean_title(text):
    is_live_origin = any(word in text.upper() for word in ["LIVE", "●"])
    text = re.sub(r'(\d{2}:\d{2})\s*(\d{2}/\d{2})', r'\1 \2', text)
    blv_match = re.search(r'(BLV.*)', text, flags=re.IGNORECASE)
    blv_str = f" {blv_match.group(1).strip()}" if blv_match else ""
    text_clean = re.sub(r'(BLV.*)', '', text, flags=re.IGNORECASE).strip()
    text_clean = re.sub(r'(●|Live|Sắp diễn ra|Sắp bắt đầu|VS|H\d\s*-\s*\d+\'?|\d-\d|-)', ' ', text_clean, flags=re.IGNORECASE)
    time_match = re.search(r'\d{2}:\d{2}\s*\d{2}/\d{2}', text_clean)
    time_str = time_match.group(0) if time_match else ""
    content = text_clean.replace(time_str, "").strip()
    content = re.sub(r'([a-z])([A-Z])', r'\1 VS \2', content)
    content = re.sub(r'(\d)([A-Z])', r'\1 VS \2', content)
    if ' VS ' not in content:
        content = re.sub(r'([a-zA-Z])(\d)', r'\1 VS \2', content)
    content = re.sub(r'\s+', ' ', content).strip()
    return f"{time_str} {content}{blv_str}", content, is_live_origin

async def main():
    async with async_playwright() as p:
        # Launch browser với timeout mặc định cao hơn
        browser = await p.chromium.launch()
        context = await browser.new_context(user_agent="Mozilla/5.0")
        page = await context.new_page()
        page.set_default_timeout(60000) # Tăng lên 60 giây cho an toàn
        
        print("Đang truy cập trang web...")
        try:
            # Thay đổi sang 'domcontentloaded' để tránh treo do quảng cáo
            await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"Lỗi tải trang: {e}")
            await browser.close()
            return

        # Cuộn trang để tải hết dữ liệu
        for _ in range(5):
            await page.mouse.wheel(0, 2000)
            await asyncio.sleep(1)
        
        elements = await page.query_selector_all("a[href*='/xem-truc-tiep/']")
        match_data = []

        print(f"Tìm thấy {len(elements)} trận đấu. Đang xử lý ảnh...")
        for el in elements:
            url = await el.get_attribute("href")
            full_url = "https://sv1.thiendinh.live" + url if url.startswith('/') else url
            raw_text = (await el.text_content()).strip()
            full_title, teams_only, is_live = clean_title(raw_text)

            imgs = await el.query_selector_all("img")
            logos = []
            for img in imgs:
                src = await img.get_attribute("data-src") or await img.get_attribute("src")
                if src and "http" in src and "30aaqin.png" not in src:
                    logos.append(src)
            
            logo_a = logos[0] if len(logos) >= 1 else ""
            logo_b = logos[1] if len(logos) >= 2 else ""

            # Ghép ảnh trực tiếp vào Base64
            combined_img = get_base64_combined_image(logo_a, logo_b)

            t_split = teams_only.split(" VS ")
            match_data.append({
                "title": full_title,
                "team_a": t_split[0].strip() if len(t_split) > 0 else "",
                "team_b": t_split[1].strip() if len(t_split) > 1 else "",
                "url": full_url,
                "logo_a": logo_a,
                "logo_b": logo_b,
                "combined_img": combined_img,
                "is_live": is_live,
                "stream": ""
            })

        print("Đang lấy link stream cho các trận LIVE...")
        for item in match_data:
            if item['is_live']:
                m3u8_list = []
                async def handle_response(response):
                    if ".m3u8" in response.url: m3u8_list.append(response.url)
                page.on("response", handle_response)
                try:
                    await page.goto(item['url'], wait_until="domcontentloaded", timeout=15000)
                    await asyncio.sleep(4)
                    if m3u8_list: item['stream'] = max(m3u8_list, key=len)
                except: pass
                page.remove_listener("response", handle_response)

        json_output = {"name": "Thiên Đỉnh TV", "groups": [{"id": "live", "name": "🔴 Live", "channels": []}, {"id": "upcoming", "name": "🗓 Sắp diễn ra", "channels": []}]}
        
        for ch in match_data:
            match_id = generate_id(ch['url'])
            channel = {
                "id": f"ch-{match_id}",
                "name": f"⚽ {ch['title']}",
                "type": "single",
                "display": "thumbnail-only",
                "image": {
                    "url": ch['combined_img'] if ch['combined_img'] else ch['logo_a'],
                    "display": "contain",
                    "padding": 1,
                    "background_color": "#ececec"
                },
                "sources": [{
                    "id": f"src-{match_id}",
                    "contents": [{
                        "id": f"ct-{match_id}",
                        "streams": [{
                            "stream_links": [{
                                "url": ch['stream'] if ch['stream'] else "",
                                "type": "hls",
                                "request_headers": [{"key": "Referer", "value": ch['url']}, {"key": "User-Agent", "value": "Mozilla/5.0"}]
                            }]
                        }]
                    }]
                }]
            }
            if ch['stream'] or ch['is_live']: json_output["groups"][0]["channels"].append(channel)
            else: json_output["groups"][1]["channels"].append(channel)

        with open("thiendinh.json", "w", encoding="utf-8") as f:
            json.dump(json_output, f, ensure_ascii=False, indent=4)
        
        print("Đã hoàn thành! File: thiendinh.json")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
