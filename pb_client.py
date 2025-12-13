# pb_client.py

import requests
from dataclasses import dataclass
from typing import Optional, List, Dict
import datetime

# Config dosyasından sabitleri alıyoruz
from config import PB_BASE_URL, PLACE_ID

@dataclass
class PBClient:
    base_url: str
    token: Optional[str] = None
    user_id: Optional[str] = None
    is_admin: bool = False

    def login_with_password(self, email: str, password: str):
        # ... (Mevcut kod aynen kalacak) ...
        url = f"{self.base_url}/api/collections/users/auth-with-password"
        payload = {"identity": email, "password": password}
        
        try:
            r = requests.post(url, json=payload, timeout=5)
            r.raise_for_status() # Hata varsa yakalayalım
            data = r.json()
            
            self.token = data["token"]
            self.user_id = data["record"]["id"]
            self.is_admin = bool(data["record"].get("isAdmin", False))
            print(f"[PB] Login OK. user_id={self.user_id}")
            
        except Exception as e:
            print(f"[PB] Login Hatası: {e}")

    def _auth_headers(self):
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def create_sensor_reading(self, payload: dict):
        # ... (Mevcut kod aynen kalacak) ...
        url = f"{self.base_url}/api/collections/sensor_readings/records"
        body = dict(payload)
        body["place_id"] = PLACE_ID
        try:
            r = requests.post(url, json=body, headers=self._auth_headers(), timeout=5)
            return r
        except Exception as e:
            print(f"[PB] Sensor Write Error: {e}")

    # --- YENİ EKLENEN FONKSİYONLAR ---

    def get_historical_readings(self, days=7) -> List[Dict]:
        """
        Son X günün sensör verilerini çeker.
        """
        # UTC zaman damgası hesaplama
        start_date = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
        start_str = start_date.strftime("%Y-%m-%d %H:%M:%SZ")
        
        # Filtre: Sadece bu ofis (place_id) ve son X gün
        filter_str = f'place_id="{PLACE_ID}" && recorded_at >= "{start_str}"'
        
        url = f"{self.base_url}/api/collections/sensor_readings/records"
        
        all_records = []
        page = 1
        
        print(f"[PB] Geçmiş veri çekiliyor... ({days} gün)")
        
        while True:
            params = {
                "filter": filter_str,
                "sort": "recorded_at",
                "perPage": 500, # Sayfa başı kayıt sayısı
                "page": page
            }
            
            try:
                r = requests.get(url, headers=self._auth_headers(), params=params, timeout=10)
                r.raise_for_status()
                data = r.json()
                items = data.get("items", [])
                
                if not items:
                    break
                    
                all_records.extend(items)
                
                if page >= data.get("totalPages", 1):
                    break
                    
                page += 1
                
            except Exception as e:
                print(f"[PB] Geçmiş veri okuma hatası: {e}")
                break
                
        print(f"[PB] Toplam {len(all_records)} adet geçmiş kayıt çekildi.")
        return all_records

    def clear_future_forecasts(self):
        """
        Eski veya çakışan tahminleri temizlemek için (Opsiyonel ama temizlik için iyi)
        Şimdilik basitlik adına sadece create yapacağız, ama ileri seviyede
        önce geleceğe dair eski tahminleri silmek gerekebilir.
        """
        pass 

    def create_forecast(self, target_ts: str, occupancy: float, comfort: float):
        """
        Tek bir tahmin kaydı oluşturur.
        """
        url = f"{self.base_url}/api/collections/forecasts/records"
        
        payload = {
            "place_id": PLACE_ID,
            "target_ts": target_ts,
            "predicted_occupancy": round(occupancy, 2),
            "predicted_comfort_score": round(comfort, 2)
        }
        
        try:
            r = requests.post(url, json=payload, headers=self._auth_headers(), timeout=5)
            if r.status_code not in [200, 204]:
                print(f"[PB] Forecast kayıt hatası: {r.text}")
        except Exception as e:
            print(f"[PB] Forecast connection error: {e}")


