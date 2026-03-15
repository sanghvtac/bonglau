import json
import asyncio
from playwright.async_api import async_playwright

TARGET_URL = "https://sv1.thiendinh.live/lich-thi-dau/bong-da?by=state&value=live"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()

        # Chặn hình ảnh/media để load cực nhanh
        await page.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "font", "media"] else route.continue_())

        print(f"--- Đang mở: {TARGET_URL} ---")
        await page.goto(TARGET_URL, wait_until="domcontentloaded")
        
        # Cuộn trang để đảm bảo load hết 32 trận
        for _ in range(8):
            await page.mouse.wheel(0, 2000)
            await asyncio.sleep(1)
        
        # Lấy danh sách thô (URL và Tên)
        elements = await page.query_selector_all("a[href*='/xem-truc-tiep/']")
        match_data = []
        for el in elements:
            url = await el.get_attribute("href")
            full_url = "https://sv1.thiendinh.live" + url if url.startswith('/') else url
            name = (await el.text_content()).strip()
            if not any(d['url'] == full_url for d in match_data):
                match_data.append({"title": name, "url": full_url, "stream": ""})

        print(f"--- Tìm thấy {len(match_data)} trận. Đang đào link stream... ---")

        # Chỉ đào link cho các trận đang Live để tiết kiệm thời gian
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

        # 1. XUẤT FILE IPTV (M3U) - Đủ 32 trận
        with open("thiendinh_iptv.txt", "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for ch in match_data:
                group = "🔴 Live" if ch['stream'] else "🗓 Sắp diễn ra"
                f.write(f'#EXTINF:-1 group-title="{group}",{ch["title"]}\n')
                if ch['stream']: f.write(f'{ch["stream"]}|Referer={ch["url"]}\n')
                else: f.write(f'#\n')

        # 2. XUẤT FILE VLC (Định dạng đơn giản)
        with open("thiendinh_vlc.txt", "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for ch in match_data:
                if ch['stream']:
                    f.write(f'#EXTINF:-1,{ch["title"]}\n{ch["stream_url"] if "stream_url" in ch else ch["stream"]}\n')
                else:
                    f.write(f'#EXTINF:-1,{ch["title"]}\n#\n')

        # 3. XUẤT FILE JSON (Chuẩn Bún Chả TV)
        json_output = {"name": "Thiên Đỉnh TV", "groups": [{"name": "🔴 Live", "channels": []}, {"name": "🗓 Sắp diễn ra", "channels": []}]}
        for ch in match_data:
            channel = {
                "name": ch['title'],
                "sources": [{"contents": [{"streams": [{"stream_links": [{"url": ch['stream'], "request_headers": [{"key": "Referer", "value": ch['url']}]}]}]}]}]
            }
            if ch['stream']: json_output["groups"][0]["channels"].append(channel)
            else: json_output["groups"][1]["channels"].append(channel)
        with open("thiendinh.json", "w", encoding="utf-8") as f:
            json.dump(json_output, f, ensure_ascii=False, indent=4)
        
        print("--- HOÀN TẤT: Đã tạo 3 file đầy đủ ---")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
