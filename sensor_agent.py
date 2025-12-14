import time
import datetime
import board
import busio
from gpiozero import MotionSensor
from adafruit_bme680 import Adafruit_BME680_I2C
from adafruit_scd4x import SCD4X

from config import (
    PB_ADMIN_EMAIL, PB_ADMIN_PASSWORD, PB_BASE_URL,
    SENSOR_INTERVAL_SECONDS, TEMP_CORRECTION_FACTOR, 
    STARTUP_DELAY_SECONDS, PLACE_ID
)
from comfort import calc_comfort_score
from pb_client import PBClient

# Sensör ısındıktan sonra bile ilk okumalar sapabilir.
WARMUP_SKIP_COUNT = 12 

def setup_sensors():
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

    pir = MotionSensor(17)
    return bme, scd4x, pir

def get_cpu_temperature() -> float:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            return float(f.read()) / 1000.0
    except:
        return 50.0

def process_gas_resistance(gas_ohms):
    """
    BME680'den gelen ham Gaz Direncini (Ohm), 0-500 arası VOC İndeksine çevirir.
    Mantık: 
    - Yüksek Ohm (Temiz) -> Düşük Index (İyi)
    - Düşük Ohm (Kirli) -> Yüksek Index (Kötü)
    """
    if gas_ohms is None:
        return 50.0 # Varsayılan: Temiz hava

    # Referans Değerler (Basitleştirilmiş)
    # 50.000 Ohm ve üzeri = Mükemmel Hava (Index 25)
    # 10.000 Ohm = Kötüye giden hava (Index 200)
    # 5.000 Ohm = Çok Kötü hava (Index 500)
    
    if gas_ohms >= 50000:
        return 25.0 # Çok İyi
    
    # 50k ile 5k arasını lineer olarak Index'e (50 -> 500) ters orantıla
    # Formül: y = mx + c yaklaşımı
    # Eğim hesabı: (500 - 50) / (5000 - 50000) = 450 / -45000 = -0.01
    
    index = (-0.01 * gas_ohms) + 550
    
    # Sınırlandırma (Clamping)
    return max(0.0, min(500.0, index))

def main():
    print(f">>> Sistem başlatıldı. Donanım hazırlığı için {STARTUP_DELAY_SECONDS} sn bekleniyor...")
    time.sleep(STARTUP_DELAY_SECONDS)
    
    print(">>> Sensörler nesneleri oluşturuluyor...")
    bme, scd4x, pir = setup_sensors()

    if not pir:
        print("Sensör hatası (I2C), çıkılıyor.")
        return

    print(">>> PocketBase bağlantısı kuruluyor...")
    client = PBClient(base_url=PB_BASE_URL)
    
    logged_in = False
    while not logged_in:
        try:
            client.login_with_password(PB_ADMIN_EMAIL, PB_ADMIN_PASSWORD)
            if client.token:
                logged_in = True
            else:
                print("[PB] Token alınamadı, 10sn sonra tekrar denenecek...")
                time.sleep(10)
        except Exception as e:
            print(f"[PB] Bağlantı hatası: {e}. 10sn sonra tekrar denenecek...")
            time.sleep(10)

    reading_counter = 0

    while True:
        start_time = time.time()
        loop_ts = datetime.datetime.now(datetime.timezone.utc)
        
        #Sensör Okuma
        co2, raw_temp, hum, gas_ohms = None, None, None, 0.0
        
        if scd4x and scd4x.data_ready:
            try:
                co2 = float(scd4x.CO2)
                raw_temp = float(scd4x.temperature) 
                hum = float(scd4x.relative_humidity)
            except: pass

        if bme:
            try:
                if raw_temp is None: raw_temp = float(bme.temperature)
                if hum is None: hum = float(bme.humidity)
                gas_ohms = float(bme.gas) 
            except: pass

        if raw_temp is None:
            print("[Uyarı] Sensörlerden veri alınamadı.")
            time.sleep(SENSOR_INTERVAL_SECONDS)
            continue

        # Isınma Kontrolü
        if reading_counter < WARMUP_SKIP_COUNT:
            print(f"[Isınma Modu] ({reading_counter + 1}/{WARMUP_SKIP_COUNT}) | Ohm: {gas_ohms:.0f}")
            reading_counter += 1
            time.sleep(SENSOR_INTERVAL_SECONDS)
            continue

        #Veri İşleme ve Dönüştürme 
        cpu_temp = get_cpu_temperature()
        comp_temp = raw_temp
        
        if cpu_temp > raw_temp:
            comp_temp = raw_temp - ((cpu_temp - raw_temp) / TEMP_CORRECTION_FACTOR)
        
        
        voc_index = process_gas_resistance(gas_ohms)
        
        is_occupied = pir.motion_detected
        safe_co2 = co2 if co2 else 400
        
        c_score = calc_comfort_score(comp_temp, hum, safe_co2, voc_index)

        payload = {
            "recorded_at": loop_ts.strftime("%Y-%m-%d %H:%M:%SZ"),
            "pir_occupied": is_occupied,
            "temp_c": round(comp_temp, 2),
            "rh_percent": round(hum, 2) if hum else 0,
            "voc_index": round(voc_index, 0), 
            "co2_ppm": co2 if co2 else 0,
            "comfort_score": c_score,
        }

        log_msg = (
            f"[{loop_ts.strftime('%H:%M:%S')}] "
            f"Net:{comp_temp:.1f}°C | "
            f"Nem:%{hum:.0f} | "
            f"VOC_Ohm:{gas_ohms:.0f} -> Index:{voc_index:.0f} | " 
            f"Skor:{c_score:.2f}" 
        )
        print(log_msg)

        try:
            client.create_sensor_reading(payload)
        except Exception as e:
            print(f"[Hata] Veri gönderimi başarısız: {e}")

        elapsed = time.time() - start_time
        time.sleep(max(0, SENSOR_INTERVAL_SECONDS - elapsed))

if __name__ == "__main__":
    main()