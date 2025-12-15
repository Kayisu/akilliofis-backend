import datetime
import requests
import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestRegressor
from config import PB_BASE_URL, PB_ADMIN_EMAIL, PB_ADMIN_PASSWORD, PLACE_ID
from comfort import calc_comfort_score # <-- YENI: Ortak mantik

class DailyForecaster:
    def __init__(self):
        self.base_url = PB_BASE_URL
        self.token = None

    def _login(self):
        payload = {"identity": PB_ADMIN_EMAIL, "password": PB_ADMIN_PASSWORD}
        endpoints = [
            "/api/collections/_superusers/auth-with-password",
            "/api/admins/auth-with-password",
            "/api/collections/users/auth-with-password"
        ]
        for ep in endpoints:
            try:
                r = requests.post(f"{self.base_url}{ep}", json=payload, timeout=10)
                if r.status_code == 200:
                    self.token = r.json().get("token")
                    return True
            except: pass
        return False

    def _headers(self):
        return {"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"}

    def run_cycle(self):
        print("   [Forecaster] Tahmin döngüsü başladı...")
        if not self._login():
            print("   [Forecaster] Giriş yapılamadı, iptal.")
            return

        try:
            # 1. Veri Cekme
            start_dt = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%SZ')
            
            def get_data(col, flt):
                r = requests.get(f"{self.base_url}/api/collections/{col}/records", 
                               headers=self._headers(), 
                               params={"filter": flt, "perPage": 1000, "sort": "-created"})
                return r.json().get("items", [])

            readings = get_data("sensor_readings", f"place_id='{PLACE_ID}' && created >= '{start_dt}'")
            reservations = get_data("reservations", f"place_id='{PLACE_ID}'")

            if len(readings) < 50:
                print("   [Forecaster] Yetersiz veri.")
                return

            # 2. Veri Hazirlama
            data = []
            res_list = []
            for r in reservations:
                s = datetime.datetime.fromisoformat(r['start_ts'].replace('Z', '+00:00')).replace(tzinfo=None)
                e = datetime.datetime.fromisoformat(r['end_ts'].replace('Z', '+00:00')).replace(tzinfo=None)
                res_list.append({'start': s, 'end': e, 'count': r['attendee_count']})

            for r in readings:
                t = datetime.datetime.fromisoformat(r['recorded_at'].replace('Z', '+00:00')).replace(tzinfo=None)
                p_count = 0
                for res in res_list:
                    if res['start'] <= t < res['end']:
                        p_count = res['count']
                        break
                data.append({
                    'hour': t.hour, 'day': t.weekday(), 'count': p_count,
                    'temp': r.get('temp_c', 22), 'co2': r.get('co2_ppm', 400),
                    'rh': r.get('rh_percent', 45), 'voc': r.get('voc_index', 50)
                })

            df = pd.DataFrame(data).fillna(method='ffill').fillna(method='bfill')

            # 3. Model Egitimi
            X = df[['hour', 'day', 'count']]
            models = {
                'temp': RandomForestRegressor(n_estimators=20).fit(X, df['temp']),
                'co2': RandomForestRegressor(n_estimators=20).fit(X, df['co2']),
                'rh': RandomForestRegressor(n_estimators=20).fit(X, df['rh']),
                'voc': RandomForestRegressor(n_estimators=20).fit(X, df['voc'])
            }

            # 4. Eski Tahminleri Temizle
            old_forecasts = get_data("forecasts", f"place_id='{PLACE_ID}'")
            for item in old_forecasts:
                requests.delete(f"{self.base_url}/api/collections/forecasts/records/{item['id']}", headers=self._headers())

            # 5. Yeni Tahminleri Yukle (24 Saat)
            now = datetime.datetime.now()
            start_future = now.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(hours=1)
            
            url_post = f"{self.base_url}/api/collections/forecasts/records"
            
            for i in range(24):
                ft = start_future + datetime.timedelta(hours=i)
                ft_count = 0
                for res in res_list:
                    if res['start'] <= ft < res['end']:
                        ft_count = res['count']
                        break
                
                inp = pd.DataFrame([[ft.hour, ft.weekday(), ft_count]], columns=['hour', 'day', 'count'])
                
                preds = {k: float(v.predict(inp)[0]) for k, v in models.items()}
                
                # <-- YENI: comfort.py fonksiyonu kullaniliyor
                score = calc_comfort_score(preds['temp'], preds['rh'], preds['co2'], preds['voc'])
                
                payload = {
                    "place_id": PLACE_ID,
                    "target_ts": ft.strftime("%Y-%m-%d %H:%M:%SZ"),
                    "predicted_occupancy": ft_count,
                    "predicted_comfort_score": score
                }
                requests.post(url_post, json=payload, headers=self._headers())
            
            print("   [Forecaster] Başarıyla tamamlandı.")

        except Exception as e:
            print(f"   [Forecaster] Hata: {e}")