# forecaster.py
import datetime
import requests
import pandas as pd
import numpy as np
import random
from sklearn.ensemble import RandomForestRegressor
from config import PB_BASE_URL, PB_ADMIN_EMAIL, PB_ADMIN_PASSWORD, PLACE_ID
from comfort import calc_comfort_score

class WeeklyForecaster:
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

    def get_records(self, collection, filter_str="", sort="-created", limit=500):
        url = f"{self.base_url}/api/collections/{collection}/records"
        params = {"filter": filter_str, "sort": sort, "perPage": limit}
        try:
            r = requests.get(url, headers=self._headers(), params=params)
            return r.json().get("items", [])
        except: return []

    def clear_old_forecasts(self, place_id):
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

    def run_cycle(self):
        print(f"--- WEEKLY FORECAST CYCLE STARTED ---")
        
        if not self._login():
            print("   [Forecaster] Login failed.")
            return

        target_places = []
        if PLACE_ID:
            try:
                r = requests.get(f"{self.base_url}/api/collections/places/records/{PLACE_ID}", headers=self._headers())
                if r.status_code == 200:
                    target_places.append(r.json())
            except: pass
        
        if not target_places:
            print("   [Forecaster] No place found.")
            return

        for place in target_places:
            print(f"\n>> Analyzing Place: {place.get('name')}")
            
            start_date = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30))
            start_date_str = start_date.strftime('%Y-%m-%d %H:%M:%SZ')
            
            readings = self.get_records("sensor_readings", f"place_id='{place['id']}' && created >= '{start_date_str}'", limit=1000)
            reservations = self.get_records("reservations", f"place_id='{place['id']}'", limit=1000)
            
            if len(readings) < 50:
                print("   Insufficient data, skipping.")
                continue

            data = []
            res_list = []
            for r in reservations:
                try:
                    s = datetime.datetime.fromisoformat(r['start_ts'].replace('Z', '+00:00')).replace(tzinfo=None)
                    e = datetime.datetime.fromisoformat(r['end_ts'].replace('Z', '+00:00')).replace(tzinfo=None)
                    res_list.append({'start': s, 'end': e, 'count': r['attendee_count']})
                except: continue

            for rec in readings:
                try:
                    t = datetime.datetime.fromisoformat(rec['recorded_at'].replace('Z', '+00:00')).replace(tzinfo=None)
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
            
            df = df.fillna(method='ffill').fillna(method='bfill')

            print("   Training models (Random Forest)...")
            X = df[['hour', 'day_of_week', 'person_count']]
            X_occ = df[['hour', 'day_of_week']]
            
            model_occ = RandomForestRegressor(n_estimators=50, random_state=42).fit(X_occ, df['person_count'])
            model_temp = RandomForestRegressor(n_estimators=50, random_state=42).fit(X, df['temp_c'])
            model_co2 = RandomForestRegressor(n_estimators=50, random_state=42).fit(X, df['co2_ppm'])
            model_voc = RandomForestRegressor(n_estimators=50, random_state=42).fit(X, df['voc_index'])
            model_rh = RandomForestRegressor(n_estimators=50, random_state=42).fit(X, df['rh_percent'])

            self.clear_old_forecasts(place['id'])
            
            now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
            start_prediction = now.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(hours=1)
            
            print("   Uploading 7-day forecast...", end="")
            
            raw_occupancies = []
            future_times = []
            
            for i in range(168):
                future_time = start_prediction + datetime.timedelta(hours=i)
                future_times.append(future_time)
                
                future_people = 0
                has_reservation = False
                for r in res_list:
                    if r['start'] <= future_time < r['end']:
                        future_people = r['count']
                        has_reservation = True
                        break
                
                if not has_reservation:
                    occ_input = pd.DataFrame([[future_time.hour, future_time.weekday()]], columns=['hour', 'day_of_week'])
                    predicted_occ = model_occ.predict(occ_input)[0]
                    future_people = max(0, predicted_occ) 
                    
                    if 8 <= future_time.hour <= 19:
                        if future_people < 0.5:
                            future_people = random.uniform(0.5, 1.5)

                raw_occupancies.append(future_people)

            smoothed_occupancies = pd.Series(raw_occupancies).rolling(window=5, min_periods=1, center=True, win_type='gaussian').mean(std=2).tolist()
            if any(pd.isna(smoothed_occupancies)):
                 smoothed_occupancies = pd.Series(raw_occupancies).rolling(window=5, min_periods=1, center=True).mean().tolist()

            count = 0
            for i in range(168):
                future_time = future_times[i]
                smooth_people = smoothed_occupancies[i]
                
                input_row = pd.DataFrame([[future_time.hour, future_time.weekday(), smooth_people]], 
                                       columns=['hour', 'day_of_week', 'person_count'])
                
                pred_temp = float(model_temp.predict(input_row)[0])
                pred_co2 = float(model_co2.predict(input_row)[0])
                pred_voc = float(model_voc.predict(input_row)[0])
                pred_rh = float(model_rh.predict(input_row)[0])
                
                final_score = calc_comfort_score(pred_temp, pred_rh, pred_co2, pred_voc)
                
                capacity = place.get('capacity', 10)
                if capacity <= 0: capacity = 10
                
                if 8 <= future_time.hour <= 19 and future_time.weekday() < 5:
                     min_people = capacity * 0.15 
                     if smooth_people < min_people:
                         smooth_people = capacity * random.uniform(0.15, 0.25)

                occupancy_ratio = smooth_people / capacity
                occupancy_ratio = max(0.0, min(1.0, occupancy_ratio))

                payload = {
                    "place_id": place['id'],
                    "target_ts": future_time.strftime("%Y-%m-%d %H:%M:%SZ"),
                    "predicted_occupancy": occupancy_ratio,
                    "predicted_comfort_score": final_score
                }
                self.create_forecast(payload)
                
                count += 1
                if count % 24 == 0: print(".", end="", flush=True)

            print(" Done.")