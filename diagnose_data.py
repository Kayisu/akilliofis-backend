import datetime
import pandas as pd
import requests

PB_BASE_URL = "http://100.96.191.83:8090"
PB_ADMIN_EMAIL = "pi_script@domain.com"
PB_ADMIN_PASSWORD = "12345678"

class Client:
    def __init__(self):
        self.token = None
        self.login()

    def login(self):
        # Superuser ve Admin endpointlerini dene
        endpoints = [
            "/api/collections/_superusers/auth-with-password",
            "/api/admins/auth-with-password"
        ]
        for ep in endpoints:
            try:
                r = requests.post(f"{PB_BASE_URL}{ep}", 
                                json={"identity": PB_ADMIN_EMAIL, "password": PB_ADMIN_PASSWORD})
                if r.status_code == 200: 
                    self.token = r.json()["token"]
                    print(f"Giriş başarılı ({ep})")
                    return
            except: pass

    def get_all(self, collection):
        items = []
        page = 1
        while True:
            try:
                r = requests.get(f"{PB_BASE_URL}/api/collections/{collection}/records", 
                               headers={"Authorization": self.token},
                               params={"page": page, "perPage": 500, "sort": "-created"})
                data = r.json()
                items.extend(data.get("items", []))
                if page >= data.get("totalPages", 0): break
                page += 1
            except: break
        return items

def diagnose():
    print("--- VERİ TEŞHİS RAPORU ---")
    client = Client()
    if not client.token:
        print("HATA: Giriş yapılamadı.")
        return

    # 1. Veri Sayıları
    readings = client.get_all("sensor_readings")
    reservations = client.get_all("reservations")
    print(f"Toplam Sensör Verisi: {len(readings)}")
    print(f"Toplam Rezervasyon: {len(reservations)}")

    if not readings or not reservations:
        print("HATA: Veri eksik. mock_gen.py çalıştırılmamış olabilir.")
        return

    # 2. Zaman Örtüşmesi Kontrolü
    print("\n--- Zaman Analizi ---")
    
    # İlk 5 rezervasyonu ve sensör verisini karşılaştır
    res_times = []
    for r in reservations[:50]: # Son 50 rezervasyon
        s = datetime.datetime.fromisoformat(r['start_ts'].replace('Z', '+00:00')).replace(tzinfo=None)
        e = datetime.datetime.fromisoformat(r['end_ts'].replace('Z', '+00:00')).replace(tzinfo=None)
        res_times.append((s, e, r['attendee_count']))

    match_count = 0
    total_checked = 0
    
    for rec in readings[:500]: # Son 500 okuma
        t = datetime.datetime.fromisoformat(rec['recorded_at'].replace('Z', '+00:00')).replace(tzinfo=None)
        total_checked += 1
        
        is_occupied = False
        for start, end, count in res_times:
            if start <= t < end:
                is_occupied = True
                match_count += 1
                break
    
    print(f"Kontrol Edilen Sensör Verisi: {total_checked}")
    print(f"Rezervasyonla Eşleşen (Dolu) Veri: {match_count}")
    
    if match_count == 0:
        print("\n!!! KRİTİK SORUN TESPİT EDİLDİ !!!")
        print("Sensör verileri ile Rezervasyon saatleri hiç örtüşmüyor.")
        print("Olası Sebepler:")
        print("1. mock_gen.py rezervasyon oluştururken saatleri yanlış kaydetti.")
        print("2. Timezone (Saat Dilimi) farkı var (UTC vs Local).")
        
        print("\nÖrnek Veriler:")
        if res_times:
            print(f"Rezervasyon Aralığı: {res_times[0][0]} - {res_times[0][1]}")
        if readings:
            t = datetime.datetime.fromisoformat(readings[0]['recorded_at'].replace('Z', '+00:00')).replace(tzinfo=None)
            print(f"Sensör Zamanı: {t}")
    else:
        print("\nDurum: Veriler örtüşüyor, eğitim yapılabilir.")
        print(f"Doluluk Oranı (Örneklem): %{match_count/total_checked*100:.1f}")

if __name__ == "__main__":
    diagnose()
