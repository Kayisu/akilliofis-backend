import time
import datetime
import random
import board
import busio
from gpiozero import MotionSensor
from adafruit_bme680 import Adafruit_BME680_I2C
from adafruit_scd4x import SCD4X

# Config'den gerekli her şeyi çekiyoruz
from config import (
    PB_ADMIN_EMAIL, PB_ADMIN_PASSWORD, PB_BASE_URL,
    SENSOR_INTERVAL_SECONDS, FORECAST_INTERVAL_SECONDS,
    TEMP_CORRECTION_FACTOR, STARTUP_DELAY_SECONDS,
    PLACE_ID
)
from comfort import calc_comfort_score
from pb_client import PBClient

def setup_sensors():
    """Sensörleri başlatır ve nesneleri döner."""
    try:
        i2c = busio.I2C(board.SCL, board.SDA)
    except Exception as e:
        print(f"[Kritik] I2C başlatılamadı: {e}")
        return None, None, None

    bme, scd4x = None, None

    try:
        bme = Adafruit_BME680_I2C(i2c, address=0x77)
        bme.sea_level_pressure = 1013.25
    except:
        print("[Uyarı] BME680 bulunamadı.")

    try:
        scd4x = SCD4X(i2c)
        scd4x.start_periodic_measurement()
    except:
        print("[Uyarı] SCD41 bulunamadı.")

    # PIR sensör tanımlaması
    pir = MotionSensor(17)
    return bme, scd4x, pir

def get_cpu_temperature() -> float:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            return float(f.read()) / 1000.0
    except:
        return 50.0

def run_forecast_logic(client: PBClient):
    """Admin paneli için basit tahmin simülasyonu."""
    print(">>> Tahmin verileri oluşturuluyor...")
    items = client.get_recent_readings(limit=20)
    
    # Veri yoksa tahmin yapma
    if not items: 
        return

    total_comfort, occupied_count = 0.0, 0
    for it in items:
        total_comfort += float(it.get("comfort_score") or 0.5)
        if it.get("pir_occupied"): occupied_count += 1
    
    avg_comfort = total_comfort / len(items)
    avg_occ = occupied_count / len(items)
    
    now = datetime.datetime.now(datetime.timezone.utc)
    # Gelecek 3 saat için 30'ar dk arayla veri üret
    for i in range(1, 7):
        future_ts = now + datetime.timedelta(minutes=30 * i)
        variation = random.uniform(-0.05, 0.05)
        
        client.create_forecast(
            future_ts, 
            max(0.0, min(1.0, avg_occ + variation)),
            max(0.0, min(1.0, avg_comfort + variation/2))
        )

def main():
    print(f">>> Sistem başlatıldı. Sensör stabilizasyonu için {STARTUP_DELAY_SECONDS} sn bekleniyor...")
    time.sleep(STARTUP_DELAY_SECONDS)
    
    print(">>> Sensörler başlatılıyor...")
    bme, scd4x, pir = setup_sensors()

    if not pir:
        print("Sensör hatası (I2C), çıkılıyor.")
        return

    # --- DÜZELTME 1: İSTEMCİ VE GİRİŞ ---
    print(">>> PocketBase bağlantısı kuruluyor...")
    client = PBClient(base_url=PB_BASE_URL)
    
    # Giriş yapmadan veri gönderemezsin!
    client.login_with_password(PB_ADMIN_EMAIL, PB_ADMIN_PASSWORD)
    
    if not client.token:
        print("[HATA] PocketBase'e giriş yapılamadı! Veriler gönderilmeyecek.")
        # Programın çökmemesi için devam edebiliriz ama veri gitmez.
        # İstersen burada 'return' ile programı durdurabilirsin.

    last_forecast = time.time()

    while True:
        start_time = time.time()
        loop_ts = datetime.datetime.now(datetime.timezone.utc)
        
        # --- 1. Sensör Okuma ---
        co2, raw_temp, hum, voc = None, None, None, 0.0
        
        # SCD41
        if scd4x and scd4x.data_ready:
            try:
                co2 = float(scd4x.CO2)
                raw_temp = float(scd4x.temperature)
                hum = float(scd4x.relative_humidity)
            except: pass

        # BME680
        if bme:
            try:
                if raw_temp is None: raw_temp = float(bme.temperature)
                if hum is None: hum = float(bme.humidity)
                voc = float(bme.gas) / 1000.0
            except: pass

        if raw_temp is None:
            print("[Uyarı] Sıcaklık okunamadı, tekrar deneniyor...")
            time.sleep(SENSOR_INTERVAL_SECONDS)
            continue

        # --- 2. Veri İşleme ---
        cpu_temp = get_cpu_temperature()
        comp_temp = raw_temp
        if cpu_temp > raw_temp:
            comp_temp = raw_temp - ((cpu_temp - raw_temp) / TEMP_CORRECTION_FACTOR)
        
        is_occupied = pir.motion_detected
        safe_co2 = co2 if co2 else 400
        
        # Konfor Skoru Hesapla
        c_score = calc_comfort_score(comp_temp, hum, safe_co2, voc)

        # Payload
        # place_id'yi pb_client zaten ekliyor ama burada görünmesi log için iyi
        payload = {
            "recorded_at": loop_ts.strftime("%Y-%m-%d %H:%M:%SZ"),
            "pir_occupied": is_occupied,
            "temp_c": round(comp_temp, 2),
            "rh_percent": round(hum, 2) if hum else 0,
            "voc_index": round(voc, 2),
            "co2_ppm": co2 if co2 else 0,
            "comfort_score": c_score,
        }

        # --- DÜZELTME 2: LOG FORMATI GÜNCELLENDİ ---
        # Konfor skoru eklendi
        log_msg = (
            f"[{loop_ts.strftime('%H:%M:%S')}] "
            f"CPU:{cpu_temp:.1f}°C | "
            f"Net:{comp_temp:.2f}°C | "
            f"Nem:%{hum:.1f} | "
            f"CO2:{co2 if co2 else '---'} | "
            f"Hareket:{'VAR' if is_occupied else 'yok'} | "
            f"Skor:{c_score:.2f}" 
        )
        print(log_msg)

        # Veriyi Gönder
        try:
            if client.token: # Sadece giriş başarılıysa dene
                client.create_sensor_reading(payload)
            else:
                print("[Hata] Token yok, veri gönderilemedi. (Login başarısız mı?)")
        except Exception as e:
            print(f"[Hata] Veri gönderilemedi: {e}")

        # --- 3. Tahmin Kontrolü ---
        if time.time() - last_forecast > FORECAST_INTERVAL_SECONDS:
            if client.token:
                try:
                    run_forecast_logic(client)
                    last_forecast = time.time()
                except Exception as e:
                    print(f"[Hata] Tahmin döngüsü hatası: {e}")

        elapsed = time.time() - start_time
        time.sleep(max(0, SENSOR_INTERVAL_SECONDS - elapsed))

if __name__ == "__main__":
    main()