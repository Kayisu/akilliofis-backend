import datetime
import requests
import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestRegressor

PB_BASE_URL = "http://100.96.191.83:8090"
PB_ADMIN_EMAIL = "pi_script@domain.com"
PB_ADMIN_PASSWORD = "12345678"

def calc_comfort_score(temp, rh, co2, voc):
    # Sicaklik Puani (21-24 arasi ideal)
    if temp is None: temp = 22.0
    if 21.0 <= temp <= 24.0: t_score = 1.0
    else:
        diff = min(abs(temp - 21.0), abs(temp - 24.0))
        t_score = max(0.0, 1.0 - (diff * 0.25))
    
    # Hava Kalitesi Puani
    if co2 is None: co2 = 400
    if co2 <= 800: air_score = 1.0
    else: air_score = max(0.0, 1.0 - ((co2 - 800) / 1200.0))
    
    # Basit agirlikli ortalama
    return round(max(0.0, min(1.0, (0.6 * t_score) + (0.4 * air_score))), 2)

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
        reservations = client.get_records("reservations", f"place_id='{place['id']}'")
        
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
        
        model_temp = RandomForestRegressor(n_estimators=50, random_state=42).fit(X, df['temp_c'])
        model_co2 = RandomForestRegressor(n_estimators=50, random_state=42).fit(X, df['co2_ppm'])
        model_voc = RandomForestRegressor(n_estimators=50, random_state=42).fit(X, df['voc_index'])
        model_rh = RandomForestRegressor(n_estimators=50, random_state=42).fit(X, df['rh_percent'])

        # Tahminleri Olustur ve Yukle
        client.clear_old_forecasts(place['id'])
        
        now = datetime.datetime.now()
        start_prediction = now.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(hours=1)
        
        print("   Gelecek 7 gun icin tahminler yukleniyor...", end="")
        
        count = 0
        for i in range(168): # 7 gun * 24 saat
            future_time = start_prediction + datetime.timedelta(hours=i)
            
            # Gelecekteki doluluk tahmini (Rezervasyonlardan)
            future_people = 0
            for r in res_list:
                if r['start'] <= future_time < r['end']:
                    future_people = r['count']
                    break
            
            input_row = pd.DataFrame([[future_time.hour, future_time.weekday(), future_people]], 
                                   columns=['hour', 'day_of_week', 'person_count'])
            
            pred_temp = float(model_temp.predict(input_row)[0])
            pred_co2 = float(model_co2.predict(input_row)[0])
            pred_voc = float(model_voc.predict(input_row)[0])
            pred_rh = float(model_rh.predict(input_row)[0])
            
            final_score = calc_comfort_score(pred_temp, pred_rh, pred_co2, pred_voc)
            
            payload = {
                "place_id": place['id'],
                "target_ts": future_time.strftime("%Y-%m-%d %H:%M:%SZ"),
                "predicted_occupancy": future_people,
                "predicted_comfort_score": final_score
            }
            client.create_forecast(payload)
            
            count += 1
            if count % 24 == 0: print(".", end="", flush=True)

        print(" Tamam.")

if __name__ == "__main__":
    run_weekly_forecast()