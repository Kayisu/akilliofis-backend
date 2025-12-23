#comfort.py
def calculate_thermal_score(temp_c: float, rh: float) -> float:
    """
    ASHRAE Standard 55 (Ofis/Sedanter Çalışma) Bazlı Puanlama.
    Mükemmel Aralık: 21°C - 24°C
    Kabul Edilebilir: 20°C - 26°C
    """
    if temp_c is None or rh is None: return 0.0

    # --- Sıcaklık Puanı ---
    if 21.0 <= temp_c <= 24.0:
        t_score = 1.0  # Altın Bölge
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

    # --- Nem Cezası (ASHRAE) ---
    rh_penalty = 0.0
    if rh < 30:
        rh_penalty = (30 - rh) * 0.005 
    elif rh > 60:
        rh_penalty = (rh - 60) * 0.01
    
    final_score = max(0.0, t_score - rh_penalty)
    return final_score

def calculate_iaq_score(co2: float, voc_index: float) -> float:
    """
    WELL Building Standard & UBA Bazlı.
    """
    # --- CO2 Puanı (WELL Standardı) ---
    if co2 is None: co2_score = 0.0
    elif co2 <= 800:
        co2_score = 1.0
    elif 800 < co2 <= 1000:
        co2_score = 1.0 - ((co2 - 800) * 0.001) 
    elif 1000 < co2 <= 1500:
        co2_score = 0.80 - ((co2 - 1000) * 0.0006)
    else: # > 1500
        co2_score = max(0.0, 0.50 - ((co2 - 1500) * 0.0005))

    # --- VOC Puanı (UBA Sınıfları) ---
    if voc_index is None: voc_score = 0.5
    elif voc_index <= 50:
        voc_score = 1.0
    elif voc_index <= 100:
        voc_score = 1.0 - ((voc_index - 50) * 0.004)
    elif voc_index <= 200:
        voc_score = 0.8 - ((voc_index - 100) * 0.004)
    else:
        voc_score = max(0.0, 0.4 - ((voc_index - 200) * 0.002))

    return (0.75 * co2_score) + (0.25 * voc_score)

def calc_comfort_score(temp_c, rh, co2, voc_index) -> float:
    """
    Genel Skor (ASHRAE 55 + WELL + Veto Mantığı)
    """
    t_score = calculate_thermal_score(temp_c, rh)
    air_score = calculate_iaq_score(co2, voc_index)

    # Temel ağırlıklı ortalama
    base_score = (0.6 * t_score) + (0.4 * air_score)

    # Değerler kritik sınırları aşarsa ortalamaya bakmaksızın puanı kırarız.
    
    # 1. CO2 Limiti: 1200 ppm üstü 'Havasız' kabul edilir.
    if co2 is not None and co2 > 1200:
        base_score = min(base_score, 0.45)

    # 2. Termal Limit: 17 altı veya 29 üstü ofis için çalışılamaz durumdur.
    if temp_c is not None and (temp_c < 17.0 or temp_c > 29.0):
        base_score = min(base_score, 0.50)
        
    # 3. VOC Limiti: 250 üstü ağır koku/gaz var demektir.
    if voc_index is not None and voc_index > 250:
        base_score = min(base_score, 0.40)

    return round(max(0.0, min(1.0, base_score)), 2)