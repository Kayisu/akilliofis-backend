from pocketbase import PocketBase

# --- AYARLAR ---
PB_URL = "http://100.96.191.83:8090"
ADMIN_EMAIL = "pi_script@domain.com" 
ADMIN_PASS = "12345678"

def wipe_all():
    client = PocketBase(PB_URL)
    
    print(f"{PB_URL} adresine bağlanılıyor...")
    
    try:
        # Admin Girişi
        client.admins.auth_with_password(ADMIN_EMAIL, ADMIN_PASS)
        print("Giriş başarılı.")
        
        # --- 1. SENSÖR VERİLERİNİ SİL (Hepsini) ---
        print("Tüm sensör verileri aranıyor...")
        # batch boyutu yüksek tutuldu
        readings = client.collection("sensor_readings").get_full_list()
        
        if len(readings) > 0:
            print(f"Toplam {len(readings)} sensör verisi bulundu.")
            if input("HEPSİNİ SİLMEK için 'sil' yazın: ") == "sil":
                print("Siliniyor...", end="")
                for item in readings:
                    try:
                        client.collection("sensor_readings").delete(item.id)
                        print(".", end="", flush=True)
                    except: pass
                print("\nSensör tablosu tertemiz oldu.")
            else:
                print("İptal edildi.")
        else:
            print("Sensör tablosu zaten boş.")

        # --- 2. REZERVASYONLARI SİL (Hepsini) ---
        print("\nTüm rezervasyonlar aranıyor...")
        reservations = client.collection("reservations").get_full_list()
        
        if len(reservations) > 0:
            print(f"Toplam {len(reservations)} rezervasyon bulundu.")
            if input("HEPSİNİ SİLMEK için 'sil' yazın: ") == "sil":
                print("Siliniyor...", end="")
                for item in reservations:
                    try:
                        client.collection("reservations").delete(item.id)
                        print(".", end="", flush=True)
                    except: pass
                print("\nRezervasyon tablosu tertemiz oldu.")
            else:
                print("İptal edildi.")
        else:
            print("Rezervasyon tablosu zaten boş.")

        # --- 3. TAHMİNLERİ SİL (Hepsini) ---
        print("\nTüm tahminler aranıyor...")
        forecasts = client.collection("forecasts").get_full_list()
        
        if len(forecasts) > 0:
            print(f"Toplam {len(forecasts)} tahmin bulundu.")
            if input("HEPSİNİ SİLMEK için 'sil' yazın: ") == "sil":
                print("Siliniyor...", end="")
                for item in forecasts:
                    try:
                        client.collection("forecasts").delete(item.id)
                        print(".", end="", flush=True)
                    except: pass
                print("\nTahmin tablosu tertemiz oldu.")
            else:
                print("İptal edildi.")
        else:
            print("Tahmin tablosu zaten boş.")

    except Exception as e:
        print(f"\nHata: {e}")

if __name__ == "__main__":
    wipe_all()