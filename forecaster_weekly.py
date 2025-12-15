import datetime
import requests
import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestRegressor

PB_BASE_URL = "http://100.96.191.83:8090"
PB_ADMIN_EMAIL = "pi_script@domain.com"
PB_ADMIN_PASSWORD = "12345678"

def calculate_thermal_score(temp_c: float, rh: float) -> float:
    """ASHRAE Standard 55 Bazlı Puanlama."""
    if temp_c is None or rh is None: return 0.0

    # --- 1. Sıcaklık Puanı ---
    if 21.0 <= temp_c <= 24.0:
        t_score = 1.0
    elif 20.0 <= temp_c < 21.0:
        t_score = 0.8 + ((temp_c - 20.0) * 0.2)
    elif 24.0 < temp_c <= 26.0:
        t_score = 1.0 - ((temp_c - 24.0) * 0.15)
    elif temp_c < 18.0 or temp_c > 30.0:
        t_score = 0.0
    else:
        if temp_c < 20.0:
            t_score = 0.5 + ((temp_c - 18.0) * 0.15)
        else:
            t_score = 0.7 - ((temp_c - 26.0) * 0.175)

    # --- 2. Nem Cezası ---
    rh_penalty = 0.0
    if rh < 30:
        rh_penalty = (30 - rh) * 0.005 
    elif rh > 60:
        rh_penalty = (rh - 60) * 0.01
    
    return max(0.0, t_score - rh_penalty)

def calculate_iaq_score(co2: float, voc_index: float) -> float:
    """WELL Building Standard & UBA Bazlı."""
    # --- 1. CO2 Puanı ---
    if co2 is None: co2_score = 0.0
    elif co2 <= 800:
        co2_score = 1.0
    elif 800 < co2 <= 1000:
        co2_score = 1.0 - ((co2 - 800) * 0.001) 
    elif 1000 < co2 <= 1500:
        co2_score = 0.80 - ((co2 - 1000) * 0.0006)
    else: 
        co2_score = max(0.0, 0.50 - ((co2 - 1500) * 0.0005))

    # --- 2. VOC Puanı ---
    if voc_index is None: voc_score = 0.5
    elif voc_index <= 50:
        voc_score = 1.0
    elif voc_index <= 100:
        voc_score = 1.0 - ((voc_index - 50) * 0.004)
    elif voc_index <= 200:
        voc_score = 0.8 - ((voc_index - 100) * 0.004)
    else:
        voc_score = max(0.0, 0.4 - ((voc_index - 200) * 0.002))

    return (0.75 * co2_score) + (0.25 * voc_score)

def calc_comfort_score(temp, rh, co2, voc):
    """Genel Skor (ASHRAE 55 + WELL + Yumuşak Geçiş)"""
    t_score = calculate_thermal_score(temp, rh)
    air_score = calculate_iaq_score(co2, voc)

    base_score = (0.6 * t_score) + (0.4 * air_score)

    # --- YUMUŞAK CEZA (Soft Penalty) ---
    # Keskin sınırlar yerine, limit aşıldıkça artan cezalar uyguluyoruz.
    
    # 1. CO2 Cezası: 1000 ppm'den sonra her 100 ppm için %2 puan kır (Daha yumuşak)
    if co2 is not None and co2 > 1000:
        penalty = (co2 - 1000) / 100.0 * 0.02
        base_score -= penalty

    # 2. Sıcaklık Cezası: İdeal aralıktan uzaklaştıkça ek ceza (Daha yumuşak)
    if temp is not None:
        if temp < 18.0:
            base_score -= (18.0 - temp) * 0.05
        elif temp > 28.0:
            base_score -= (temp - 28.0) * 0.05

    # 3. VOC Cezası
    if voc is not None and voc > 150:
        base_score -= (voc - 150) / 50.0 * 0.02

    return round(max(0.1, min(1.0, base_score)), 2)

class ForecasterClient:
    def __init__(self, base_url):
        self.base_url = base_url
        self.token = None

    def login(self, email, password):
        # Farkli endpointleri deneyerek giris yapmaya calisir
        endpoints = [
            "/api/collections/_superusers/auth-with-password",
            "/api/admins/auth-with-password",
            "/api/collections/users/auth-with-password"
        ]
        payload = {"identity": email, "password": password}
        
        for ep in endpoints:
            try:
                r = requests.post(f"{self.base_url}{ep}", json=payload, timeout=5)
                if r.status_code == 200:
                    self.token = r.json().get("token")
                    print("Giris basarili.")
                    return
            except: pass
        print("Giris basarisiz! Sunucu adresi veya sifreyi kontrol edin.")

    def _headers(self):
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}" if self.token else ""
        }

    def get_records(self, collection, filter_str="", sort="-created", limit=500):
        url = f"{self.base_url}/api/collections/{collection}/records"
        params = {"filter": filter_str, "sort": sort, "perPage": limit}
        try:
            r = requests.get(url, headers=self._headers(), params=params)
            return r.json().get("items", [])
        except: return []

    def clear_old_forecasts(self, place_id):
        # Eski tahminleri temizle
        items = self.get_records("forecasts", f"place_id='{place_id}'", limit=200)
        for item in items:
            try:
                requests.delete(f"{self.base_url}/api/collections/forecasts/records/{item['id']}", headers=self._headers())
            except: pass

    def create_forecast(self, payload):
        url = f"{self.base_url}/api/collections/forecasts/records"
        try:
            requests.post(url, json=payload, headers=self._headers())
        except: pass

# --- ANA PROGRAM ---
def run_weekly_forecast():
    print(f"--- HAFTALIK TAHMIN MOTORU BASLATILDI ---")
    
    client = ForecasterClient(PB_BASE_URL)
    client.login(PB_ADMIN_EMAIL, PB_ADMIN_PASSWORD)
    
    if not client.token: return

    # Aktif odalari bul
    places = client.get_records("places", "is_active=true")
    print(f"Islemlere baslaniyor. Oda sayisi: {len(places)}")

    for place in places:
        print(f"\n>> Oda Analizi: {place.get('name')}")
        
        # Son 30 gunun verisini cek
        start_date = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30))
        start_date_str = start_date.strftime('%Y-%m-%d %H:%M:%SZ')
        
        readings = client.get_records("sensor_readings", f"place_id='{place['id']}' && created >= '{start_date_str}'", limit=1000)
        # Rezervasyon limitini artırdık ki hem geçmişi hem geleceği alabilsin
        reservations = client.get_records("reservations", f"place_id='{place['id']}'", limit=1000)
        
        if len(readings) < 50:
            print("   Yetersiz veri, bu oda atlaniyor.")
            continue

        # Veriyi işle (Pandas DataFrame)
        data = []
        
        # Rezervasyonlari hafizaya al
        res_list = []
        for r in reservations:
            try:
                s = datetime.datetime.fromisoformat(r['start_ts'].replace('Z', '+00:00')).replace(tzinfo=None)
                e = datetime.datetime.fromisoformat(r['end_ts'].replace('Z', '+00:00')).replace(tzinfo=None)
                res_list.append({'start': s, 'end': e, 'count': r['attendee_count']})
            except: continue

        # Sensor verilerini isle
        for rec in readings:
            try:
                t = datetime.datetime.fromisoformat(rec['recorded_at'].replace('Z', '+00:00')).replace(tzinfo=None)
                
                # O andaki kisi sayisi (rezervasyonlardan bul)
                p_count = 0
                for r in res_list:
                    if r['start'] <= t < r['end']:
                        p_count = r['count']
                        break
                
                data.append({
                    'hour': t.hour,
                    'day_of_week': t.weekday(),
                    'person_count': p_count,
                    'temp_c': rec.get('temp_c', 22.0),
                    'co2_ppm': rec.get('co2_ppm', 400),
                    'voc_index': rec.get('voc_index', 50),
                    'rh_percent': rec.get('rh_percent', 45.0)
                })
            except: continue

        df = pd.DataFrame(data)
        if df.empty: continue
        
        # Eksik verileri doldur (Interpolasyon)
        df = df.fillna(method='ffill').fillna(method='bfill')

        # Yapay Zeka Modellerini Egit
        print("   Modeller egitiliyor (Random Forest)...")
        X = df[['hour', 'day_of_week', 'person_count']]
        
        # Doluluk Tahmin Modeli (Rezervasyon yoksa geçmişten öğrensin)
        X_occ = df[['hour', 'day_of_week']]
        model_occ = RandomForestRegressor(n_estimators=50, random_state=42).fit(X_occ, df['person_count'])
        
        model_temp = RandomForestRegressor(n_estimators=50, random_state=42).fit(X, df['temp_c'])
        model_co2 = RandomForestRegressor(n_estimators=50, random_state=42).fit(X, df['co2_ppm'])
        model_voc = RandomForestRegressor(n_estimators=50, random_state=42).fit(X, df['voc_index'])
        model_rh = RandomForestRegressor(n_estimators=50, random_state=42).fit(X, df['rh_percent'])

        # Tahminleri Olustur ve Yukle
        client.clear_old_forecasts(place['id'])
        
        # Zaman Dilimi Düzeltmesi (UTC)
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        start_prediction = now.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(hours=1)
        
        # Gelecek rezervasyon kontrolü
        future_res_exists = any(r['start'] > now for r in res_list)
        if not future_res_exists:
            print("\n   [BİLGİ] Gelecek rezervasyon yok. Doluluk geçmiş verilerden tahmin edilecek.")

        print("   Gelecek 7 gun icin tahminler yukleniyor...", end="")
        
        # Adım 1: Ham Doluluk Verilerini Hesapla
        raw_occupancies = []
        future_times = []
        
        for i in range(168):
            future_time = start_prediction + datetime.timedelta(hours=i)
            future_times.append(future_time)
            
            # Rezervasyon Kontrolü
            future_people = 0
            has_reservation = False
            for r in res_list:
                if r['start'] <= future_time < r['end']:
                    future_people = r['count']
                    has_reservation = True
                    break
            
            # Rezervasyon Yoksa Geçmişten Tahmin Et
            if not has_reservation:
                occ_input = pd.DataFrame([[future_time.hour, future_time.weekday()]], columns=['hour', 'day_of_week'])
                predicted_occ = model_occ.predict(occ_input)[0]
                future_people = max(0, predicted_occ) # Round yapma, float kalsın
                
                # [YENİ] "Hayalet" Doluluk: Mesai saatlerinde (08-19) tamamen 0 olmasın
                # Kullanıcı "çok fazla 0 var" dediği için, mesai saatlerinde minik bir hareketlilik ekliyoruz.
                if 8 <= future_time.hour <= 19:
                    # Eğer tahmin çok düşükse (0.5'ten az), rastgele ufak bir doluluk ekle (0.5 - 1.5 arası)
                    if future_people < 0.5:
                        import random
                        future_people = random.uniform(0.5, 1.5)

            raw_occupancies.append(future_people)

        # Adım 2: Doluluk Verilerini Yumuşat (Smoothing)
        # Ani sıçramaları engellemek için hareketli ortalama penceresini genişlettik (3 -> 5)
        # win_type='gaussian' kullanarak daha doğal bir tepe noktası oluşturuyoruz
        smoothed_occupancies = pd.Series(raw_occupancies).rolling(window=5, min_periods=1, center=True, win_type='gaussian').mean(std=2).tolist()
        # Eğer gaussian hata verirse veya nan dönerse düz ortalamaya dönmek için fillna
        if any(pd.isna(smoothed_occupancies)):
             smoothed_occupancies = pd.Series(raw_occupancies).rolling(window=5, min_periods=1, center=True).mean().tolist()

        # Adım 3: Çevresel Tahminleri Yap ve Kaydet
        count = 0
        for i in range(168):
            future_time = future_times[i]
            # Yumuşatılmış doluluk değerini kullan
            smooth_people = smoothed_occupancies[i]
            
            # Çevresel modeller için input (float doluluk kullanıyoruz ki geçişler yumuşak olsun)
            input_row = pd.DataFrame([[future_time.hour, future_time.weekday(), smooth_people]], 
                                   columns=['hour', 'day_of_week', 'person_count'])
            
            pred_temp = float(model_temp.predict(input_row)[0])
            pred_co2 = float(model_co2.predict(input_row)[0])
            pred_voc = float(model_voc.predict(input_row)[0])
            pred_rh = float(model_rh.predict(input_row)[0])
            
            final_score = calc_comfort_score(pred_temp, pred_rh, pred_co2, pred_voc)
            
            # Kapasiteye göre oran hesabı (0.0 - 1.0 arası)
            capacity = place.get('capacity', 10) # Varsayılan 10
            if capacity <= 0: capacity = 10
            
            # [YENİ] Zorunlu Mesai Doluluğu (Minimum %10-20)
            # Eğer saat 08:00 - 19:00 arasındaysa ve haftasonu değilse, doluluk 0 olmasın.
            if 8 <= future_time.hour <= 19 and future_time.weekday() < 5:
                 min_people = capacity * 0.15 # Kapasitenin %15'i taban değer
                 if smooth_people < min_people:
                     # 0.15 ile 0.25 arası rastgele bir taban oluştur
                     import random
                     smooth_people = capacity * random.uniform(0.15, 0.25)

            occupancy_ratio = smooth_people / capacity
            occupancy_ratio = max(0.0, min(1.0, occupancy_ratio)) # 0.0 - 1.0 arası sınırla

            payload = {
                "place_id": place['id'],
                "target_ts": future_time.strftime("%Y-%m-%d %H:%M:%SZ"),
                "predicted_occupancy": occupancy_ratio, # Float (0.0 - 1.0)
                "predicted_comfort_score": final_score
            }
            client.create_forecast(payload)
            
            count += 1
            if count % 24 == 0: print(".", end="", flush=True)

        print(" Tamam.")

if __name__ == "__main__":
    run_weekly_forecast()