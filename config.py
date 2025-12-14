import os


PB_BASE_URL = "http://100.96.191.83:8090/"  # RPi IP'si veya localhost
PB_ADMIN_EMAIL = "pi_agent@domain.com"
PB_ADMIN_PASSWORD = "12345678"

PLACE_ID = "jat8nmi4h0bsii0" 

# Sensör Döngü Ayarları
SENSOR_INTERVAL_SECONDS = 5
STARTUP_DELAY_SECONDS = 3  # RPi açıldıktan sonra sensörlerin elektriği alması için bekleme

# Sıcaklık Kalibrasyonu
# CPU ısısı sensörü etkilediği için düşülecek miktar 
TEMP_CORRECTION_FACTOR = 0.9

# Bu süre boyunca veriler sadece loglanır
WARMUP_SKIP_COUNT = 30

# VOC (Gaz) verisi için hareketli ortalama (Smoothing) hafıza uzunluğu
# Son 10 verinin ortalamasını alır.
GAS_HISTORY_LEN = 10