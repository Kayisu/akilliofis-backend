import datetime
import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestRegressor
from config import PB_BASE_URL, PB_ADMIN_EMAIL, PB_ADMIN_PASSWORD
from pb_client import PBClient

# --- FİZİK MOTORU (COMFORT.PY MANTIĞI) ---
def calculate_thermal_score(temp_c, rh):
    if temp_c is None or rh is None: return 0.0
    # Sıcaklık Puanı
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
            
    # Nem Cezası
    rh_penalty = 0.0
    if rh < 30: rh_penalty = (30 - rh) * 0.005 
    elif rh > 60: rh_penalty = (rh - 60) * 0.01
    
    return max(0.0, t_score - rh_penalty)

def calculate_iaq_score(co2, voc_index):
    # CO2 Puanı
    if co2 is None: co2_score = 0.0
    elif co2 <= 800: co2_score = 1.0
    elif co2 <= 1000: co2_score = 1.0 - ((co2 - 800) * 0.001) 
    elif co2 <= 1500: co2_score = 0.80 - ((co2 - 1000) * 0.0006)
    else: co2_score = max(0.0, 0.50 - ((co2 - 1500) * 0.0005))

    # VOC Puanı
    if voc_index is None: voc_score = 0.5
    elif voc_index <= 50: voc_score = 1.0
    elif voc_index <= 100: voc_score = 1.0 - ((voc_index - 50) * 0.004)
    elif voc_index <= 200: voc_score = 0.8 - ((voc_index - 100) * 0.004)
    else: voc_score = max(0.0, 0.4 - ((voc_index - 200) * 0.002))

    return (0.75 * co2_score) + (0.25 * voc_score)

def calc_derived_comfort(temp_c, rh, co2, voc):
    t_score = calculate_thermal_score(temp_c, rh)
    air_score = calculate_iaq_score(co2, voc)
    base_score = (0.6 * t_score) + (0.4 * air_score)
    return round(max(0.0, min(1.0, base_score)), 2)

# --- HAFTALIK TAHMİN ---

def run_weekly_forecast():
    print(f"\n[{datetime.datetime.now().strftime('%H:%M:%S')}] --- HAFTALIK Forecaster (7 Gun) Baslatiliyor ---")
    
    client = PBClient(base_url=PB_BASE_URL)
    try:
        client.login_with_password(PB_ADMIN_EMAIL, PB_ADMIN_PASSWORD)
    except Exception as e:
        print(f"[Forecaster] Kritik: Login hatasi: {e}")
        return

    # Aktif Odaları Bul
    try:
        places = client.client.collection("places").get_full_list(query_params={"filter": "is_active=true"})
    except:
        print("[Forecaster] Oda listesi cekilemedi.")
        return

    for place in places:
        print(f"\n>> Oda Analizi (Haftalik): {place.name}")
        
        # 1. Gecmis Verileri Cek
        try:
            readings = client.client.collection("sensor_readings").get_full_list(
                query_params={"filter": f"place_id='{place.id}'", "sort": "-recorded_at"}
            )
            reservations = client.client.collection("reservations").get_full_list(
                query_params={"filter": f"place_id='{place.id}'"}
            )
        except:
            continue

        if len(readings) < 50:
            print(f"   [Atlandi] Yetersiz veri.")
            continue

        # 2. Egitim Veri Setini Hazirla
        data = []
        res_map = []
        for r in reservations:
            # Tarihleri naive datetime'a cevir
            start = datetime.datetime.fromisoformat(r.start_ts.replace('Z', '+00:00')).replace(tzinfo=None)
            end = datetime.datetime.fromisoformat(r.end_ts.replace('Z', '+00:00')).replace(tzinfo=None)
            res_map.append({'start': start, 'end': end, 'count': r.attendee_count})

        for record in readings:
            if isinstance(record.recorded_at, str):
                rec_time = datetime.datetime.fromisoformat(record.recorded_at.replace('Z', '+00:00')).replace(tzinfo=None)
            else:
                rec_time = record.recorded_at.replace(tzinfo=None)
            
            # Gecmis rezervasyon durumu
            person_count = 0
            for r in res_map:
                if r['start'] <= rec_time < r['end']:
                    person_count = r['count']
                    break
            
            data.append({
                'hour': rec_time.hour,
                'day_of_week': rec_time.weekday(),
                'person_count': person_count,
                # Hedefler: Fiziksel degerler
                'temp_c': record.temp_c,
                'co2_ppm': record.co2_ppm,
                'voc_index': record.voc_index,
                'rh_percent': record.rh_percent
            })

        df = pd.DataFrame(data)
        df = df.fillna(method='ffill').fillna(method='bfill')

        # 3. Modelleri Egit (Sensör Davranislari)
        X = df[['hour', 'day_of_week', 'person_count']]
        
        print("   [Egitim] Sensor modelleri egitiliyor...")
        model_temp = RandomForestRegressor(n_estimators=50, random_state=42).fit(X, df['temp_c'])
        model_co2 = RandomForestRegressor(n_estimators=50, random_state=42).fit(X, df['co2_ppm'])
        model_voc = RandomForestRegressor(n_estimators=50, random_state=42).fit(X, df['voc_index'])
        model_rh = RandomForestRegressor(n_estimators=50, random_state=42).fit(X, df['rh_percent'])

        # 4. Gelecek 7 Gunu Simule Et
        # Once eski tahminleri temizle
        try:
            olds = client.client.collection("forecasts").get_full_list(query_params={"filter": f"place_id='{place.id}'"})
            for o in olds: client.client.collection("forecasts").delete(o.id)
        except: pass

        now = datetime.datetime.now()
        current_hour = now.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(hours=1)
        
        print("   [Simulasyon] 7 Gunluk (168 saat) tahmin olusturuluyor...")
        
        # 7 Gun * 24 Saat = 168 Iterasyon
        forecast_count = 0
        for i in range(24 * 7):
            future_time = current_hour + datetime.timedelta(hours=i)
            
            # Gelecek rezervasyon kontrolu
            future_people = 0
            for r in res_map:
                if r['start'] <= future_time < r['end']:
                    future_people = r['count']
                    break
            
            # Model Girdisi
            input_data = pd.DataFrame([[future_time.hour, future_time.weekday(), future_people]], 
                                    columns=['hour', 'day_of_week', 'person_count'])
            
            # A. Sensor Degerlerini Tahmin Et
            pred_temp = float(model_temp.predict(input_data)[0])
            pred_co2 = float(model_co2.predict(input_data)[0])
            pred_voc = float(model_voc.predict(input_data)[0])
            pred_rh = float(model_rh.predict(input_data)[0])

            # B. Konfor Skorunu Hesapla
            final_comfort_score = calc_derived_comfort(pred_temp, pred_rh, pred_co2, pred_voc)
            
            # C. Kaydet
            forecast_data = {
                "place_id": place.id,
                "target_ts": future_time.isoformat(),
                "predicted_occupancy": future_people,
                "predicted_comfort_score": final_comfort_score
            }
            
            try:
                client.client.collection("forecasts").create(forecast_data)
                forecast_count += 1
                # Ilerleme gostergesi (her 24 saatte bir nokta koy)
                if i % 24 == 0: print(".", end="", flush=True)
            except: pass

        print(f"\n   [Tamam] {forecast_count} saatlik veri sisteme yuklendi.")

    print(f"\n[Forecaster] Haftalik analiz tamamlandi.")

if __name__ == "__main__":
    run_weekly_forecast()