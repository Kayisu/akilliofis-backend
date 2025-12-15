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
    SENSOR_INTERVAL_SECONDS, TEMP_CORRECTION_FACTOR, WARMUP_SKIP_COUNT
)

# --- MODULLER ---
from forecaster import WeeklyForecaster 
from comfort import calc_comfort_score

# --- DONANIM KUTUPHANELERI (ZORUNLU) ---
# Eger bunlar yoksa kod hata verip kirilabilir, istenilen bu.
from gpiozero import MotionSensor
from adafruit_bme680 import Adafruit_BME680_I2C
import adafruit_scd4x

class SensorAgent:
    def __init__(self):
        print("--- SMART OFFICE AGENT STARTED ---")
        
        # 1. Connections
        self.forecaster = WeeklyForecaster()
        self.token = None
        self._login()
        
        # Run initial forecast immediately
        print(">> Triggering initial weekly forecast...")
        t = threading.Thread(target=self.forecaster.run_cycle)
        t.daemon = True
        t.start()
        
        # 2. Hardware Initialization
        self.pir_sensor = None
        self.bme680 = None
        self.scd4x = None
        self._init_hardware()

        # 3. Timers
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
                print(">> Login Successful")
            else:
                print(f">> Login Failed: {r.status_code}")
        except Exception as e: 
            print(f">> Login Error: {e}")

    def _init_hardware(self):
        try:
            i2c = board.I2C()
            
            # --- SCD41 ---
            self.scd4x = adafruit_scd4x.SCD4X(i2c)
            self.scd4x.start_periodic_measurement()
            print(">> SCD4x Initialized")

            # --- BME680 ---
            try:
                self.bme680 = Adafruit_BME680_I2C(i2c, address=0x77)
            except:
                self.bme680 = Adafruit_BME680_I2C(i2c, address=0x76)
            
            self.bme680.sea_level_pressure = 1013.25
            print(">> BME680 Initialized")
            
            # --- PIR SENSOR (GPIO 17) ---
            self.pir_sensor = MotionSensor(17)
            print(">> PIR (GPIO 17) Initialized")
            
        except Exception as e:
            print(f"!!! CRITICAL HARDWARE ERROR !!!: {e}")

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
        
        # 1. BME680
        if self.bme680:
            try:
                raw_temp = self.bme680.temperature
                rh = self.bme680.relative_humidity
                voc_ohms = self.bme680.gas
                
                # Temperature Correction
                if cpu_temp > raw_temp:
                    temp = raw_temp - ((cpu_temp - raw_temp) / TEMP_CORRECTION_FACTOR)
                else:
                    temp = raw_temp
            except Exception as e:
                print(f"[WARNING] BME Read: {e}")

        # 2. SCD41
        if self.scd4x and self.scd4x.data_ready:
            try:
                co2 = self.scd4x.CO2
            except Exception as e:
                print(f"[WARNING] SCD Read: {e}")

        # 3. PIR
        if self.pir_sensor:
            try:
                pir_val = self.pir_sensor.is_active 
            except Exception as e:
                print(f"[WARNING] PIR Read: {e}")

        # VOC Conversion (Ohm -> Index)
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
        print(f">> Loop started. PIR: GPIO 17.")
        warmup_counter = 0
        
        while True:
            start_t = time.time()
            
            # A. SENSOR OPERATIONS
            vals = self.read_sensors()
            
            # WARMUP CHECK
            if warmup_counter < WARMUP_SKIP_COUNT:
                warmup_counter += 1
                print(f"[WARMUP] Sensor stabilizing... ({warmup_counter}/{WARMUP_SKIP_COUNT})")
                print(f"         Temp: {vals['temp']:.1f} | CO2: {vals['co2']}")
                time.sleep(SENSOR_INTERVAL_SECONDS)
                continue
            
            # Comfort Score
            score = calc_comfort_score(
                vals['temp'], vals['rh'], vals['co2'], vals['voc_index']
            )
            
            print(f"\n--- SENSOR READING ---")
            print(f"CPU Temp      : {vals['cpu_temp']:.1f} C")
            print(f"Raw Sensor    : {vals['raw_temp']:.1f} C")
            print(f"Ambient Temp  : {vals['temp']:.1f} C (Corrected)")
            print(f"Humidity      : {vals['rh']:.1f} %")
            print(f"CO2           : {vals['co2']} ppm")
            print(f"VOC Index     : {vals['voc_index']:.0f}/500")
            print(f"PIR Detection : {'DETECTED' if vals['pir'] else 'NONE'}")
            print(f"Comfort Score : {score}")
            print(f"----------------------")

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
                print("Data send failed (Connection?)")
                self._login()

            # B. FORECAST
            if time.time() - self.last_forecast_time > self.forecast_interval:
                print(">> Forecasting time...")
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