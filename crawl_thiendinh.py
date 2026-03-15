import json
import re
import cloudscraper
from bs4 import BeautifulSoup

TARGET_URL = "https://sv1.thiendinh.live/lich-thi-dau/bong-da?by=state&value=live"
scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False})

def main():
    print(f"--- Đang truy cập: {TARGET_URL} ---")
    try:
        response = scraper.get(TARGET_URL, timeout=30)
        
        if response.status_code != 200:
            print(f"❌ Lỗi truy cập. Status: {response.status_code}")
            return

        soup = BeautifulSoup(response.text, 'html.parser')
        
        # SỬA ĐỔI CHÍNH: Tìm tất cả thẻ <a> có link chứa "/xem-truc-tiep/" 
        # bất kể nó nằm trong thẻ div hay li nào
        elements = soup.find_all('a', href=True)
        
        match_data = []
        processed_links = set()

        for el in elements:
            url = el['href']
            # Kiểm tra nếu link chứa từ khóa trực tiếp
            if "/xem-truc-tiep/" in url:
                if url.startswith('/'):
                    url = "https://sv1.thiendinh.live" + url
                
                # Lấy text hiển thị (tên trận/giờ)
                name = el.get_text(" ", strip=True)
                
                # Nếu text rỗng, thử tìm trong các thẻ con hoặc thuộc tính title
                if not name:
                    name = el.get('title', '')
                
                if url not in processed_links and name:
                    processed_links.add(url)
                    match_data.append({"url": url, "name": name, "stream_url": ""})

        # BỔ SUNG: Nếu vẫn không tìm thấy, quét toàn bộ văn bản để tìm link thô
        if not match_data:
            print("⚠️ Chưa tìm thấy bằng thẻ A, đang thử quét link thô...")
            raw_links = re.findall(r'/xem-truc-tiep/[a-zA-Z0-9\-\.]+', response.text)
            for r_link in set(raw_links):
                full_url = "https://sv1.thiendinh.live" + r_link
                match_data.append({"url": full_url, "name": "Trận đấu chưa rõ tên", "stream_url": ""})

        print(f"--- Tìm thấy {len(match_data)} trận đấu ---")

        for item in match_data:
            # Kiểm tra trạng thái trực tiếp
            is_live = any(x in item['name'].upper() for x in ["LIVE", "TRỰC TIẾP", "ĐANG ĐÁ"])
            
            # Mẹo: Nếu tên trận là "Trận đấu chưa rõ tên", cứ thử đào link xem sao
            if is_live or item['name'] == "Trận đấu chưa rõ tên":
                try:
                    detail_resp = scraper.get(item['url'], timeout=20)
                    links = re.findall(r'https?://[^\s"\'<>]+?\.m3u8[^\s"\'<>]*', detail_resp.text)
                    if links:
                        item['stream_url'] = max(links, key=len)
                        # Cố gắng lấy lại tên trận từ trang chi tiết nếu ở trang lịch bị rỗng
                        if item['name'] == "Trận đấu chưa rõ tên":
                            detail_soup = BeautifulSoup(detail_resp.text, 'html.parser')
                            item['name'] = detail_soup.title.string.split('|')[0].strip() if detail_soup.title else item['name']
                        print(f" ✅ {item['name']}: OK")
                    else:
                        print(f" ⚠️ {item['name']}: Không thấy m3u8")
                except:
                    print(f" ❌ Lỗi: {item['name']}")
            else:
                print(f" ℹ️ {item['name']}: Sắp đá")

        # GHI FILE
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

        print("--- HOÀN TẤT ---")

    except Exception as e:
        print(f"Lỗi: {e}")

if __name__ == "__main__":
    main()
