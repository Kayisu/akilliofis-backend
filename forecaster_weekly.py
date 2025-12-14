import datetime
import requests
import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestRegressor
from config import PB_BASE_URL

PB_ADMIN_EMAIL = "pi_script@domain.com"
PB_ADMIN_PASSWORD = "12345678"

# --- 1. ÖZEL İSTEMCİ (GÜNCELLENDİ) ---
class ForecasterClient:
    def __init__(self, base_url):
        self.base_url = base_url
        self.token = None

    def login_admin(self, email, password):
        payload = {"identity": email, "password": password}
        
        # 1. Deneme: PocketBase v0.23+ (Yeni Süper Kullanıcılar)
        url = f"{self.base_url}/api/collections/_superusers/auth-with-password"
        
        try:
            r = requests.post(url, json=payload, timeout=10)
            
            # Eğer 404 alırsak, eski versiyon olabilir
            if r.status_code == 404:
                # 2. Deneme: Eski PocketBase (Admins)
                url = f"{self.base_url}/api/admins/auth-with-password"
                r = requests.post(url, json=payload, timeout=10)

            # Hala başarısızsak ve 404 alıyorsak, belki normal kullanıcıdır?
            if r.status_code == 404:
                 # 3. Deneme: Normal Users Koleksiyonu
                url = f"{self.base_url}/api/collections/users/auth-with-password"
                r = requests.post(url, json=payload, timeout=10)

            if r.status_code == 200:
                self.token = r.json().get("token")
                print("✅ Giriş başarılı.")
            else:
                print(f"❌ Giriş başarısız: {r.text}")
                
        except Exception as e:
            print(f"❌ Bağlantı hatası: {e}")

    def _headers(self):
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}" if self.token else ""
        }

    def get_active_places(self):
        url = f"{self.base_url}/api/collections/places/records"
        params = {"filter": "is_active=true", "perPage": 100}
        try:
            r = requests.get(url, headers=self._headers(), params=params)
            return r.json().get("items", [])
        except: return []

    def get_readings(self, place_id, days=30):
        url = f"{self.base_url}/api/collections/sensor_readings/records"
        start_date = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days))
        params = {
            "filter": f"place_id='{place_id}' && created >= '{start_date.strftime('%Y-%m-%d %H:%M:%SZ')}'",
            "sort": "-recorded_at",
            "perPage": 500
        }
        try:
            r = requests.get(url, headers=self._headers(), params=params)
            return r.json().get("items", [])
        except: return []

    def get_reservations(self, place_id):
        url = f"{self.base_url}/api/collections/reservations/records"
        params = {"filter": f"place_id='{place_id}'", "perPage": 500}
        try:
            r = requests.get(url, headers=self._headers(), params=params)
            return r.json().get("items", [])
        except: return []

    def delete_old_forecasts(self, place_id):
        # Toplu silme olmadığı için önce listele sonra sil
        url_list = f"{self.base_url}/api/collections/forecasts/records"
        try:
            r = requests.get(url_list, headers=self._headers(), params={"filter": f"place_id='{place_id}'", "perPage": 200})
            items = r.json().get("items", [])
            for item in items:
                requests.delete(f"{url_list}/{item['id']}", headers=self._headers())
        except: pass

    def create_forecast(self, payload):
        url = f"{self.base_url}/api/collections/forecasts/records"
        try:
            requests.post(url, json=payload, headers=self._headers())
        except Exception as e:
            print(f"⚠️ Yazma hatası: {e}")

# --- 2. FİZİK MOTORU (Konfor Hesaplama) ---
def calc_comfort_score(temp, rh, co2, voc):
    # Sıcaklık Puanı (21-24 arası mükemmel)
    if temp is None: temp = 22.0
    if 21.0 <= temp <= 24.0: t_score = 1.0
    elif 20.0 <= temp < 21.0: t_score = 0.8 + ((temp - 20.0) * 0.2)
    elif 24.0 < temp <= 26.0: t_score = 1.0 - ((temp - 24.0) * 0.15)
    else: t_score = 0.0
    
    # Nem Cezası
    if rh is None: rh = 45.0
    rh_penalty = 0.0
    if rh < 30: rh_penalty = (30 - rh) * 0.005
    elif rh > 60: rh_penalty = (rh - 60) * 0.01
    
    # Hava Kalitesi Puanı
    if co2 is None: co2 = 400
    if voc is None: voc = 50
    
    co2_score = 1.0 if co2 <= 800 else max(0.0, 1.0 - ((co2 - 800) / 1000.0))
    voc_score = 1.0 if voc <= 50 else max(0.0, 1.0 - ((voc - 50) * 0.004))
    
    air_score = (0.75 * co2_score) + (0.25 * voc_score)
    
    # Final Ağırlık (%60 Termal, %40 Hava)
    return round(max(0.0, min(1.0, (0.6 * (t_score - rh_penalty)) + (0.4 * air_score))), 2)

# --- 3. ANA ÇALIŞMA DÖNGÜSÜ ---
def run_weekly_forecast():
    print(f"\n[{datetime.datetime.now().strftime('%H:%M:%S')}] --- HAFTALIK Forecaster Başlatılıyor ---")
    
    client = ForecasterClient(PB_BASE_URL)
    # Otomatik olarak doğru giriş yöntemini bulacaktır
    client.login_admin(PB_ADMIN_EMAIL, PB_ADMIN_PASSWORD)
    
    if not client.token:
        print("❌ Hiçbir giriş yöntemi çalışmadı. Lütfen kullanıcı adı/şifre ve sunucu adresini kontrol edin.")
        return

    places = client.get_active_places()
    print(f"🏢 Analiz edilecek oda sayısı: {len(places)}")

    for place in places:
        print(f"\n>> Oda: {place.get('name')} ({place.get('id')})")
        
        # Verileri Çek
        readings = client.get_readings(place['id'])
        reservations = client.get_reservations(place['id'])
        
        if len(readings) < 50:
            print("   ⚠️ Yetersiz veri, atlanıyor.")
            continue

        # Pandas DataFrame Hazırlığı
        data = []
        res_map = []
        
        for r in reservations:
            try:
                start = datetime.datetime.fromisoformat(r['start_ts'].replace('Z', '+00:00')).replace(tzinfo=None)
                end = datetime.datetime.fromisoformat(r['end_ts'].replace('Z', '+00:00')).replace(tzinfo=None)
                res_map.append({'start': start, 'end': end, 'count': r['attendee_count']})
            except: continue

        for rec in readings:
            try:
                rec_time = datetime.datetime.fromisoformat(rec['recorded_at'].replace('Z', '+00:00')).replace(tzinfo=None)
                
                # O andaki kişi sayısı
                p_count = 0
                for r in res_map:
                    if r['start'] <= rec_time < r['end']:
                        p_count = r['count']
                        break
                
                data.append({
                    'hour': rec_time.hour,
                    'day_of_week': rec_time.weekday(),
                    'person_count': p_count,
                    'temp_c': rec.get('temp_c', 22.0),
                    'co2_ppm': rec.get('co2_ppm', 400),
                    'voc_index': rec.get('voc_index', 50),
                    'rh_percent': rec.get('rh_percent', 45.0)
                })
            except: continue

        if not data:
            print("   ⚠️ Veri işlenemedi.")
            continue

        df = pd.DataFrame(data).fillna(method='ffill').fillna(method='bfill')
        
        # Yapay Zeka Modellerini Eğit
        X = df[['hour', 'day_of_week', 'person_count']]
        
        print("   🧠 Sensör modelleri eğitiliyor...")
        model_temp = RandomForestRegressor(n_estimators=50, random_state=42).fit(X, df['temp_c'])
        model_co2 = RandomForestRegressor(n_estimators=50, random_state=42).fit(X, df['co2_ppm'])
        model_voc = RandomForestRegressor(n_estimators=50, random_state=42).fit(X, df['voc_index'])
        model_rh = RandomForestRegressor(n_estimators=50, random_state=42).fit(X, df['rh_percent'])

        # Gelecek 7 Günü Tahminle
        print("   🔮 7 günlük tahmin oluşturuluyor...")
        client.delete_old_forecasts(place['id'])
        
        now = datetime.datetime.now()
        start_hour = now.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(hours=1)
        
        forecast_count = 0
        for i in range(168): # 7 Gün * 24 Saat
            future_time = start_hour + datetime.timedelta(hours=i)
            
            # Gelecek rezervasyon kontrolü
            future_people = 0
            for r in res_map:
                if r['start'] <= future_time < r['end']:
                    future_people = r['count']
                    break
            
            # Tahmin İste
            input_df = pd.DataFrame([[future_time.hour, future_time.weekday(), future_people]], 
                                  columns=['hour', 'day_of_week', 'person_count'])
            
            pred_temp = float(model_temp.predict(input_df)[0])
            pred_co2 = float(model_co2.predict(input_df)[0])
            pred_voc = float(model_voc.predict(input_df)[0])
            pred_rh = float(model_rh.predict(input_df)[0])
            
            # Fizik Motoru ile Skoru Hesapla
            final_score = calc_comfort_score(pred_temp, pred_rh, pred_co2, pred_voc)
            
            payload = {
                "place_id": place['id'],
                "target_ts": future_time.strftime("%Y-%m-%d %H:%M:%SZ"),
                "predicted_occupancy": future_people,
                "predicted_comfort_score": final_score
            }
            client.create_forecast(payload)
            forecast_count += 1
            if i % 24 == 0: print(".", end="", flush=True)

        print(f"\n   ✅ {forecast_count} saatlik veri yüklendi.")

    print("\n--- Haftalık Analiz Tamamlandı ---")

if __name__ == "__main__":
    run_weekly_forecast()