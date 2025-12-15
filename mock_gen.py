import time
import random
import datetime
import math
from pocketbase import PocketBase

# --- AYARLAR ---
# Bu ayarlar proje yapisina uygun olarak sabit birakilmistir.
PB_URL = "http://100.96.191.83:8090" 
ADMIN_EMAIL = "pi_script@domain.com" 
ADMIN_PASS = "12345678"               

TARGET_PLACE_ID = "jat8nmi4h0bsii0"

# Simulasyon parametreleri
DAYS_BACK = 30            
OFFICE_START_HOUR = 8     
OFFICE_END_HOUR = 19      
READING_INTERVAL_MIN = 10 

# Fiziksel degisim katsayilari (Daha organik veriler icin)
# Degerler 0.0 ile 1.0 arasindadir. Dusuk degerler daha yavas degisim (buyuk atalet) saglar.
CO2_RISE_RATE = 0.08      # Insan varken CO2 artisi
CO2_DECAY_RATE = 0.03     # Havalandirma ile azalma hizi
TEMP_RISE_RATE = 0.05     # Sicaklik artisi (cok yavas)
TEMP_DECAY_RATE = 0.04    # Soguma hizi
VOC_CHANGE_RATE = 0.1     # Koku/Gaz degisimi

def get_weekly_occupancy_pattern(sim_time):
    # Regresyon modelinin ogrenebilmesi icin tutarli bir desen olusturuyoruz.
    # Pazartesi - Cuma arasi (0-4), Hafta sonu (5-6)
    weekday = sim_time.weekday()
    hour = sim_time.hour

    base_attendees = 0

    if weekday < 5: # Hafta ici
        # Sabah toplantisi simulasyonu (09:00 - 11:00)
        if 9 <= hour < 11:
            base_attendees = 6
        # Ogle yemegi sonrasi yogunluk (13:00 - 15:00)
        elif 13 <= hour < 15:
            base_attendees = 4
        # Mesai bitimi oncesi hafiflik (16:00 - 17:00)
        elif 16 <= hour < 17:
            base_attendees = 2
        
        # Hafif rastgelelik ekle (her gun tipatip ayni olmasin ama benzer olsun)
        if base_attendees > 0:
            variation = random.randint(-1, 2)
            base_attendees = max(0, base_attendees + variation)
    
    return base_attendees

def calculate_comfort_score(temp, co2):
    # Basit bir konfor skoru hesaplamasi
    # Sicaklik puani (21-24 arasi ideal)
    if 21.0 <= temp <= 24.0:
        t_score = 1.0
    else:
        diff = min(abs(temp - 21.0), abs(temp - 24.0))
        t_score = max(0.0, 1.0 - (diff * 0.25))

    # Hava kalitesi puani
    if co2 <= 800:
        air_score = 1.0
    else:
        air_score = max(0.0, 1.0 - ((co2 - 800) / 1200.0))

    # Agirlikli ortalama
    final_score = (0.6 * t_score) + (0.4 * air_score)
    return round(max(0.0, min(1.0, final_score)), 2)

def run_organic_simulation():
    client = PocketBase(PB_URL)
    print(f"Sunucuya baglaniliyor: {PB_URL}")
    
    try:
        client.admins.auth_with_password(ADMIN_EMAIL, ADMIN_PASS)
        print("Yonetici girisi basarili.")

        # Onceki verileri temizleme istege bagli, surada sadece yeni veri ekliyoruz.
        start_date = datetime.datetime.now() - datetime.timedelta(days=DAYS_BACK)
        end_date = datetime.datetime.now()
        
        current_sim_time = start_date
        
        # Baslangic atmosfer degerleri (Sabah saatleri gibi serin ve temiz)
        curr_co2 = 410.0
        curr_temp = 21.0
        curr_rh = 48.0
        curr_voc = 20.0
        
        total_readings = 0
        total_reservations = 0

        print("Organik veri uretimi basliyor...")

        while current_sim_time < end_date:
            # Ofis saatleri disinda hizli atla (geceleri veri az olsun veya olmasin)
            if not (OFFICE_START_HOUR <= current_sim_time.hour < OFFICE_END_HOUR):
                # Gece boyunca odayi sifirla (decay)
                curr_co2 = max(400, curr_co2 - 10)
                curr_temp = max(20.0, curr_temp - 0.1)
                curr_voc = max(10, curr_voc - 2)
                
                current_sim_time += datetime.timedelta(minutes=READING_INTERVAL_MIN)
                continue

            # 1. O anki insan sayisini desenden cek
            attendees = get_weekly_occupancy_pattern(current_sim_time)
            
            # Arada sirada rastgele bosluklar veya pikler (Noise)
            if random.random() < 0.05: 
                attendees = 0 if random.random() < 0.5 else attendees + 2

            is_occupied = attendees > 0

            # 2. Rezervasyon Kaydi (Eger doluysa ve o saat basinda ise kayit at)
            # Bu kisim sadece veritabaninda rezervasyon gorunmesi icin.
            if is_occupied and current_sim_time.minute == 0 and random.random() < 0.3:
                res_end = current_sim_time + datetime.timedelta(hours=1)
                try:
                    res_data = {
                        "place_id": TARGET_PLACE_ID,
                        "user_id": client.collection("users").get_list(1, 1).items[0].id, # Ilk kullaniciyi al
                        "start_ts": current_sim_time.isoformat(),
                        "end_ts": res_end.isoformat(),
                        "status": "completed",
                        "is_hidden": True,
                        "attendee_count": attendees
                    }
                    client.collection("reservations").create(res_data)
                    total_reservations += 1
                except: pass

            # 3. Fizik Motoru (Degerleri guncelle)
            
            # CO2 Hesaplama
            # Hedef: Kisi basi +400ppm katkida bulunur (basit yaklasim)
            # Ancak direkt hedefe gitmek yerine, uretime gore artar.
            if is_occupied:
                # Uretim (Kisi sayisi * nefes faktoru)
                production = attendees * 20.0 
                # Mevcut degerden hedefe dogru yumusak artis
                curr_co2 += production * CO2_RISE_RATE
            else:
                # Havalandirma etkisi (400'e dogru azal)
                diff = curr_co2 - 400.0
                curr_co2 -= diff * CO2_DECAY_RATE

            # Sicaklik Hesaplama
            # Hedef: Insan vucut isisi odayi isitir (Max 26-27), Klima (22) sogutur
            target_temp = 22.0 + (attendees * 0.5) if is_occupied else 21.5
            temp_diff = target_temp - curr_temp
            
            if temp_diff > 0:
                curr_temp += temp_diff * TEMP_RISE_RATE # Isinma
            else:
                curr_temp += temp_diff * TEMP_DECAY_RATE # Soguma

            # Nem ve VOC (Hafif rastgele dalgalanma + insan etkisi)
            target_rh = 45.0 + (attendees * 1.5)
            curr_rh += (target_rh - curr_rh) * 0.1 + random.uniform(-0.5, 0.5)

            target_voc = 50 + (attendees * 30)
            curr_voc += (target_voc - curr_voc) * VOC_CHANGE_RATE + random.uniform(-2, 2)

            # Sınırlandırma (Sacma degerleri engelle)
            curr_co2 = max(400, min(3000, curr_co2))
            curr_temp = max(18.0, min(35.0, curr_temp))
            curr_voc = max(0, min(500, curr_voc))

            # 4. Kayit Olustur
            score = calculate_comfort_score(curr_temp, curr_co2)
            
            reading_data = {
                "place_id": TARGET_PLACE_ID,
                "recorded_at": current_sim_time.isoformat().replace("T", " "),
                "pir_occupied": is_occupied,
                "temp_c": round(curr_temp, 2),
                "rh_percent": round(curr_rh, 2),
                "voc_index": int(curr_voc),
                "co2_ppm": int(curr_co2),
                "comfort_score": score
            }

            try:
                client.collection("sensor_readings").create(reading_data)
                total_readings += 1
                if total_readings % 50 == 0:
                    print(f"Ilerliyor... Tarih: {current_sim_time.date()} - CO2: {int(curr_co2)} ppm")
            except Exception as e:
                print(f"Kayit hatasi: {e}")

            current_sim_time += datetime.timedelta(minutes=READING_INTERVAL_MIN)

        print(f"\nISLEM TAMAMLANDI.")
        print(f"Toplam Sensor Verisi: {total_readings}")
        print(f"Toplam Rezervasyon: {total_reservations}")

    except Exception as e:
        print(f"Kritik hata olustu: {e}")

if __name__ == "__main__":
    run_organic_simulation()