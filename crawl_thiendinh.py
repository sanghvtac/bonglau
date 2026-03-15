import json
import re
import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

TARGET_URL = "https://sv1.thiendinh.live/lich-thi-dau/bong-da?by=state&value=live"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        
        # Vẫn chặn ảnh/media để nhanh, nhưng để lại file script/XHR
        await page.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "font"] else route.continue_())
        
        print(f"--- Đang mở: {TARGET_URL} ---")
        await page.goto(TARGET_URL, wait_until="domcontentloaded")
        
        # MẸO QUAN TRỌNG: Đợi thêm 5 giây để Javascript tải dữ liệu trận đấu
        await asyncio.sleep(5) 
        
        content = await page.content()
        soup = BeautifulSoup(content, 'html.parser')
        
        # Quét rộng hơn: tìm tất cả các thẻ có class liên quan đến trận đấu
        # Nếu web dùng dynamic loading, đôi khi dữ liệu nằm trong các thẻ div có class cụ thể
        elements = soup.find_all('a', href=re.compile(r'/xem-truc-tiep/'))
        
        match_data = []
        for el in elements:
            url = "https://sv1.thiendinh.live" + el['href'] if el['href'].startswith('/') else el['href']
            name = el.get_text(" ", strip=True)
            if not any(d['url'] == url for d in match_data):
                match_data.append({"url": url, "name": name, "stream_url": ""})
        
        print(f"--- Tìm thấy {len(match_data)} trận. Đang lấy link stream... ---")
        
        # ... (Phần lấy stream_url giữ nguyên như cũ)
        for item in match_data:
            if "LIVE" in item['name'].upper() or "TRỰC TIẾP" in item['name'].upper():
                try:
                    await page.goto(item['url'], wait_until="domcontentloaded", timeout=15000)
                    await asyncio.sleep(2) # Đợi trang chi tiết render
                    detail_content = await page.content()
                    links = re.findall(r'https?://[^\s"\'<>]+?\.m3u8[^\s"\'<>]*', detail_content)
                    if links:
                        item['stream_url'] = max(links, key=len)
                        print(f" ✅ {item['name']}: OK")
                except Exception as e:
                    print(f" ⚠️ Lỗi load {item['name']}: {e}")

        # ... (Phần xuất file giữ nguyên như cũ)
        with open("thiendinh.json", "w", encoding="utf-8") as f:
            json.dump(match_data, f, ensure_ascii=False, indent=4)
        
        with open("thiendinh_iptv.txt", "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for ch in match_data:
                if ch['stream_url']:
                    f.write(f'#EXTINF:-1 group-title="ThienDinhTV",{ch["name"]}\n{ch["stream_url"]}|Referer=https://sv1.thiendinh.live/\n')
        
        print("--- HOÀN TẤT ---")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
