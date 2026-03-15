import json
import asyncio
from playwright.async_api import async_playwright
from datetime import datetime, timedelta

TARGET_URL = "https://sv1.thiendinh.live/lich-thi-dau/bong-da?by=state&value=live"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()

        print(f"--- Đang mở: {TARGET_URL} ---")
        await page.goto(TARGET_URL, wait_until="domcontentloaded")
        
        # Cuộn trang mạnh mẽ hơn để lấy đủ 32+ trận
        for _ in range(8):
            await page.mouse.wheel(0, 2000)
            await asyncio.sleep(1.5)
        
        # Lấy tất cả link và tên trận
        elements = await page.query_selector_all("a[href*='/xem-truc-tiep/']")
        match_data = []
        
        for el in elements:
            url = await el.get_attribute("href")
            full_url = "https://sv1.thiendinh.live" + url if url.startswith('/') else url
            name = await el.text_content()
            
            # Kiểm tra trùng lặp
            if not any(d['url'] == full_url for d in match_data):
                match_data.append({"title": name.strip(), "url": full_url, "stream": ""})

        print(f"--- Đã tìm thấy {len(match_data)} trận. Đang đào link stream... ---")

        # Đào link m3u8 (Chỉ đào cho trận cần thiết để tăng tốc)
        for item in match_data:
            m3u8_list = []
            def intercept(res):
                if ".m3u8" in res.url: m3u8_list.append(res.url)
            
            page.on("response", intercept)
            try:
                # Chỉ truy cập các trận có khả năng Live
                if "LIVE" in item['title'].upper() or "TRỰC TIẾP" in item['title'].upper():
                    await page.goto(item['url'], wait_until="domcontentloaded", timeout=10000)
                    await asyncio.sleep(2)
                    if m3u8_list: item['stream'] = max(m3u8_list, key=len)
            except: pass
            page.remove_listener("response", intercept)

        # --- XUẤT FILE ---
        # File IPTV (M3U) - Hiển thị đủ 32 trận
        with open("thiendinh_iptv.txt", "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for ch in match_data:
                group = "🔴 LIVE" if ch['stream'] else "🗓 Sắp diễn ra"
                f.write(f'#EXTINF:-1 group-title="{group}",{ch["title"]}\n')
                if ch['stream']:
                    f.write(f'{ch["stream"]}|Referer={ch["url"]}\n')
                else:
                    f.write(f'#\n')

        # File JSON - Theo chuẩn Bún Chả TV
        json_output = {"name": "Thiên Đỉnh TV", "groups": [{"name": "🔴 Live", "channels": []}]}
        for ch in match_data:
            if ch['stream']:
                json_output["groups"][0]["channels"].append({
                    "name": ch['title'],
                    "sources": [{"contents": [{"streams": [{"stream_links": [{"url": ch['stream'], "request_headers": [{"key": "Referer", "value": ch['url']}]}]}]}]}]
                })
        with open("thiendinh.json", "w", encoding="utf-8") as f:
            json.dump(json_output, f, ensure_ascii=False, indent=4)
        
        print("--- HOÀN TẤT ---")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
