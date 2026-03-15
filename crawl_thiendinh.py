import json
import time
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By

# Thay đổi đường dẫn đến trang lịch thi đấu
TARGET_URL = "https://sv1.thiendinh.live/lich-thi-dau/bong-da?by=state&value=live"

def get_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)

def extract_best_m3u8(driver):
    logs = driver.get_log("performance")
    best_url = ""
    for entry in logs:
        try:
            log_msg = json.loads(entry["message"])["message"]
            if log_msg["method"] == "Network.requestWillBeSent":
                url = log_msg["params"]["request"]["url"]
                if ".m3u8" in url and len(url) > len(best_url):
                    best_url = url
        except: continue
    return best_url

def main():
    driver = get_driver()
    print(f"--- Đang quét toàn bộ lịch thi đấu: {TARGET_URL} ---")
    driver.get(TARGET_URL)
    time.sleep(5)

    # Tìm các phần tử chứa thông tin trận đấu (thường là các thẻ li hoặc div)
    # Lưu ý: Cần điều chỉnh XPath nếu website thay đổi giao diện
    match_elements = driver.find_elements(By.XPATH, "//a[contains(@href, '/xem-truc-tiep/')]")
    
    match_data = []
    # Lưu danh sách link tạm để tránh quét trùng
    processed_links = []

    for el in match_elements:
        url = el.get_attribute("href")
        name = el.text.replace("\n", " ").strip()
        
        if url not in processed_links:
            processed_links.append(url)
            match_data.append({"url": url, "name": name, "stream_url": ""})

    print(f"Tìm thấy {len(match_data)} trận đấu. Đang kiểm tra link...")

    # Duyệt qua từng trận để đào link stream
    for item in match_data:
        print(f"-> Xử lý: {item['name']}")
        # Chỉ những trận có chữ 'LIVE' hoặc đang trong giờ đá mới đào link
        if "LIVE" in item['name'].upper() or "TRỰC TIẾP" in item['name'].upper():
            driver.get(item['url'])
            time.sleep(10) # Đợi load
            stream = extract_best_m3u8(driver)
            if stream:
                item['stream_url'] = stream
                print("   ✅ Đã lấy link stream.")
            else:
                print("   ⚠️ Trận này chưa có link hoặc link bị ẩn.")
        else:
            print("   ℹ️ Trận sắp diễn ra, bỏ qua đào link.")

    # Xuất file JSON
    with open("thiendinh.json", "w", encoding="utf-8") as f:
        json.dump(match_data, f, ensure_ascii=False, indent=4)

    # Xuất file TXT cho VLC (Bỏ hậu tố)
    with open("thiendinh_vlc.txt", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for ch in match_data:
            if ch['stream_url']:
                f.write(f'#EXTINF:-1,{ch["name"]}\n{ch["stream_url"]}\n')

    # Xuất file TXT cho IPTV (Có hậu tố)
    with open("thiendinh_iptv.txt", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for ch in match_data:
            if ch['stream_url']:
                f.write(f'#EXTINF:-1 group-title="ThienDinhTV",{ch["name"]}\n{ch["stream_url"]}|Referer=https://sv1.thiendinh.live/\n')
            else:
                # Vẫn liệt kê trận sắp đá vào list nhưng không có link
                f.write(f'#EXTINF:-1 group-title="ThienDinhTV (Sắp diễn ra)",{ch["name"]}\n#\n')

    print("\n--- ĐÃ HOÀN THÀNH ---")
    driver.quit()

if __name__ == "__main__":
    main()
