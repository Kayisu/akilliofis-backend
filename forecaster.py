# forecaster.py

import datetime
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from config import PB_BASE_URL, PB_ADMIN_EMAIL, PB_ADMIN_PASSWORD
from pb_client import PBClient

def run_forecast_cycle():
    print("\n--- [Forecaster] Tahmin döngüsü tetiklendi ---")
    client = PBClient(base_url=PB_BASE_URL)
    
    # Login olmayı dene
    try:
        client.login_with_password(PB_ADMIN_EMAIL, PB_ADMIN_PASSWORD)
    except Exception as e:
        print(f"[Forecaster] Login hatası: {e}")
        return

    # Veri çek
    records = client.get_historical_readings(days=7)
    if len(records) < 50:
        print("[Forecaster] Yetersiz veri (min 50 kayıt). Pas geçiliyor.")
        return

    # Veri Hazırlığı
    df = pd.DataFrame(records)
    df['recorded_at'] = pd.to_datetime(df['recorded_at'])
    df['hour'] = df['recorded_at'].dt.hour
    df['day_of_week'] = df['recorded_at'].dt.dayofweek
    
    # Pir sensörünü sayısal yap (True->1, False->0)
    df['pir_occupied'] = df['pir_occupied'].astype(int)
    
    # Eksik konfor verilerini ortalama ile doldur
    if df['comfort_score'].isnull().any():
        df['comfort_score'].fillna(df['comfort_score'].mean(), inplace=True)

    # Eğitim
    print(f"[Forecaster] Model eğitiliyor... (Veri: {len(df)} satır)")
    X = df[['hour', 'day_of_week']]
    
    # Doluluk Modeli
    model_occ = RandomForestRegressor(n_estimators=50, random_state=42)
    model_occ.fit(X, df['pir_occupied'])
    
    # Konfor Modeli
    model_comf = RandomForestRegressor(n_estimators=50, random_state=42)
    model_comf.fit(X, df['comfort_score'])

    # Tahmin (Gelecek 24 Saat)
    now = datetime.datetime.now(datetime.timezone.utc)
    current_hour = now.replace(minute=0, second=0, microsecond=0)
    
    print("[Forecaster] Tahminler veritabanına yazılıyor:")
    
    for i in range(1, 169):
        future_time = current_hour + datetime.timedelta(hours=i)
        
        # Girdi hazırla
        input_data = pd.DataFrame([[future_time.hour, future_time.weekday()]], 
                                columns=['hour', 'day_of_week'])
        
        pred_occ = model_occ.predict(input_data)[0]
        pred_comf = model_comf.predict(input_data)[0]
        
        # --- İŞTE BU SATIRI GERİ EKLEDİK ---
        print(f"   -> Saat {future_time.hour:02d}:00 | Doluluk: %{int(pred_occ*100)} | Konfor: {pred_comf:.2f}")
        
        # Kaydet
        client.create_forecast(
            target_ts=future_time.strftime("%Y-%m-%d %H:%M:%SZ"),
            occupancy=pred_occ,
            comfort=pred_comf
        )
        
    print("[Forecaster] Döngü başarıyla tamamlandı.\n")


