import time
import random
import datetime
from pocketbase import PocketBase

# --- AYARLAR ---
PB_URL = "http://100.96.191.83:8090" 
ADMIN_EMAIL = "pi_script@domain.com" 
ADMIN_PASS = "12345678"               

TARGET_PLACE_ID = "jat8nmi4h0bsii0"

# Simülasyon Ayarları
DAYS_BACK = 30            
OFFICE_START_HOUR = 8     
OFFICE_END_HOUR = 19      
READING_INTERVAL_MIN = 10 

# YUMUŞATMA FAKTÖRÜ (0.1 = Çok Yavaş, 0.5 = Hızlı, 1.0 = Anında)
SMOOTHING = 0.15 

def run_direct_upload():
    client = PocketBase(PB_URL)
    print(f"{PB_URL} adresine bağlanılıyor...")
    
    try:
        client.admins.auth_with_password(ADMIN_EMAIL, ADMIN_PASS)
        print("Giriş başarılı.")

        try:
            target_place = client.collection("places").get_one(TARGET_PLACE_ID)
            print(f"Hedef Oda: {target_place.name}")
        except:
            print("HATA: Oda bulunamadı!")
            return

        users = client.collection("users").get_full_list()
        
        start_date = datetime.datetime.now() - datetime.timedelta(days=DAYS_BACK)
        end_date = datetime.datetime.now()
        
        total_reservations = 0
        total_readings = 0

        print(f"ORGANİK Veri üretimi başlıyor...")

        current_sim_time = start_date
        active_reservation = None 
        reservation_end_time = None
        attendees_in_room = 0

        # --- BAŞLANGIÇ DEĞERLERİ (State) ---
        # Bu değerler döngü boyunca korunacak ve yavaş yavaş değişecek
        curr_co2 = 400.0
        curr_temp = 22.0
        curr_voc = 50.0
        curr_rh = 45.0

        while current_sim_time < end_date:
            if OFFICE_START_HOUR <= current_sim_time.hour < OFFICE_END_HOUR:
                
                # --- A. REZERVASYON ---
                if active_reservation and current_sim_time >= reservation_end_time:
                    active_reservation = None
                    attendees_in_room = 0

                if not active_reservation and random.random() < 0.20:
                    duration = random.choice([1, 1.5, 2])
                    res_end = current_sim_time + datetime.timedelta(hours=duration)
                    if res_end.hour >= OFFICE_END_HOUR:
                        res_end = current_sim_time.replace(hour=OFFICE_END_HOUR, minute=0)

                    cap = target_place.capacity if hasattr(target_place, 'capacity') else 5
                    attendees_in_room = random.randint(1, cap)
                    
                    res_data = {
                        "place_id": target_place.id,
                        "user_id": random.choice(users).id,
                        "start_ts": current_sim_time.isoformat(),
                        "end_ts": res_end.isoformat(),
                        "status": "completed",
                        "is_hidden": True,
                        "attendee_count": attendees_in_room
                    }
                    try:
                        client.collection("reservations").create(res_data)
                        total_reservations += 1
                        active_reservation = True
                        reservation_end_time = res_end
                        print(f"[+] Rezervasyon: {attendees_in_room} Kişi", end="\r")
                    except: pass

                # --- B. SENSÖR (ORGANİK MANTIK) ---
                
                # 1. HEDEFLERİ BELİRLE (Target)
                if active_reservation:
                    # Dolu oda hedefleri
                    target_co2 = 400 + (attendees_in_room * 250) # Kişi başı daha yüksek hedef ama yavaş çıkacak
                    target_temp = 22.0 + (attendees_in_room * 0.6) 
                    target_voc = 50 + (attendees_in_room * 40)
                    target_rh = 45.0 + (attendees_in_room * 2)
                    pir = random.random() < 0.95
                else:
                    # Boş oda hedefleri (Fabrika ayarları)
                    target_co2 = 400.0
                    target_temp = 22.0 # Klima set değeri
                    target_voc = 50.0
                    target_rh = 45.0
                    pir = False

                # 2. YAVAŞ GEÇİŞ (Smoothing)
                # Formül: Yeni = Eski + (Fark * Hız) + Gürültü
                
                # CO2
                diff_co2 = target_co2 - curr_co2
                curr_co2 += (diff_co2 * SMOOTHING) + random.uniform(-10, 10)
                
                # Sıcaklık (Daha yavaş değişir)
                diff_temp = target_temp - curr_temp
                curr_temp += (diff_temp * (SMOOTHING / 2)) + random.uniform(-0.05, 0.05)
                
                # VOC
                diff_voc = target_voc - curr_voc
                curr_voc += (diff_voc * SMOOTHING) + random.uniform(-5, 5)

                # Nem
                diff_rh = target_rh - curr_rh
                curr_rh += (diff_rh * SMOOTHING) + random.uniform(-1, 1)

                # Sınırlandırma (Clamp)
                curr_co2 = max(400, min(2500, curr_co2))
                curr_temp = max(18, min(30, curr_temp))
                curr_voc = max(0, min(500, curr_voc))

                # --- KONFOR SKORU ---
                # 1. Sıcaklık Skoru
                if 21.0 <= curr_temp <= 24.0:
                    t_score = 1.0
                else:
                    diff = min(abs(curr_temp - 21.0), abs(curr_temp - 24.0))
                    t_score = max(0.0, 1.0 - (diff * 0.2))

                # 2. Hava Kalitesi Skoru
                if curr_co2 <= 800:
                    air_score = 1.0
                else:
                    air_score = max(0.0, 1.0 - ((curr_co2 - 800) / 1000.0))

                final_score_raw = (0.6 * t_score) + (0.4 * air_score)
                final_score = round(max(0.0, min(1.0, final_score_raw)), 2)

                reading_data = {
                    "place_id": target_place.id,
                    "recorded_at": current_sim_time.isoformat().replace("T", " "),
                    "pir_occupied": pir,
                    "temp_c": round(curr_temp, 2),
                    "rh_percent": round(curr_rh, 2),
                    "voc_index": int(curr_voc),
                    "co2_ppm": int(curr_co2),
                    "comfort_score": final_score
                }

                try:
                    client.collection("sensor_readings").create(reading_data)
                    total_readings += 1
                except: pass

            else:
                # Gece boyunca değerleri sıfırla/soğut (Hızlı düşüş)
                # Böylece sabah geldiğinde 400ppm'den başlar
                curr_co2 = 400.0
                curr_temp = 21.5
                curr_voc = 50.0

            current_sim_time += datetime.timedelta(minutes=READING_INTERVAL_MIN)

        print("\nTAMAMLANDI!")
        print(f"Toplam {total_readings} adet 'yumuşak geçişli' veri yüklendi.")

    except Exception as e:
        print(f"\nKritik Hata: {e}")

if __name__ == "__main__":
    run_direct_upload()