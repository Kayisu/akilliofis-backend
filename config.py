import os

# PocketBase Ayarları
PB_BASE_URL = "http://100.96.191.83:8090/"
PB_ADMIN_EMAIL = "pi_agent@domain.com"
PB_ADMIN_PASSWORD = "12345678"

# Ofis/Mekan Ayarı
PLACE_ID = "jat8nmi4h0bsii0"

# Sensör Döngü Ayarları
SENSOR_INTERVAL_SECONDS = 5
STARTUP_DELAY_SECONDS = 30 

# Sıcaklık Kalibrasyonu
TEMP_CORRECTION_FACTOR = 0.85

# --- Isınma ve Filtreleme Ayarları ---
WARMUP_SKIP_COUNT = 30 # İlk 2.5 dakika kayıt yapma

# Hareketli Ortalama Uzunlukları (Smoothing)
# Son 10 verinin ortalamasını alarak ani zıplamaları önler.
GAS_HISTORY_LEN = 10 
TEMP_HISTORY_LEN = 10  # <-- YENİ: Sıcaklık için tampon bellek