import time
import datetime
import threading
import requests
import sys
from collections import deque

# --- CONFIG ---
from config import (
    PB_BASE_URL, PB_ADMIN_EMAIL, PB_ADMIN_PASSWORD, PLACE_ID, 
    SENSOR_INTERVAL_SECONDS, TEMP_CORRECTION_FACTOR,
    WARMUP_SKIP_COUNT, GAS_HISTORY_LEN, TEMP_HISTORY_LEN
)

# --- MODULLER ---
from forecaster import DailyForecaster 
from comfort import calc_comfort_score

# --- DONANIM KUTUPHANELERI (ZORUNLU) ---
try:
    import board
    import adafruit_scd4x
    import bme680
    import RPi.GPIO as GPIO
except ImportError as e:
    print(f"\n[KRITIK HATA] Donanim kutuphaneleri eksik: {e}")
    print("Sistem KAPATILIYOR. Lutfen 'pip install adafruit-circuitpython-scd4x bme680 RPi.GPIO' komutunu calistirin.")
    sys.exit(1)

class SensorAgent:
    def __init__(self):
        print("--- AKILLI OFIS AJANI (REAL MODE ONLY) ---")
        
        # 1. Degiskenler
        self.token = None
        self.temp_buffer = deque(maxlen=TEMP_HISTORY_LEN or 10)
        self.gas_buffer = deque(maxlen=GAS_HISTORY_LEN or 10)
        
        # 2. Baglanti
        self._login()
        
        # 3. Donanim Baslatma
        self.scd4x = None
        self.bme = None
        self.pir_pin = 23
        self._init_hardware() # Hata verirse programi durdurur

        # 4. Zamanlayicilar
        self.forecaster = DailyForecaster()
        self.last_forecast_time = 0
        self.forecast_interval = 24 * 3600

    def _login(self):
        try:
            payload = {"identity": PB_ADMIN_EMAIL, "password": PB_ADMIN_PASSWORD}
            url = f"{PB_BASE_URL}/api/admins/auth-with-password"
            r = requests.post(url, json=payload, timeout=5)
            
            if r.status_code == 404:
                 url = f"{PB_BASE_URL}/api/collections/users/auth-with-password"
                 r = requests.post(url, json=payload, timeout=5)
            
            if r.status_code == 200:
                self.token = r.json().get("token")
                print(">> PocketBase Girisi Basarili")
            else:
                print(f">> Giris Hatasi: {r.status_code}")
                # Giris yapamazsa da devam etmeyi dener ama veri gonderemez
        except Exception as e: 
            print(f">> Baglanti Hatasi: {e}")

    def _init_hardware(self):
        print(">> Donanim baglantilari kontrol ediliyor...")
        try:
            # 1. SCD41 (CO2)
            i2c = board.I2C()
            self.scd4x = adafruit_scd4x.SCD4X(i2c)
            self.scd4x.start_periodic_measurement()
            print("   [OK] SCD41 (CO2)")

            # 2. BME680 (Sicaklik/Nem/Gaz)
            try:
                self.bme = bme680.BME680(bme680.I2C_ADDR_PRIMARY)
            except IOError:
                self.bme = bme680.BME680(bme680.I2C_ADDR_SECONDARY)
            
            # BME Ayarlari
            self.bme.set_humidity_oversample(bme680.OS_2X)
            self.bme.set_pressure_oversample(bme680.OS_4X)
            self.bme.set_temperature_oversample(bme680.OS_8X)
            self.bme.set_filter(bme680.FILTER_SIZE_3)
            self.bme.set_gas_status(bme680.ENABLE_GAS_MEAS)
            self.bme.set_gas_heater_temperature(320)
            self.bme.set_gas_heater_duration(150)
            self.bme.select_gas_heater_profile(0)
            print("   [OK] BME680 (Hava)")
            
            # 3. PIR (Hareket)
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.pir_pin, GPIO.IN)
            print("   [OK] PIR (Hareket)")
            
        except Exception as e:
            print(f"\n[KRITIK DONANIM HATASI] : {e}")
            print("Kablolarinizi kontrol edin. Program durduruluyor.")
            sys.exit(1)

    def _get_cpu_temperature(self):
        try:
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                return float(f.read()) / 1000.0
        except:
            return 50.0

    def read_sensors(self):
        """
        Sadece GERCEK veri doner. Hata varsa None doner.
        Asla random sayi uretmez.
        """
        # 1. PIR Check
        try:
            pir_val = GPIO.input(self.pir_pin)
        except:
            print("[HATA] PIR Okunamadi")
            pir_val = False

        # 2. BME680 Check
        temp, rh, voc = None, None, None
        if self.bme and self.bme.get_sensor_data():
            raw_temp = self.bme.data.temperature
            rh = self.bme.data.humidity
            voc = self.bme.data.gas_resistance
            
            # CPU sicaklik duzeltmesi
            cpu_temp = self._get_cpu_temperature()
            if cpu_temp > raw_temp:
                temp = raw_temp - ((cpu_temp - raw_temp) / TEMP_CORRECTION_FACTOR)
            else:
                temp = raw_temp
        else:
            # BME verisi hazir degilse bekle
            pass 

        # 3. SCD41 Check
        co2 = None
        if self.scd4x and self.scd4x.data_ready:
            co2 = self.scd4x.CO2
        
        # Eger kritik veriler yoksa None don
        if temp is None or co2 is None:
            return None

        return {"temp": temp, "rh": rh, "voc": voc, "co2": co2, "pir": bool(pir_val)}

    def loop(self):
        print(f">> Sensor dongusu basladi (Aralik: {SENSOR_INTERVAL_SECONDS}s)")
        print(f">> Isinma Modu: {WARMUP_SKIP_COUNT} okuma boyunca kayit alinmayacak.")
        
        loop_counter = 0

        while True:
            start_t = time.time()
            loop_counter += 1

            # A. VERI OKU
            vals = self.read_sensors()

            if vals is None:
                print(f"[BEKLIYOR] Sensorler hazirlaniyor veya veri okunamadi... ({loop_counter})")
                time.sleep(2)
                continue

            # B. BUFFER / SMOOTHING (Sadece gercek veri ile)
            self.temp_buffer.append(vals['temp'])
            if vals['voc']: self.gas_buffer.append(vals['voc'])

            avg_temp = sum(self.temp_buffer) / len(self.temp_buffer)
            # VOC Ohm degerini aliyoruz
            avg_voc_ohm = sum(self.gas_buffer) / len(self.gas_buffer) if self.gas_buffer else vals['voc']
            
            # Basit Ohm -> Index Donusumu (Ters oranti)
            # 50k Ohm = Temiz (50 puan), 5k Ohm = Kirli (300 puan) gibi kaba taslak
            voc_index_estimated = max(0, min(500, int((50000 - avg_voc_ohm) / 100)))
            if voc_index_estimated < 0: voc_index_estimated = 0

            # C. ISINMA (WARMUP) KONTROLU
            if loop_counter <= WARMUP_SKIP_COUNT:
                remaining = WARMUP_SKIP_COUNT - loop_counter
                print(f"[ISINIYOR] Kalan: {remaining} | T: {avg_temp:.1f} | CO2: {vals['co2']}")
                time.sleep(SENSOR_INTERVAL_SECONDS)
                continue

            # D. KONFOR HESAPLA & GONDER
            comfort = calc_comfort_score(avg_temp, vals['rh'], vals['co2'], voc_index_estimated)

            payload = {
                "place_id": PLACE_ID,
                "recorded_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "temp_c": round(avg_temp, 2),
                "rh_percent": round(vals['rh'], 2),
                "voc_index": int(voc_index_estimated),
                "co2_ppm": int(vals['co2']),
                "pir_occupied": vals['pir'],
                "comfort_score": comfort
            }

            self._send_data(payload)

            # E. FORECAST TETIKLEME
            self._check_forecast()

            elapsed = time.time() - start_t
            time.sleep(max(0, SENSOR_INTERVAL_SECONDS - elapsed))

    def _send_data(self, payload):
        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            r = requests.post(
                f"{PB_BASE_URL}/api/collections/sensor_readings/records", 
                json=payload, headers=headers, timeout=2
            )
            if r.status_code == 401:
                print(">> Token suresi doldu, yenileniyor...")
                self._login()
            elif r.status_code >= 400:
                print(f"[HATA] Sunucu Hatasi: {r.status_code}")
            else:
                print(f"[KAYIT] T:{payload['temp_c']} | CO2:{payload['co2_ppm']} | H:{payload['pir_occupied']}")
        except Exception as e:
            print(f"[HATA] Ag hatasi: {e}")

    def _check_forecast(self):
        if time.time() - self.last_forecast_time > self.forecast_interval:
            print(">> Forecast guncelleniyor...")
            try:
                t = threading.Thread(target=self.forecaster.generate_forecast) # run_cycle degil direkt metod
                t.daemon = True
                t.start()
                self.last_forecast_time = time.time()
            except Exception as e:
                print(f"Forecast baslatilamadi: {e}")

if __name__ == "__main__":
    agent = SensorAgent()
    try:
        agent.loop()
    except KeyboardInterrupt:
        print("\nKapatiliyor...")
        GPIO.cleanup()