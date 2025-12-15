import requests
from config import PB_BASE_URL, PB_ADMIN_EMAIL, PB_ADMIN_PASSWORD

def check_place_schema():
    # 1. Login as Admin
    auth_url = f"{PB_BASE_URL}/api/admins/auth-with-password"
    try:
        r = requests.post(auth_url, json={"identity": PB_ADMIN_EMAIL, "password": PB_ADMIN_PASSWORD})
        token = r.json()['token']
    except:
        # Try user login if admin fails (though config says admin)
        auth_url = f"{PB_BASE_URL}/api/collections/users/auth-with-password"
        r = requests.post(auth_url, json={"identity": PB_ADMIN_EMAIL, "password": PB_ADMIN_PASSWORD})
        token = r.json()['token']

    headers = {"Authorization": f"Bearer {token}"}
    
    # 2. Get Places
    r = requests.get(f"{PB_BASE_URL}/api/collections/places/records", headers=headers)
    places = r.json().get('items', [])
    
    if places:
        print("Place Data:", places[0])
    else:
        print("No places found.")

if __name__ == "__main__":
    check_place_schema()