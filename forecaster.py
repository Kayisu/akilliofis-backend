import datetime
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from config import PB_BASE_URL, PB_ADMIN_EMAIL, PB_ADMIN_PASSWORD, PLACE_ID
from pb_client import PBClient

def run_forecast_cycle():
    print(f"\n[{datetime.datetime.now().strftime('%H:%M:%S')}] --- Forecaster Başlatılıyor ---")
    client = PBClient(base_url=PB_BASE_URL)
    
    try:
        client.login_with_password(PB_ADMIN_EMAIL, PB_ADMIN_PASSWORD)
    except Exception as e:
        print(f"[Forecaster] Kritik: Login hatası: {e}")
        return

    # Senin eklediğin metod burada kullanılıyor
    print("[Forecaster] Geçmiş veriler çekiliyor...")
    records = client.get_historical_readings(days=7)
    
    if len(records) < 50:
        print(f"[Forecaster] Yetersiz veri ({len(records)} kayıt). En az 50 kayıt gerekli. Çıkılıyor.")
        return

    # --- Veri Hazırlığı ---
    df = pd.DataFrame(records)
    
    # PocketBase'den gelen tarih stringini datetime'a çevir
    df['recorded_at'] = pd.to_datetime(df['recorded_at'])
    
    # Feature Engineering (Saat ve Haftanın Günü)
    df['hour'] = df['recorded_at'].dt.hour
    df['day_of_week'] = df['recorded_at'].dt.dayofweek
    
    # Boolean -> Int
    if 'pir_occupied' in df.columns:
        df['pir_occupied'] = df['pir_occupied'].astype(int)
    else:
        # Eski verilerde sütun yoksa 0 bas
        df['pir_occupied'] = 0
    
    # Eksik verileri temizle
    if df['comfort_score'].isnull().any():
        df['comfort_score'] = df['comfort_score'].fillna(df['comfort_score'].mean())

    print(f"[Forecaster] Model eğitiliyor (Veri Seti: {len(df)} satır)...")
    
    X = df[['hour', 'day_of_week']]
    y_occ = df['pir_occupied']
    y_comf = df['comfort_score']
    
    # Modeller
    model_occ = RandomForestRegressor(n_estimators=100, random_state=42, n_jobs=-1)
    model_occ.fit(X, y_occ)
    
    model_comf = RandomForestRegressor(n_estimators=100, random_state=42, n_jobs=-1)
    model_comf.fit(X, y_comf)

    # --- Gelecek 24 Saat Tahmini ---
    now = datetime.datetime.now(datetime.timezone.utc)
    # Dakika ve saniyeyi sıfırla, önümüzdeki tam saatten başla
    current_hour = now.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(hours=1)
    
    print("[Forecaster] Gelecek 24 saat tahminleniyor ve DB'ye yazılıyor...")
    
    forecast_count = 0
    for i in range(24): # 24 saatlik döngü
        future_time = current_hour + datetime.timedelta(hours=i)
        
        # Modele girecek veri
        input_data = pd.DataFrame([[future_time.hour, future_time.weekday()]], 
                                columns=['hour', 'day_of_week'])
        
        pred_occ = float(model_occ.predict(input_data)[0])
        pred_comf = float(model_comf.predict(input_data)[0])
        
        # Sınırlandırmalar (Clamping)
        pred_occ = max(0.0, min(1.0, pred_occ))
        pred_comf = max(0.0, min(1.0, pred_comf))
        
        # Konsola örnek çıktı (ilk 3 saat ve son saat)
        if i < 3 or i == 23:
            print(f"   -> {future_time.strftime('%H:%M')} | Doluluk: %{int(pred_occ*100)} | Konfor: {pred_comf:.2f}")

        # DB'ye Yaz
        try:
            client.create_forecast(
                target_ts=future_time,
                occupancy=pred_occ,
                comfort=pred_comf
            )
            forecast_count += 1
        except Exception as e:
            print(f"[Forecaster] Yazma hatası ({future_time}): {e}")

    print(f"[Forecaster] Tamamlandı. {forecast_count} adet tahmin oluşturuldu.")

if __name__ == "__main__":
    run_forecast_cycle()