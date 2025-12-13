# sensor_agent.py

import time
import datetime
import random
import board
import busio
from gpiozero import MotionSensor
from adafruit_bme680 import Adafruit_BME680_I2C
from adafruit_scd4x import SCD4X

from config import (
    PB_ADMIN_EMAIL, PB_ADMIN_PASSWORD, PB_BASE_URL,
    SENSOR_INTERVAL_SECONDS, FORECAST_INTERVAL_SECONDS,
    TEMP_CORRECTION_FACTOR
)
from comfort import calc_comfort_score
from pb_client import PBClient

def setup_sensors():
    i2c = busio.I2C(board.SCL, board.SDA)
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
    items = client.get_recent_readings(limit=20)
    if not items: return

    total_comfort, occupied_count = 0.0, 0
    for it in items:
        total_comfort += float(it.get("comfort_score") or 0.5)
        if it.get("pir_occupied"): occupied_count += 1
    
    avg_comfort = total_comfort / len(items)
    avg_occ = occupied_count / len(items)
    
    now = datetime.datetime.now(datetime.timezone.utc)
    # Gelecek 3 saat için 30'ar dk arayla veri bas
    for i in range(1, 7):
        future_ts = now + datetime.timedelta(minutes=30 * i)
        variation = random.uniform(-0.05, 0.05)
        client.create_forecast(
            future_ts, 
            max(0.0, min(1.0, avg_occ + variation)),
            max(0.0, min(1.0, avg_comfort + variation/2))
        )

def main():
    client = PBClient(base_url=PB_BASE_URL)
    client.login_with_password(PB_ADMIN_EMAIL, PB_ADMIN_PASSWORD)

    bme, scd4x, pir = setup_sensors()
    last_forecast = 0
    print(f"Sistem devrede. Aralık: {SENSOR_INTERVAL_SECONDS}sn. LED: YOK.")

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

        # BME680 Okuma (Yedek veya VOC için)
        if bme:
            try:
                if raw_temp is None: raw_temp = float(bme.temperature)
                if hum is None: hum = float(bme.humidity)
                voc = float(bme.gas) / 1000.0
            except: pass

        if raw_temp is None:
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
        if co2:
            c_score = calc_comfort_score(comp_temp, hum, co2, voc)

        payload = {
            "recorded_at": loop_ts.strftime("%Y-%m-%d %H:%M:%SZ"),
            "pir_occupied": is_occupied,
            "temp_c": round(comp_temp, 2),
            "rh_percent": round(hum, 2) if hum else 0,
            "voc_index": round(voc, 2),
            "co2_ppm": co2,
            "comfort_score": c_score,
        }

        print(f"[{loop_ts.strftime('%H:%M:%S')}] Sıcaklık: {comp_temp:.2f}°C (CPU: {cpu_temp:.1f}) | Hareket: {is_occupied} | Konfor: {c_score}")
        client.create_sensor_reading(payload)

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