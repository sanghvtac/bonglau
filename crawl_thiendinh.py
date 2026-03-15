import json
import asyncio
from playwright.async_api import async_playwright

TARGET_URL = "https://sv1.thiendinh.live/lich-thi-dau/bong-da?by=state&value=live"

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch() # Chạy không giao diện (headless)
        page = await browser.new_page()
        
        # Giả lập User-Agent của trình duyệt thật
        await page.set_extra_http_headers({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"})
        
        print(f"--- Đang mở: {TARGET_URL} ---")
        await page.goto(TARGET_URL, wait_until="networkidle") # Đợi trang tải xong
        
        # Lấy nội dung trang sau khi đã chạy JS
        content = await page.content()
        print("--- Đã lấy nội dung trang ---")
        
        # (Ở đây bạn có thể dùng BeautifulSoup để parse content như cũ)
        # Vì GitHub không có Selenium, chúng ta dùng Playwright để "render" nội dung
        # Rồi dùng BeautifulSoup lọc link
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run())
