import json
import re
import cloudscraper
from bs4 import BeautifulSoup

TARGET_URL = "https://sv1.thiendinh.live/lich-thi-dau/bong-da?by=state&value=live"
# Khởi tạo scraper với cấu hình giả lập trình duyệt mạnh hơn
scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False})

def main():
    print(f"--- Đang truy cập: {TARGET_URL} ---")
    try:
        response = scraper.get(TARGET_URL, timeout=30)
        print(f"Status Code: {response.status_code}")
        
        if response.status_code != 200:
            print("❌ Không thể truy cập website (Bị chặn hoặc lỗi).")
            return

        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Thử nhiều cách tìm link khác nhau để tránh web đổi cấu trúc
        elements = soup.find_all('a', href=re.compile(r'/xem-truc-tiep/'))
        
        match_data = []
        processed_links = set()

        for el in elements:
            url = el['href']
            if url.startswith('/'):
                url = "https://sv1.thiendinh.live" + url
            
            # Lấy text và làm sạch
            name = el.get_text(" ", strip=True)
            if url not in processed_links and name:
                processed_links.add(url)
                match_data.append({"url": url, "name": name, "stream_url": ""})

        print(f"--- Tìm thấy {len(match_data)} trận đấu ---")

        if len(match_data) == 0:
            print("⚠️ Cảnh báo: Không tìm thấy trận đấu nào. Có thể cấu hình HTML đã thay đổi.")

        for item in match_data:
            name_upper = item['name'].upper()
            if any(x in name_upper for x in ["LIVE", "TRỰC TIẾP", "ĐANG ĐÁ"]):
                try:
                    detail_resp = scraper.get(item['url'], timeout=20)
                    links = re.findall(r'https?://[^\s"\'<>]+?\.m3u8[^\s"\'<>]*', detail_resp.text)
                    if links:
                        item['stream_url'] = max(links, key=len)
                        print(f" ✅ {item['name']}: OK")
                    else:
                        print(f" ⚠️ {item['name']}: Không tìm thấy m3u8")
                except:
                    print(f" ❌ Lỗi truy cập trận: {item['name']}")
            else:
                print(f" ℹ️ {item['name']}: Sắp đá")

        # Ghi file (Đảm bảo luôn ghi dù rỗng để tránh lỗi Git)
        with open("thiendinh.json", "w", encoding="utf-8") as f:
            json.dump(match_data, f, ensure_ascii=False, indent=4)

        with open("thiendinh_vlc.txt", "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for ch in match_data:
                if ch['stream_url']:
                    f.write(f'#EXTINF:-1,{ch["name"]}\n{ch["stream_url"]}\n')

        with open("thiendinh_iptv.txt", "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for ch in match_data:
                if ch['stream_url']:
                    f.write(f'#EXTINF:-1 group-title="ThienDinhTV",{ch["name"]}\n{ch["stream_url"]}|Referer=https://sv1.thiendinh.live/\n')
                else:
                    f.write(f'#EXTINF:-1 group-title="Sắp diễn ra",{ch["name"]}\n#\n')

        print("--- ĐÃ CẬP NHẬT FILE THÀNH CÔNG ---")

    except Exception as e:
        print(f"Lỗi phát sinh: {e}")

if __name__ == "__main__":
    main()
