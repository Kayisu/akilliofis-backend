import time
import datetime
import threading
import requests
import os
import math
import board 

# --- CONFIG ---
from config import (
    PB_BASE_URL, PB_ADMIN_EMAIL, PB_ADMIN_PASSWORD, PLACE_ID, 
    SENSOR_INTERVAL_SECONDS, TEMP_CORRECTION_FACTOR
)

# --- MODULLER ---
from forecaster import DailyForecaster 
from comfort import calc_comfort_score

# --- DONANIM KUTUPHANELERI (ZORUNLU) ---
# Eger bunlar yoksa kod hata verip kirilabilir, istenilen bu.
from gpiozero import MotionSensor
from adafruit_bme680 import Adafruit_BME680_I2C
import adafruit_scd4x

class SensorAgent:
    def __init__(self):
        print("--- AKILLI OFIS AJANI (PURE HARDWARE) ---")
        
        # 1. Baglantilar
        self.forecaster = DailyForecaster()
        self.token = None
        self._login()
        
        # 2. Donanim Baslatma
        self.pir_sensor = None
        self.bme680 = None
        self.scd4x = None
        self._init_hardware()

        # 3. Zamanlayicilar
        self.last_forecast_time = 0
        self.forecast_interval = 24 * 3600 # 24 Saatte bir

    def _login(self):
        try:
            payload = {"identity": PB_ADMIN_EMAIL, "password": PB_ADMIN_PASSWORD}
            r = requests.post(f"{PB_BASE_URL}/api/collections/users/auth-with-password", json=payload, timeout=5)
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
            
            # --- SCD41 BASLATMA ---
            self.scd4x = adafruit_scd4x.SCD4X(i2c)
            self.scd4x.start_periodic_measurement()
            print(">> SCD4x Baslatildi")

            # --- BME680 BASLATMA ---
            try:
                self.bme680 = Adafruit_BME680_I2C(i2c, address=0x77)
            except:
                self.bme680 = Adafruit_BME680_I2C(i2c, address=0x76)
            
            self.bme680.sea_level_pressure = 1013.25
            print(">> BME680 Baslatildi")
            
            # --- PIR SENSOR (GPIO 17) ---
            self.pir_sensor = MotionSensor(17)
            print(">> PIR (GPIO 17) Baslatildi")
            
        except Exception as e:
            print(f"!!! KRITIK DONANIM HATASI !!!: {e}")
            # Mock moda dusmek yok, hata gorunsun.

    def get_cpu_temperature(self):
        try:
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                return float(f.read()) / 1000.0
        except:
            return 50.0 

    def ohm_to_voc_index(self, gas_resistance_ohm):
        """
        BME680 Ohm -> IAQ Index (0-500)
        """
        if gas_resistance_ohm is None: return 50.0 
        
        min_ohm = 5000.0   # Cok kirli
        max_ohm = 50000.0  # Temiz
        
        gas = max(min_ohm, min(gas_resistance_ohm, max_ohm))
        
        log_min = math.log(min_ohm)
        log_max = math.log(max_ohm)
        log_val = math.log(gas)
        
        factor = (log_val - log_min) / (log_max - log_min)
        voc_index = (1.0 - factor) * 500.0
        
        return max(0, min(500, int(voc_index)))

    def read_sensors(self):
        # --- SADECE GERCEK OKUMA ---
        temp, rh, voc_ohms, co2, pir_val = None, None, None, None, False
        
        # 1. BME680
        if self.bme680:
            try:
                raw_temp = self.bme680.temperature
                rh = self.bme680.relative_humidity
                voc_ohms = self.bme680.gas
                
                # Sicaklik Duzeltme
                cpu_temp = self.get_cpu_temperature()
                if cpu_temp > raw_temp:
                    temp = raw_temp - ((cpu_temp - raw_temp) / TEMP_CORRECTION_FACTOR)
                else:
                    temp = raw_temp
            except Exception as e:
                print(f"[UYARI] BME Okuma: {e}")

        # 2. SCD41
        if self.scd4x and self.scd4x.data_ready:
            try:
                co2 = self.scd4x.CO2
            except Exception as e:
                print(f"[UYARI] SCD Okuma: {e}")

        # 3. PIR
        if self.pir_sensor:
            try:
                pir_val = self.pir_sensor.is_active 
            except Exception as e:
                print(f"[UYARI] PIR Okuma: {e}")

        # Varsayilanlar (Veri yoksa yok demektir, uydurmak yok)
        if temp is None: temp = 0.0 
        if rh is None: rh = 0.0
        if co2 is None: co2 = 0
        
        # VOC Donusumu (Ohm -> Index)
        voc_index = self.ohm_to_voc_index(voc_ohms) if voc_ohms else 0.0

        return {
            "temp": temp, 
            "rh": rh, 
            "voc_index": voc_index, 
            "co2": co2, 
            "pir": pir_val
        }

    def loop(self):
        print(f">> Dongu basladi. PIR: GPIO 17. Mock Mode: ASLA.")
        
        while True:
            start_t = time.time()
            
            # A. SENSOR ISLEMLERI
            vals = self.read_sensors()
            
            # Konfor Skoru
            score = calc_comfort_score(
                vals['temp'], vals['rh'], vals['co2'], vals['voc_index']
            )
            
            print(f"\n[OKUMA] T:{vals['temp']:.1f}C | RH:%{vals['rh']:.0f} | CO2:{vals['co2']} | VOC_IDX:{vals['voc_index']:.0f} | PIR:{vals['pir']}")
            print(f"[SKOR] Konfor Skoru: {score}")

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
                print("Veri gonderilemedi (Baglanti?)")
                self._login()

            # B. FORECAST
            if time.time() - self.last_forecast_time > self.forecast_interval:
                print(">> Tahmin zamani geldi...")
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
        print("\nKapatiliyor...")