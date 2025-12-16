import time
import random
import datetime
import math
from pocketbase import PocketBase

# Ayarlar
PB_URL = "http://100.96.191.83:8090"
ADMIN_EMAIL = "pi_script@domain.com"
ADMIN_PASS = "12345678"

# Hedef Oda ID
TARGET_PLACE_ID = "jat8nmi4h0bsii0"

# Simülasyon ayarları
DAYS_BACK = 30
READING_INTERVAL_MIN = 15  # 15 dakikalık aralıklar

# Fiziksel değişim katsayıları
CO2_RISE_RATE = 2.5       # CO2 artış hızı
CO2_DECAY_RATE = 0.02     # CO2 azalma hızı
TEMP_RISE_RATE = 0.10     # Isınma hızı
TEMP_DECAY_RATE = 0.03    # Soğuma hızı
VOC_CHANGE_RATE = 0.1     # VOC değişim hızı

def get_target_occupancy(sim_time):
    """Doluluk oranını hesaplar."""
    weekday = sim_time.weekday() # 0=Pzt, 6=Paz
    hour = sim_time.hour
    
    # Her gün mesai var
    day_factor = 1.0 

    # Saatlik doluluk
    if 8 <= hour < 18:
        # Sabah (8-10)
        if hour < 10: 
            base = 2 + (hour - 8) * 1
        # Öğle arası (12-13)
        elif hour == 12:
            base = 2
        # Öğleden sonra (13-16)
        elif 13 <= hour < 16:
            base = 4
        # Akşam (16-18)
        else:
            base = 3 - (hour - 16)
            
        # Rastgele dalgalanma
        noise = random.randint(-1, 1) 
        occupancy = max(0, int((base + noise) * day_factor))
        
        # Kapasite kontrolü
        if occupancy > 4: occupancy = 4
        
        return occupancy
    
    return 0 # Mesai dışı

def calculate_comfort_score(temp, co2):
    """Konfor skorunu hesaplar (0.0 - 1.0)."""
    # Sıcaklık skoru (21-24 arası ideal)
    if 21.0 <= temp <= 24.0:
        t_score = 1.0
    else:
        diff = min(abs(temp - 21.0), abs(temp - 24.0))
        t_score = max(0.0, 1.0 - (diff * 0.25))

    # Hava kalitesi skoru (800 ppm altı ideal)
    if co2 <= 800:
        air_score = 1.0
    else:
        # 2000 ppm üstü 0 puan
        air_score = max(0.0, 1.0 - ((co2 - 800) / 1200.0))

    # Ağırlıklı ortalama
    return round(max(0.0, min(1.0, (0.6 * t_score) + (0.4 * air_score))), 2)

def run_organic_simulation():
    client = PocketBase(PB_URL)
    print(f"Sunucuya bağlanılıyor: {PB_URL}")
    
    try:
        client.admins.auth_with_password(ADMIN_EMAIL, ADMIN_PASS)
        print("Yönetici girişi başarılı.")
        
        # Kullanıcıyı getir veya oluştur
        target_user_id = None
        try:
            users = client.collection("users").get_list(1, 1)
            if users.items: 
                target_user_id = users.items[0].id
                print(f"Mevcut kullanıcı seçildi: {target_user_id}")
            else:
                print("Kullanıcı bulunamadı, yeni oluşturuluyor...")
                new_user = client.collection("users").create({
                    "username": f"mock_{random.randint(1000,9999)}",
                    "email": f"mock{random.randint(1000,9999)}@test.com",
                    "password": "12345678",
                    "passwordConfirm": "12345678",
                    "name": "Mock Bot"
                })
                target_user_id = new_user.id
                print(f"Yeni kullanıcı oluşturuldu: {target_user_id}")
        except Exception as e: 
            print(f"Kullanıcı hatası: {e}")
            
        if not target_user_id:
            print("!!! HATA: Kullanıcı ID yok. Rezervasyonlar oluşturulmayacak.")

        # Geçmiş simülasyonu
        now = datetime.datetime.now()
        start_date = now.replace(minute=0, second=0, microsecond=0) - datetime.timedelta(days=DAYS_BACK)
        end_date = now
        
        current_sim_time = start_date
        
        # Başlangıç değerleri
        curr_co2 = 420.0
        curr_temp = 20.5
        curr_rh = 45.0
        curr_voc = 15.0
        
        total_readings = 0
        total_reservations = 0

        print("1/2: Geçmiş veriler üretiliyor (Sensor + Rezervasyon)...")

        # Rezervasyon takibi
        active_reservation_end = None
        window_open = False # Pencere durumu

        while current_sim_time < end_date:
            # Doluluk hesapla
            occupancy = get_target_occupancy(current_sim_time)
            is_occupied = occupancy > 0

            # Rezervasyon oluştur
            if is_occupied and target_user_id:
                if active_reservation_end is None or current_sim_time >= active_reservation_end:
                    duration_mins = random.choice([30, 60, 90, 120])
                    res_start = current_sim_time
                    res_end = res_start + datetime.timedelta(minutes=duration_mins)
                    active_reservation_end = res_end
                    
                    planned_attendees = max(1, occupancy + random.randint(-1, 1))

                    res_data = {
                        "place_id": TARGET_PLACE_ID,
                        "user_id": target_user_id,
                        "start_ts": res_start.isoformat(),
                        "end_ts": res_end.isoformat(),
                        "status": "completed",
                        "is_hidden": False,
                        "attendee_count": planned_attendees
                    }
                    try:
                        client.collection("reservations").create(res_data)
                        total_reservations += 1
                    except: pass

            # Fiziksel değişimler
            # Hava kötüyse cam açılabilir
            if is_occupied and curr_co2 > 1100 and not window_open:
                # %70 ihtimalle cam açılır
                if random.random() < 0.7:
                    window_open = True
            
            # Hava düzelince cam kapanır
            if (curr_co2 < 600 or not is_occupied) and window_open:
                window_open = False

            # Sıcaklık hesabı
            target_temp = 24.5 if is_occupied else 19.0 
            
            if window_open:
                # Cam açıksa soğuma
                curr_temp -= 0.3 
            elif curr_temp < target_temp:
                curr_temp += TEMP_RISE_RATE * (occupancy * 0.8 + 1) 
            else:
                curr_temp -= TEMP_DECAY_RATE

            # CO2 hesabı
            if window_open:
                # Cam açıksa CO2 düşer
                curr_co2 -= (curr_co2 - 400) * 0.15
            elif is_occupied:
                curr_co2 += (occupancy * 15.0) * CO2_RISE_RATE
            else:
                curr_co2 -= (curr_co2 - 400) * CO2_DECAY_RATE

            target_rh = 45.0 + (occupancy * 3.0)
            curr_rh += (target_rh - curr_rh) * 0.05 + random.uniform(-0.2, 0.2)
            
            target_voc = 20 + (occupancy * 60) 
            if window_open: curr_voc *= 0.8 # Cam açıksa koku da gider
            curr_voc += (target_voc - curr_voc) * VOC_CHANGE_RATE

            # Değer sınırları
            curr_co2 = max(400, min(2000, curr_co2)) # 2000 ppm üst limit
            curr_temp = max(16.0, min(30.0, curr_temp))
            curr_voc = max(0, min(500, curr_voc))
            
            reading_data = {
                "place_id": TARGET_PLACE_ID,
                "recorded_at": current_sim_time.isoformat().replace("T", " "),
                "pir_occupied": is_occupied,
                "temp_c": round(curr_temp, 2),
                "rh_percent": round(curr_rh, 2),
                "voc_index": int(curr_voc),
                "co2_ppm": int(curr_co2),
                "comfort_score": calculate_comfort_score(curr_temp, curr_co2)
            }

            try:
                client.collection("sensor_readings").create(reading_data)
                total_readings += 1
                if total_readings % 100 == 0:
                    print(f"İlerliyor... {total_readings} veri, {total_reservations} rez.", end='\r')
            except: pass

            current_sim_time += datetime.timedelta(minutes=READING_INTERVAL_MIN)

        print(f"\n\nİŞLEM TAMAMLANDI.")
        print(f"Toplam Sensör Verisi: {total_readings}")
        print(f"Toplam Geçmiş Rezervasyon: {total_reservations}")

    except Exception as e:
        print(f"\nKritik Hata: {e}")

if __name__ == "__main__":
    run_organic_simulation()
