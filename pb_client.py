# pb_client.py

import requests
import datetime
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from config import PB_BASE_URL, PLACE_ID

@dataclass
class PBClient:
    base_url: str
    token: Optional[str] = None
    user_id: Optional[str] = None
    is_admin: bool = False

    def login_with_password(self, email: str, password: str):
        url = f"{self.base_url}/api/collections/users/auth-with-password"
        payload = {"identity": email, "password": password}

        try:
            r = requests.post(url, json=payload, timeout=10)
            data = r.json()
        except Exception as e:
            print(f"[PB] Giriş hatası: {e}")
            return

        if "token" not in data:
            print(f"[PB] Token alınamadı. Yanıt: {data}")
            return

        self.token = data["token"]
        self.user_id = data["record"]["id"]
        print(f"[PB] Giriş başarılı. ID: {self.user_id}")

    def _auth_headers(self):
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}" if self.token else ""
        }

    def create_sensor_reading(self, payload: dict):
        url = f"{self.base_url}/api/collections/sensor_readings/records"
        body = dict(payload)
        body["place_id"] = PLACE_ID
        try:
            requests.post(url, json=body, headers=self._auth_headers(), timeout=5)
        except Exception as e:
            print(f"[PB] Okuma gönderme hatası: {e}")

    # --- Tahmin İçin Gerekli Metodlar ---

    def get_recent_readings(self, limit=20) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/api/collections/sensor_readings/records"
        params = {
            "sort": "-created",
            "limit": limit,
            "filter": f"place_id='{PLACE_ID}'"
        }
        try:
            r = requests.get(url, params=params, headers=self._auth_headers(), timeout=5)
            return r.json().get("items", [])
        except Exception:
            return []

    def create_forecast(self, target_ts: datetime.datetime, occupancy: float, comfort: float):
        url = f"{self.base_url}/api/collections/forecasts/records"
        ts_str = target_ts.strftime("%Y-%m-%d %H:%M:%SZ")
        payload = {
            "place_id": PLACE_ID,
            "target_ts": ts_str,
            "predicted_occupancy": round(occupancy, 2),
            "predicted_comfort_score": round(comfort, 2)
        }
        try:
            requests.post(url, json=payload, headers=self._auth_headers(), timeout=5)
        except Exception as e:
            print(f"[PB] Tahmin gönderme hatası: {e}")
            
    def get_historical_readings(self, days=7) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/api/collections/sensor_readings/records"
        
        # PocketBase tarih formatı UTC gerektirir
        start_date = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days))
        start_str = start_date.strftime("%Y-%m-%d %H:%M:%SZ")
        
        params = {
            "sort": "-created",
            "perPage": 500, # İhtiyaca göre artırılabilir
            "filter": f"place_id='{PLACE_ID}' && created >= '{start_str}'"
        }
        try:
            r = requests.get(url, params=params, headers=self._auth_headers(), timeout=10)
            return r.json().get("items", [])
        except Exception as e:
            print(f"[PB] Geçmiş veri çekme hatası: {e}")
            return []