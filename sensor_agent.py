# sensor_agent.py

import time
import datetime
import board
import busio
from typing import Optional, Tuple

# Donanım Kütüphaneleri
from gpiozero import MotionSensor  # LED importu kaldırıldı
from adafruit_bme680 import Adafruit_BME680_I2C
from adafruit_scd4x import SCD4X

# Proje Modülleri
from config import (
    PB_ADMIN_EMAIL,
    PB_ADMIN_PASSWORD,
    PB_BASE_URL,
    SENSOR_INTERVAL_SECONDS,
)
from comfort import calc_comfort_score
from pb_client import PBClient

# --- Tahmin Motoru Entegrasyonu ---
try:
    from forecaster import run_forecast_cycle
    FORECASTER_AVAILABLE = True
except ImportError as e:
    print(f"[WARN] Tahmin modülü yüklenemedi: {e}")
    print("[WARN] Sistem sadece sensör okuyarak devam edecek.")
    FORECASTER_AVAILABLE = False

FORECAST_INTERVAL_SECONDS = 86400 


def setup_sensors():
    """I2C ve GPIO üzerinden sensörleri başlatır."""
    i2c = busio.I2C(board.SCL, board.SDA)

    # BME680
    try:
        bme = Adafruit_BME680_I2C(i2c, address=0x77)
    except Exception:
        print("[Info] BME680 0x77 adresinde bulunamadı, 0x76 deneniyor...")
        bme = Adafruit_BME680_I2C(i2c, address=0x76)
        
    bme.sea_level_pressure = 1013.25

    # SCD41 (CO2)
    scd4x = SCD4X(i2c)
    scd4x.start_periodic_measurement()

    # PIR (Hareket) 
    pir = MotionSensor(17)
    
    # LED tanımlaması kaldırıldı
    
    return bme, scd4x, pir

def try_read_scd4x(scd4x: SCD4X) -> Optional[Tuple[float, float, float]]:
    try:
        if not scd4x.data_ready:
            return None
        return float(scd4x.CO2), float(scd4x.temperature), float(scd4x.relative_humidity)
    except Exception:
        return None


def main():
    # PocketBase İstemcisi Başlatma
    client = PBClient(base_url=PB_BASE_URL)
    
    print("\n--- Sensör verileri gönderiliyor ---")
    print(f"[Init] Hedef: {PB_BASE_URL}")

    # Login
    try:
        client.login_with_password(PB_ADMIN_EMAIL, PB_ADMIN_PASSWORD)
    except Exception as e:
        print(f"[CRITICAL] PocketBase Login Hatası: {e}")
        print("İnternet bağlantısını veya sunucuyu kontrol et.")
        return

    # Sensörleri Başlatma
    try:
        # setup_sensors artık sadece 3 değer dönüyor
        bme, scd4x, pir = setup_sensors()
        print("[Init] Sensörler başarıyla tanımlandı.")
    except Exception as e:
        print(f"[CRITICAL] Sensör başlatma hatası: {e}")
        return

    last_forecast_time = 0 

    print("[Main] Ana döngüye giriliyor...")

    while True:
        try:
            loop_ts = datetime.datetime.now(datetime.timezone.utc)
            current_epoch = time.time()

            # Tahmin Kontrolü 
            if FORECASTER_AVAILABLE and (current_epoch - last_forecast_time >= FORECAST_INTERVAL_SECONDS):
                print(f"\n>>> [{loop_ts.strftime('%H:%M')}] Tahmin Döngüsü Tetiklendi <<<")
                try:
                    # forecaster.py içindeki ana fonksiyon
                    run_forecast_cycle()
                    last_forecast_time = time.time() # Zamanlayıcıyı sıfırla
                except Exception as e:
                    print(f"[Forecast Error] Tahmin sırasında hata: {e}")
                print(">>> Tahmin tamamlandı, sensör takibine dönülüyor.\n")

            
            # sensör okuma / veri gönderme
            
            # SCD41
            scd_data = try_read_scd4x(scd4x)
            if scd_data:
                co2, scd_temp, scd_hum = scd_data
            else:
                co2, scd_temp, scd_hum = None, None, None

            # BME680 
            try:
                bme_temp = float(bme.temperature)
                bme_hum = float(bme.humidity)
                bme_gas = float(bme.gas)
            except Exception as e:
                print(f"[Sensor Error] BME680 okuma hatası: {e}")
                time.sleep(1)
                continue

            voc_index = bme_gas / 1000.0

            # PIR 
            hareket = pir.motion_detected

            # sıcaklık ve nem 
            final_temp = scd_temp if scd_temp is not None else bme_temp
            final_hum = scd_hum if scd_hum is not None else bme_hum

            # konfor skoru
            comfort_score = None
            if final_temp is not None and co2 is not None:
                comfort_score = calc_comfort_score(final_temp, final_hum, co2, voc_index)

            recorded_at_str = loop_ts.strftime("%Y-%m-%d %H:%M:%SZ")
            
            payload = {
                "recorded_at": recorded_at_str,
                "pir_occupied": bool(hareket),
                "temp_c": round(final_temp, 2),
                "rh_percent": round(final_hum, 2),
                "voc_index": round(voc_index, 2),
                "co2_ppm": co2,                 
                "comfort_score": comfort_score,  
            }

            # log 
            status_symbol = "🟢" if hareket else "⚪"
            print(
                f"[{loop_ts.strftime('%H:%M:%S')}] {status_symbol} "
                f"T:{payload['temp_c']}°C | RH:%{payload['rh_percent']} | "
                f"CO2:{co2 or '---'} | VOC:{voc_index:.1f} | "
                f"Konfor:{comfort_score or '---'}"
            )

            # pocketbase'e veri gönderme
            client.create_sensor_reading(payload)

        except KeyboardInterrupt:
            print("\n[Stop] Kullanıcı tarafından durduruldu.")
            break
        except Exception as e:
            print(f"[Main Loop Error] Beklenmeyen hata: {e}")

        time.sleep(SENSOR_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()