#sensor_agent.py
import time
import datetime
import threading
import requests
import os
import math
import board 
from config import (
    PB_BASE_URL, PB_ADMIN_EMAIL, PB_ADMIN_PASSWORD, PLACE_ID, 
    SENSOR_INTERVAL_SECONDS, TEMP_CORRECTION_FACTOR, WARMUP_SKIP_COUNT
)
from forecaster import WeeklyForecaster 
from comfort import calc_comfort_score
from gpiozero import MotionSensor
from adafruit_bme680 import Adafruit_BME680_I2C
import adafruit_scd4x

class SensorAgent:
    def __init__(self):
        print("--- AKILLI OFİS ARACISI BAŞLATILDI ---")
        
        self.forecaster = WeeklyForecaster()
        self.token = None
        self._login()
        
        print(">> İlk haftalık tahmin tetikleniyor...")
        t = threading.Thread(target=self.forecaster.run_cycle)
        t.daemon = True
        t.start()
        
        self.pir_sensor = None
        self.bme680 = None
        self.scd4x = None
        self._init_hardware()

        # 3. Zamanlayıcılar
        self.last_forecast_time = time.time()
        self.forecast_interval = 24 * 3600

    def _login(self):
        try:
            payload = {"identity": PB_ADMIN_EMAIL, "password": PB_ADMIN_PASSWORD}
            r = requests.post(f"{PB_BASE_URL}/api/collections/users/auth-with-password", json=payload, timeout=5)
            if r.status_code == 404:
                 r = requests.post(f"{PB_BASE_URL}/api/admins/auth-with-password", json=payload, timeout=5)
            
            if r.status_code == 200:
                self.token = r.json().get("token")
                print(">> Giriş başarılı")
            else:
                print(f">> Giriş başarısız: {r.status_code}")
        except Exception as e: 
            print(f">> Giriş hatası: {e}")

    def _init_hardware(self):
        try:
            i2c = board.I2C()
            
            # --- SCD41 (CO2 sensörü) ---
            self.scd4x = adafruit_scd4x.SCD4X(i2c)
            self.scd4x.start_periodic_measurement()
            print(">> SCD4x sensörü başlatıldı")

            # --- BME680 (sıcaklık/nem/VOC sensörü) ---
            try:
                self.bme680 = Adafruit_BME680_I2C(i2c, address=0x77)
            except:
                self.bme680 = Adafruit_BME680_I2C(i2c, address=0x76)
            
            self.bme680.sea_level_pressure = 1013.25
            print(">> BME680 sensörü başlatıldı")
            
            # --- PIR HAREKET SENSÖRÜ (GPIO 17) ---
            self.pir_sensor = MotionSensor(17)
            print(">> PIR (GPIO 17) hareket sensörü başlatıldı")
            
        except Exception as e:
            print(f"!!! KRİTİK DONANIM HATASI !!!: {e}")

    def get_cpu_temperature(self):
        try:
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                return float(f.read()) / 1000.0
        except:
            return 50.0 

    def ohm_to_voc_index(self, gas_resistance_ohm):
        """
        BME680 Ohm -> IAQ (Hava Kalitesi) İndeksi (0-500)
        """
        if gas_resistance_ohm is None: return 50.0 
        
        min_ohm = 5000.0
        max_ohm = 50000.0
        
        gas = max(min_ohm, min(gas_resistance_ohm, max_ohm))
        
        log_min = math.log(min_ohm)
        log_max = math.log(max_ohm)
        log_val = math.log(gas)
        
        factor = (log_val - log_min) / (log_max - log_min)
        voc_index = (1.0 - factor) * 500.0
        
        return max(0, min(500, int(voc_index)))

    def read_sensors(self):
        temp, rh, voc_ohms, co2, pir_val = 0.0, 0.0, 0.0, 0, False
        raw_temp = 0.0
        cpu_temp = self.get_cpu_temperature()
        
        # BME680 sensör okuması
        if self.bme680:
            try:
                raw_temp = self.bme680.temperature
                rh = self.bme680.relative_humidity
                voc_ohms = self.bme680.gas
                
                # Sıcaklık düşürme düzeltmesi (CPU ısısı etkisini azaltma)
                if cpu_temp > raw_temp:
                    temp = raw_temp - ((cpu_temp - raw_temp) / TEMP_CORRECTION_FACTOR)
                else:
                    temp = raw_temp
            except Exception as e:
                print(f"[UYARI] BME680 okuma hatası: {e}")

        # SCD41 CO2 sensör okuması
        if self.scd4x and self.scd4x.data_ready:
            try:
                co2 = self.scd4x.CO2
            except Exception as e:
                print(f"[UYARI] SCD4x okuma hatası: {e}")

        # PIR hareket sensörü okuması
        if self.pir_sensor:
            try:
                pir_val = self.pir_sensor.is_active 
            except Exception as e:
                print(f"[UYARI] PIR okuma hatası: {e}")

        # VOC dönüşümü (Ohm -> İndeks)
        voc_index = self.ohm_to_voc_index(voc_ohms) if voc_ohms else 0.0

        return {
            "temp": temp, 
            "raw_temp": raw_temp,
            "cpu_temp": cpu_temp,
            "rh": rh, 
            "voc_index": voc_index, 
            "co2": co2, 
            "pir": pir_val
        }

    def loop(self):
        print(f">> Döngü başlatıldı. PIR: GPIO 17.")
        warmup_counter = 0
        
        while True:
            start_t = time.time()
            
            # Sensör işlemleri
            vals = self.read_sensors()
            
            # Isınma süresi kontrolü
            is_warmup = warmup_counter < WARMUP_SKIP_COUNT
            if is_warmup:
                warmup_counter += 1
                status_label = f"WARMUP ({warmup_counter}/{WARMUP_SKIP_COUNT})"
            else:
                status_label = "ACTIVE"
                
            print(f"\n--- SENSOR READING [{status_label}] ---")
            print(f"CPU Temp      : {vals['cpu_temp']:.1f} C")
            print(f"Raw Sensor    : {vals['raw_temp']:.1f} C")
            print(f"Processed Temp: {vals['temp']:.1f} C")
            print(f"Humidity      : {vals['rh']:.1f} %")
            print(f"VOC Index     : {vals['voc_index']:.0f}")
            print(f"PIR           : {vals['pir']}")
            print(f"CO2           : {vals['co2']} ppm")
            print(f"-------------------------------------")

            if is_warmup:
                time.sleep(SENSOR_INTERVAL_SECONDS)
                continue
            
            # Konfor skoru hesaplama
            score = calc_comfort_score(
                vals['temp'], vals['rh'], vals['co2'], vals['voc_index']
            )
            
            print(f"Konfor Skoru  : {score}")

            headers = {"Authorization": f"Bearer {self.token}"}
            payload = {
                "place_id": PLACE_ID,
                "recorded_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "temp_c": round(vals['temp'], 2),
                "rh_percent": round(vals['rh'], 2),
                "voc_index": int(vals['voc_index']),
                "co2_ppm": int(vals['co2']),
                "pir_occupied": vals['pir'],
                "comfort_score": score 
            }
            
            try:
                requests.post(f"{PB_BASE_URL}/api/collections/sensor_readings/records", json=payload, headers=headers, timeout=2)
            except: 
                print("Veri gönderimi başarısız (Bağlantı sorunu olabilir mi?)")
                self._login()

            #Tahmin başlatma
            if time.time() - self.last_forecast_time > self.forecast_interval:
                print(">> Tahmin zamanı...")
                t = threading.Thread(target=self.forecaster.run_cycle)
                t.daemon = True
                t.start()
                self.last_forecast_time = time.time()

            elapsed = time.time() - start_t
            time.sleep(max(0, SENSOR_INTERVAL_SECONDS - elapsed))

if __name__ == "__main__":
    agent = SensorAgent()
    try:
        agent.loop()
    except KeyboardInterrupt:
        print("\nKapatılıyor...")