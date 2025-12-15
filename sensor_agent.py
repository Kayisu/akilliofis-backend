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

# --- MODÜLLER ---
from forecaster import DailyForecaster 
from comfort import calc_comfort_score

# --- DONANIM KÜTÜPHANELERİ ---
# Burada sessizce Mock moduna geçmek yerine, donanım yoksa net bilgi veriyoruz.
try:
    import board
    import adafruit_scd4x
    import bme680
    import RPi.GPIO as GPIO
    HARDWARE_AVAILABLE = True
except ImportError as e:
    print(f"\n[KRİTİK UYARI] Donanım kütüphaneleri eksik: {e}")
    print("Sistem MOCK (Simülasyon) modunda çalışacak ama bu istenen bir durum değil.\n")
    HARDWARE_AVAILABLE = False

class SensorAgent:
    def __init__(self):
        print("--- AKILLI OFIS AJANI (REFACTORED) ---")
        
        # 1. Değişkenler ve Bufferlar (Smoothing için)
        self.token = None
        self.temp_buffer = deque(maxlen=TEMP_HISTORY_LEN or 10)
        self.gas_buffer = deque(maxlen=GAS_HISTORY_LEN or 10)
        
        # 2. Bağlantı
        self._login()
        
        # 3. Donanım Başlatma
        self.scd4x = None
        self.bme = None
        self.pir_pin = 23
        
        if HARDWARE_AVAILABLE:
            self._init_hardware()
        else:
            print(">> Donanım bulunamadığı için Simülasyon verileri kullanılacak.")

        # 4. Zamanlayıcılar ve AI
        self.forecaster = DailyForecaster()
        self.last_forecast_time = 0
        self.forecast_interval = 24 * 3600

    def _login(self):
        """PocketBase Auth İşlemi"""
        try:
            payload = {"identity": PB_ADMIN_EMAIL, "password": PB_ADMIN_PASSWORD}
            # Önce admin olarak dene
            url = f"{PB_BASE_URL}/api/admins/auth-with-password"
            r = requests.post(url, json=payload, timeout=5)
            
            # Olmazsa user (superusers) olarak dene
            if r.status_code == 404:
                 url = f"{PB_BASE_URL}/api/collections/users/auth-with-password"
                 r = requests.post(url, json=payload, timeout=5)
            
            if r.status_code == 200:
                self.token = r.json().get("token")
                print(">> PocketBase Girişi Başarılı")
            else:
                print(f">> PocketBase Giriş Hatası: {r.status_code} - {r.text}")
        except Exception as e: 
            print(f">> Bağlantı Hatası: {e}")

    def _init_hardware(self):
        """Sensörleri başlatır ve konfigüre eder."""
        print(">> Donanım Başlatılıyor...")
        try:
            # SCD41 (CO2)
            i2c = board.I2C()
            self.scd4x = adafruit_scd4x.SCD4X(i2c)
            self.scd4x.start_periodic_measurement()
            print("   - SCD41 Aktif")

            # BME680 (Sıcaklık/Nem/Gaz)
            try:
                self.bme = bme680.BME680(bme680.I2C_ADDR_PRIMARY)
            except IOError:
                self.bme = bme680.BME680(bme680.I2C_ADDR_SECONDARY)
            
            self.bme.set_humidity_oversample(bme680.OS_2X)
            self.bme.set_pressure_oversample(bme680.OS_4X)
            self.bme.set_temperature_oversample(bme680.OS_8X)
            self.bme.set_filter(bme680.FILTER_SIZE_3)
            # Gaz sensörü ısıtıcı ayarları (Daha stabil VOC için)
            self.bme.set_gas_status(bme680.ENABLE_GAS_MEAS)
            self.bme.set_gas_heater_temperature(320)
            self.bme.set_gas_heater_duration(150)
            self.bme.select_gas_heater_profile(0)
            print("   - BME680 Aktif")
            
            # PIR (Hareket)
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.pir_pin, GPIO.IN)
            print("   - PIR Sensörü Aktif")
            
        except Exception as e:
            print(f"!!! DONANIM BAŞLATMA HATASI: {e}")
            print("Sistem çalışmaya devam edecek ama veriler eksik olabilir.")

    def _get_cpu_temperature(self):
        """Pi CPU sıcaklığını okur (Termal düzeltme için)."""
        try:
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                return float(f.read()) / 1000.0
        except:
            return 50.0

    def _read_mock_data(self):
        """Sadece donanım yoksa devreye giren sahte veri üretici."""
        import random
        return {
            "temp": 22.0 + random.uniform(-0.5, 0.5),
            "rh": 45.0 + random.uniform(-2, 2),
            "voc": 50 + random.randint(-5, 5),
            "co2": 450 + random.randint(-10, 10),
            "pir": False
        }

    def read_hardware_data(self):
        """Gerçek sensör okuma mantığı."""
        if not HARDWARE_AVAILABLE:
            return self._read_mock_data()

        # 1. PIR Durumu
        try:
            pir_val = GPIO.input(self.pir_pin)
        except:
            pir_val = False

        # 2. BME680 (Sıcaklık, Nem, VOC)
        temp, rh, voc = None, None, None
        if self.bme and self.bme.get_sensor_data():
            raw_temp = self.bme.data.temperature
            rh = self.bme.data.humidity
            voc = self.bme.data.gas_resistance
            
            # Sıcaklık Düzeltme (CPU ısısı sensörü etkiler)
            cpu_temp = self._get_cpu_temperature()
            if cpu_temp > raw_temp:
                temp = raw_temp - ((cpu_temp - raw_temp) / TEMP_CORRECTION_FACTOR)
            else:
                temp = raw_temp
        
        # 3. SCD41 (CO2)
        co2 = None
        if self.scd4x and self.scd4x.data_ready:
            co2 = self.scd4x.CO2
        
        return {"temp": temp, "rh": rh, "voc": voc, "co2": co2, "pir": bool(pir_val)}

    def loop(self):
        print(f">> Sensör döngüsü başladı. Aralık: {SENSOR_INTERVAL_SECONDS}sn")
        print(f">> ISINMA MODU: İlk {WARMUP_SKIP_COUNT} okuma kaydedilmeyecek (Sensör stabilizasyonu).")

        loop_counter = 0

        while True:
            start_t = time.time()
            loop_counter += 1

            # A. VERİ OKUMA
            vals = self.read_hardware_data()
            
            # Veri bütünlük kontrolü (Sensörler bazen None dönebilir)
            if vals['temp'] is None or vals['co2'] is None:
                print(f"[UYARI] Sensör verisi eksik. Okuma atlanıyor... (Sayaç: {loop_counter})")
                time.sleep(2)
                continue

            # B. SMOOTHING (Veri Yumuşatma)
            self.temp_buffer.append(vals['temp'])
            # VOC değeri dirençtir (Ohm). Ters orantılıdır ama şimdilik direkt alalım.
            if vals['voc']: self.gas_buffer.append(vals['voc'])

            avg_temp = sum(self.temp_buffer) / len(self.temp_buffer)
            # Eğer gas buffer boşsa anlık değeri al
            avg_voc = sum(self.gas_buffer) / len(self.gas_buffer) if self.gas_buffer else vals['voc'] or 0

            # C. ISINMA KONTROLÜ (WARMUP)
            if loop_counter <= WARMUP_SKIP_COUNT:
                remaining = WARMUP_SKIP_COUNT - loop_counter
                print(f"[ISINIYOR] Kalan okuma: {remaining} | Anlık Temp: {vals['temp']:.1f} | CO2: {vals['co2']}")
                time.sleep(SENSOR_INTERVAL_SECONDS)
                continue

            # D. KONFOR VE KAYIT
            # Konfor skoru (comfort.py üzerinden)
            # Not: VOC sensörü Ohm verir, ancak bizim index mantığımız 0-500 arası.
            # BME680 gas_resistance arttıkça hava temizdir. Bunu basite indirgemek için
            # şimdilik bir map yapmıyoruz, ham veriyi (direnci) veya basit bir dönüşümü kullanacağız.
            # Ancak IAQ kütüphanesi (BSEC) olmadan doğrudan Ohm kullanmak zordur.
            # Şimdilik "voc_index" yerine temsili bir değer göndereceğiz veya
            # comfort.py içinde index beklediği için direnci scale edeceğiz:
            # ÖRNEK: 50000 Ohm (Temiz) -> 50 Index, 5000 Ohm (Kirli) -> 300 Index gibi.
            # Burada basit bir ters orantı simülasyonu yapalım gerçek kütüphane yoksa:
            
            # (Basit mapping: 50k Ohm ve üzeri temiz, aşağısı kirleniyor)
            fake_voc_index = max(0, min(500, (50000 - avg_voc) / 100)) if avg_voc else 0
            if fake_voc_index < 0: fake_voc_index = 0 # Negatif olmasın

            comfort = calc_comfort_score(avg_temp, vals['rh'], vals['co2'], fake_voc_index)

            payload = {
                "place_id": PLACE_ID,
                "recorded_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "temp_c": round(avg_temp, 2),
                "rh_percent": round(vals['rh'], 2),
                "voc_index": int(fake_voc_index),
                "co2_ppm": int(vals['co2']),
                "pir_occupied": vals['pir'],
                "comfort_score": comfort
            }

            self._send_data(payload)

            # E. FORECAST SÜRECİ (Arka planda)
            self._check_forecast()

            # Bekleme
            elapsed = time.time() - start_t
            time.sleep(max(0, SENSOR_INTERVAL_SECONDS - elapsed))

    def _send_data(self, payload):
        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            r = requests.post(
                f"{PB_BASE_URL}/api/collections/sensor_readings/records", 
                json=payload, headers=headers, timeout=3
            )
            if r.status_code >= 400:
                print(f"[HATA] Veri Gönderilemedi: {r.status_code}")
                # Token süresi dolmuş olabilir, tekrar login
                if r.status_code == 401: self._login()
            else:
                print(f"[KAYIT] T:{payload['temp_c']} | CO2:{payload['co2_ppm']} | S:{payload['comfort_score']}")
        except Exception as e:
            print(f"[HATA] Bağlantı sorunu: {e}")

    def _check_forecast(self):
        if time.time() - self.last_forecast_time > self.forecast_interval:
            print(">> Tahmin (Forecast) döngüsü tetikleniyor...")
            t = threading.Thread(target=self.forecaster.run_cycle)
            t.daemon = True
            t.start()
            self.last_forecast_time = time.time()

if __name__ == "__main__":
    agent = SensorAgent()
    try:
        agent.loop()
    except KeyboardInterrupt:
        print("\nKapatılıyor...")
        GPIO.cleanup()