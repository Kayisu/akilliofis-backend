import time
import datetime
import threading
import random
import requests
import os

# --- CONFIG ---
from config import (
    PB_BASE_URL, PB_ADMIN_EMAIL, PB_ADMIN_PASSWORD, PLACE_ID, 
    SENSOR_INTERVAL_SECONDS, TEMP_CORRECTION_FACTOR
)

# --- MODULLER ---
from forecaster import DailyForecaster 
from comfort import calc_comfort_score  # <-- YENI: Konfor modulu eklendi

# Donanim kutuphaneleri
try:
    import board
    import adafruit_scd4x
    import bme680
    import RPi.GPIO as GPIO
    MOCK_MODE = False
except ImportError:
    print("Donanim bulunamadi. SIMULASYON modunda calisiliyor.")
    MOCK_MODE = True

class SensorAgent:
    def __init__(self):
        print("--- AKILLI OFIS AJANI BASLATILIYOR ---")
        
        # 1. Baglantilar
        self.forecaster = DailyForecaster()
        self.token = None
        self._login()
        
        # 2. Donanim Baslatma
        if not MOCK_MODE:
            self._init_hardware()

        # 3. Zamanlayicilar
        self.last_forecast_time = 0
        self.forecast_interval = 24 * 3600 # 24 Saatte bir

    def _login(self):
        try:
            payload = {"identity": PB_ADMIN_EMAIL, "password": PB_ADMIN_PASSWORD}
            r = requests.post(f"{PB_BASE_URL}/api/collections/users/auth-with-password", json=payload, timeout=5)
            # Admin degilse admin endpoint dene
            if r.status_code == 404:
                 r = requests.post(f"{PB_BASE_URL}/api/admins/auth-with-password", json=payload, timeout=5)
            
            if r.status_code == 200:
                self.token = r.json().get("token")
                print(">> Login Basarili")
            else:
                print(f">> Login Basarisiz: {r.status_code}")
        except Exception as e: 
            print(f">> Login Hatasi: {e}")

    def _init_hardware(self):
        try:
            i2c = board.I2C()
            self.scd4x = adafruit_scd4x.SCD4X(i2c)
            self.scd4x.start_periodic_measurement()
            self.bme = bme680.BME680(bme680.I2C_ADDR_PRIMARY)
            
            self.bme.set_humidity_oversample(bme680.OS_2X)
            self.bme.set_pressure_oversample(bme680.OS_4X)
            self.bme.set_temperature_oversample(bme680.OS_8X)
            self.bme.set_filter(bme680.FILTER_SIZE_3)
            
            self.pir_pin = 23
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.pir_pin, GPIO.IN)
        except Exception as e:
            print(f"Donanim Hatasi: {e}")

    def get_cpu_temperature(self):
        """Raspberry Pi CPU sicakligini okur"""
        try:
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                return float(f.read()) / 1000.0
        except:
            return 50.0 # Okuyamazsa varsayilan

    def read_sensors(self):
        if MOCK_MODE:
            return {
                "temp": 22.0 + random.uniform(-0.5, 0.5),
                "rh": 45.0 + random.uniform(-1, 1),
                "voc": 50 + random.randint(-5, 5),
                "co2": 450 + random.randint(-10, 10),
                "pir": random.choice([True, False])
            }
        
        # --- GERCEK OKUMA ---
        pir = GPIO.input(self.pir_pin)
        
        # Varsayilanlar
        temp, rh, voc, co2 = 22.0, 45.0, 50.0, 400
        
        # BME680 Okuma ve Sicaklik Duzeltme
        if self.bme.get_sensor_data():
            raw_temp = self.bme.data.temperature
            rh = self.bme.data.humidity
            voc = self.bme.data.gas_resistance
            
            # --- SICAKLIK DUZELTME FORMULU ---
            # Sensorun isinmasini CPU isisina gore kompanse ediyoruz
            cpu_temp = self.get_cpu_temperature()
            if cpu_temp > raw_temp:
                temp = raw_temp - ((cpu_temp - raw_temp) / TEMP_CORRECTION_FACTOR)
            else:
                temp = raw_temp
        
        # SCD41 Okuma
        if self.scd4x.data_ready:
            co2 = self.scd4x.CO2
            
        return {"temp": temp, "rh": rh, "voc": voc, "co2": co2, "pir": bool(pir)}

    def loop(self):
        print(f">> Dongu basladi. Forecast her 24 saatte bir guncellenecek.")
        
        while True:
            start_t = time.time()
            
            # A. SENSOR ISLEMLERI
            vals = self.read_sensors()
            
            # Konfor Skorunu Hesapla (comfort.py kullanarak)
            score = calc_comfort_score(
                vals['temp'], vals['rh'], vals['co2'], vals['voc']
            )
            
            headers = {"Authorization": f"Bearer {self.token}"}
            payload = {
                "place_id": PLACE_ID,
                "recorded_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "temp_c": round(vals['temp'], 2),
                "rh_percent": round(vals['rh'], 2),
                "voc_index": int(vals['voc']),
                "co2_ppm": int(vals['co2']),
                "pir_occupied": vals['pir'],
                "comfort_score": score # <-- ARTIK GERCEK HESAP
            }
            
            try:
                requests.post(f"{PB_BASE_URL}/api/collections/sensor_readings/records", json=payload, headers=headers, timeout=2)
                print(f"Veri: {payload['temp_c']}C | {payload['co2_ppm']}ppm | Skor: {score}")
            except: 
                print("Veri gonderilemedi (Baglanti?)")
                self._login()

            # B. FORECAST KONTROLU
            if time.time() - self.last_forecast_time > self.forecast_interval:
                print(">> Tahmin zamani geldi, arka planda baslatiliyor...")
                t = threading.Thread(target=self.forecaster.run_cycle)
                t.daemon = True
                t.start()
                self.last_forecast_time = time.time()

            # Bekleme
            elapsed = time.time() - start_t
            time.sleep(max(0, SENSOR_INTERVAL_SECONDS - elapsed))

if __name__ == "__main__":
    agent = SensorAgent()
    try:
        agent.loop()
    except KeyboardInterrupt:
        print("\nKapatiliyor...")