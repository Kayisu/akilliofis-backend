import time
import datetime
import random
import board
import busio
from gpiozero import MotionSensor
from adafruit_bme680 import Adafruit_BME680_I2C
from adafruit_scd4x import SCD4X

# DÜZELTME 1: PLACE_ID buraya eklendi
from config import (
    PB_ADMIN_EMAIL, PB_ADMIN_PASSWORD, PB_BASE_URL,
    SENSOR_INTERVAL_SECONDS, FORECAST_INTERVAL_SECONDS,
    TEMP_CORRECTION_FACTOR, STARTUP_DELAY_SECONDS,
    PLACE_ID 
)
from comfort import calc_comfort_score
from pb_client import PBClient

def setup_sensors():
    # I2C hatası alırsan burayı try-except içine almak gerekebilir
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

    # PIR sensörü bazen başlangıçta yanlış tetiklenebilir
    pir = MotionSensor(17)
    return bme, scd4x, pir

def get_cpu_temperature() -> float:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            return float(f.read()) / 1000.0
    except:
        return 50.0

def run_forecast_logic(client: PBClient):
    """Admin paneli grafikleri için basit tahmin verisi üretir."""
    print(">>> Tahmin verileri oluşturuluyor...")
    # Not: client.get_recent_readings metodunun pb_client.py içinde tanımlı olduğundan emin ol
    items = client.get_recent_readings(limit=20)
    if not items: return

    total_comfort, occupied_count = 0.0, 0
    for it in items:
        # items bir dict listesi döner
        total_comfort += float(it.get("comfort_score") or 0.5)
        if it.get("pir_occupied"): occupied_count += 1
    
    avg_comfort = total_comfort / len(items)
    avg_occ = occupied_count / len(items)
    
    now = datetime.datetime.now(datetime.timezone.utc)
    # Gelecek 3 saat için 30'ar dk arayla veri bas
    for i in range(1, 7):
        future_ts = now + datetime.timedelta(minutes=30 * i)
        variation = random.uniform(-0.05, 0.05)
        # create_forecast metodunun pb_client.py içinde tanımlı olduğundan emin ol
        client.create_forecast(
            future_ts, 
            max(0.0, min(1.0, avg_occ + variation)),
            max(0.0, min(1.0, avg_comfort + variation/2))
        )

def main():
    print(f">>> Sistem başlatıldı. Sensör stabilizasyonu için {STARTUP_DELAY_SECONDS} sn bekleniyor...")
    time.sleep(STARTUP_DELAY_SECONDS)
    
    print(">>> Bekleme tamamlandı. Sensörler ve bağlantılar kuruluyor...")
    # DÜZELTME 2: Sensörleri main içinde başlatıp değişkenlere alıyoruz
    bme, scd4x, pir = setup_sensors()

    if not pir: # I2C hatası olduysa çık
        print("Sensör kurulum hatası. Çıkılıyor.")
        return

    client = PBClient(base_url=PB_BASE_URL)

    # DÜZELTME 3: Zamanlayıcıyı döngüden önce başlatıyoruz
    last_forecast = time.time()

    while True:
        start_time = time.time()
        loop_ts = datetime.datetime.now(datetime.timezone.utc)
        
        # --- 1. Sensör Okuma ---
        co2, raw_temp, hum, voc = None, None, None, 0.0
        
        # SCD41 Okuma
        if scd4x and scd4x.data_ready:
            try:
                co2 = float(scd4x.CO2)
                raw_temp = float(scd4x.temperature)
                hum = float(scd4x.relative_humidity)
            except: pass

        # BME680 Okuma
        if bme:
            try:
                if raw_temp is None: raw_temp = float(bme.temperature)
                if hum is None: hum = float(bme.humidity)
                voc = float(bme.gas) / 1000.0
            except: pass

        if raw_temp is None:
            print("[Uyarı] Sıcaklık okunamadı, bekleniyor...")
            time.sleep(SENSOR_INTERVAL_SECONDS)
            continue

        # --- 2. CPU Kompanzasyonu ---
        cpu_temp = get_cpu_temperature()
        comp_temp = raw_temp
        if cpu_temp > raw_temp:
            comp_temp = raw_temp - ((cpu_temp - raw_temp) / TEMP_CORRECTION_FACTOR)
        
        # --- 3. Veri Hazırlama ---
        is_occupied = pir.motion_detected
        c_score = 0.5
        
        safe_co2 = co2 if co2 else 400
        # Konfor skoru hesaplama
        c_score = calc_comfort_score(comp_temp, hum, safe_co2, voc)

        payload = {
            "place_id": PLACE_ID, # DÜZELTME 4: config.PLACE_ID yerine direkt PLACE_ID
            "recorded_at": loop_ts.strftime("%Y-%m-%d %H:%M:%SZ"),
            "pir_occupied": is_occupied,
            "temp_c": round(comp_temp, 2),
            "rh_percent": round(hum, 2) if hum else 0,
            "voc_index": round(voc, 2),
            "co2_ppm": co2 if co2 else 0,
            "comfort_score": c_score,
        }

        # --- LOGLAMA ---
        log_msg = (
            f"[{loop_ts.strftime('%H:%M:%S')}] "
            f"CPU:{cpu_temp:.1f}°C | "
            f"Ham:{raw_temp:.1f}°C -> "
            f"Net:{comp_temp:.2f}°C | "
            f"Nem:%{hum:.1f} | "
            f"VOC:{voc:.1f} | "
            f"CO2:{co2 if co2 else '---'} ppm | "
            f"Hareket:{'VAR' if is_occupied else 'yok'}"
        )
        print(log_msg)

        try:
            client.create_sensor_reading(payload)
        except Exception as e:
            print(f"[Hata] Veri gönderilemedi: {e}")

        # --- 4. Tahmin Kontrolü ---
        if time.time() - last_forecast > FORECAST_INTERVAL_SECONDS:
            try:
                run_forecast_logic(client)
                last_forecast = time.time()
            except Exception as e:
                print(f"[Hata] Tahmin oluşturulamadı: {e}")

        # Döngü gecikmesi
        elapsed = time.time() - start_time
        time.sleep(max(0, SENSOR_INTERVAL_SECONDS - elapsed))

if __name__ == "__main__":
    main()