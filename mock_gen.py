import json
import random
import datetime
from pocketbase import PocketBase  # pip install pocketbase

# --- KONFİGÜRASYON ---
PB_URL = "http://127.0.0.1:8090"  # PocketBase adresiniz
ADMIN_EMAIL = "admin@example.com" # Admin giriş bilgileriniz
ADMIN_PASS = "1234567890"

# Simülasyon Ayarları
DAYS_BACK = 30           # Kaç günlük veri üretilsin?
OFFICE_START_HOUR = 8    # Mesai başlangıcı
OFFICE_END_HOUR = 19     # Mesai bitişi
READING_INTERVAL_MIN = 10 # Sensör okuma sıklığı (dakika)

def generate_mock_data():
    client = PocketBase(PB_URL)
    
    try:
        # 1. Admin Girişi
        print(f"🔌 {PB_URL} adresine bağlanılıyor...")
        client.admins.auth_with_password(ADMIN_EMAIL, ADMIN_PASS)
        print("✅ Giriş başarılı.")

        # 2. Mevcut Verileri Çek (ID'leri kullanmak için)
        print("📦 Odalar ve kullanıcılar çekiliyor...")
        places = client.collection("places").get_full_list()
        users = client.collection("users").get_full_list()

        if not places:
            print("❌ HATA: Hiç 'places' (oda) kaydı bulunamadı. Önce oda ekleyin.")
            return
        if not users:
            print("❌ HATA: Hiç 'users' (kullanıcı) kaydı bulunamadı.")
            return

        mock_reservations = []
        mock_readings = []
        
        start_date = datetime.datetime.now() - datetime.timedelta(days=DAYS_BACK)
        end_date = datetime.datetime.now()

        print(f"🚀 Simülasyon başlıyor ({DAYS_BACK} gün geriye dönük)...")

        # Her oda için döngü
        for place in places:
            print(f"   👉 {place.name} için veriler üretiliyor...")
            
            current_sim_time = start_date
            
            while current_sim_time < end_date:
                # Sadece mesai saatlerinde işlem yap
                if OFFICE_START_HOUR <= current_sim_time.hour < OFFICE_END_HOUR:
                    
                    # --- A. REZERVASYON OLUŞTURMA (Rastgelelik: %20 şans) ---
                    # Eğer şu an bir rezervasyonun içinde değilsek ve şans tutarsa
                    active_res = next((r for r in mock_reservations if r['place_id'] == place.id and r['_start_obj'] <= current_sim_time < r['_end_obj']), None)
                    
                    if not active_res and random.random() < 0.2:
                        duration_hours = random.choice([1, 1.5, 2, 3])
                        res_end_time = current_sim_time + datetime.timedelta(hours=duration_hours)
                        
                        # Mesai bitişini aşmasın
                        if res_end_time.hour >= OFFICE_END_HOUR:
                            res_end_time = current_sim_time.replace(hour=OFFICE_END_HOUR, minute=0)

                        attendee_count = random.randint(1, place.capacity if hasattr(place, 'capacity') else 5)
                        
                        reservation = {
                            "place_id": place.id,
                            "user_id": random.choice(users).id,
                            "start_ts": current_sim_time.isoformat(),
                            "end_ts": res_end_time.isoformat(),
                            "status": "completed",
                            "is_hidden": True, # Geçmiş veri olduğu için gizli
                            "attendee_count": attendee_count,
                            
                            # Yardımcı objeler (JSON'a dahil edilmeyecek)
                            "_start_obj": current_sim_time,
                            "_end_obj": res_end_time,
                            "_attendees": attendee_count
                        }
                        mock_reservations.append(reservation)
                        active_res = reservation # Şu an rezerve edildi

                    # --- B. SENSÖR VERİSİ OLUŞTURMA ---
                    # Temel Değerler (Boş Oda)
                    co2 = 400 + random.uniform(-10, 20)
                    temp = 22.0 + random.uniform(-0.5, 0.5)
                    rh = 45.0 + random.uniform(-2, 2)
                    voc = 50 + random.uniform(0, 10)
                    pir = False
                    
                    # Eğer aktif bir rezervasyon varsa değerleri yükselt
                    if active_res:
                        people = active_res["_attendees"]
                        
                        # İnsan sayısı kadar CO2 ve Isı artışı
                        # Basit fizik: Her insan CO2'yi artırır
                        co2_boost = people * 150 # Kişi başı ppm katkısı (simüle)
                        temp_boost = people * 0.3
                        
                        co2 = 400 + co2_boost + random.uniform(-50, 50)
                        temp = 22.0 + temp_boost + random.uniform(-0.2, 0.2)
                        voc = 100 + (people * 20) + random.uniform(0, 30)
                        
                        # Hareket sensörü: %90 ihtimalle hareket var
                        pir = random.random() < 0.9

                    # Konfor Skoru Hesapla (Basit algoritma)
                    # İdeal: 22C, 400ppm. Fark arttıkça skor düşer.
                    temp_diff = abs(temp - 22.0)
                    co2_diff = max(0, co2 - 600) # 600'e kadar tolerans
                    
                    score = 100 - (temp_diff * 5) - (co2_diff / 20)
                    score = max(0, min(100, score)) # 0-100 arası tut

                    readings = {
                        "place_id": place.id,
                        "recorded_at": current_sim_time.isoformat().replace("T", " "),
                        "pir_occupied": pir,
                        "temp_c": round(temp, 2),
                        "rh_percent": round(rh, 2),
                        "voc_index": int(voc),
                        "co2_ppm": int(co2),
                        "comfort_score": int(score)
                    }
                    mock_readings.append(readings)

                # Zamanı ilerlet
                current_sim_time += datetime.timedelta(minutes=READING_INTERVAL_MIN)

        # 3. Dosyaları Kaydet (Helper key'leri temizleyerek)
        print("💾 Dosyalar kaydediliyor...")
        
        # Helper key'leri temizle
        final_reservations = []
        for r in mock_reservations:
            r_copy = r.copy()
            del r_copy["_start_obj"]
            del r_copy["_end_obj"]
            del r_copy["_attendees"]
            final_reservations.append(r_copy)

        with open('mock_reservations.json', 'w', encoding='utf-8') as f:
            json.dump(final_reservations, f, indent=2, default=str)
            
        with open('mock_readings.json', 'w', encoding='utf-8') as f:
            json.dump(mock_readings, f, indent=2, default=str)

        print(f"✨ TAMAMLANDI!\n   🔹 {len(mock_reservations)} rezervasyon üretildi -> mock_reservations.json\n   🔹 {len(mock_readings)} sensör okuması üretildi -> mock_readings.json")
        print("\nŞimdi PocketBase Admin paneline gidip bu JSON dosyalarını ilgili koleksiyonlara 'Import' edebilirsiniz.")

    except Exception as e:
        print(f" Bir hata oluştu: {e}")

if __name__ == "__main__":
    generate_mock_data()