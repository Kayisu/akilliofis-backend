def calculate_thermal_score(temp_c: float, rh: float) -> float:
    """
    ASHRAE Standard 55 (Ofis/Sedanter Çalışma) Bazlı Puanlama.
    Mükemmel Aralık: 21°C - 24°C
    Kabul Edilebilir: 20°C - 26°C
    """
    if temp_c is None or rh is None: return 0.0

    # --- 1. Sıcaklık Puanı (Parçalı Fonksiyon) ---
    # Profesyonel yaklaşım: "Hedef" tek bir nokta değil, bir aralıktır (Band).
    if 21.0 <= temp_c <= 24.0:
        t_score = 1.0  # Altın Bölge
    elif 20.0 <= temp_c < 21.0:
        # 20'den 21'e çıkarken puan 0.8'den 1.0'a çıkar
        t_score = 0.8 + ((temp_c - 20.0) * 0.2)
    elif 24.0 < temp_c <= 26.0:
        # 24'ten 26'ya çıkarken puan 1.0'dan 0.7'ye düşer
        t_score = 1.0 - ((temp_c - 24.0) * 0.15)
    elif temp_c < 18.0 or temp_c > 30.0:
        t_score = 0.0  # Çok soğuk veya çok sıcak
    else:
        # Kalan ara bölgeler (18-20 arası ve 26-30 arası)
        if temp_c < 20.0:
            t_score = 0.5 + ((temp_c - 18.0) * 0.15)
        else:
            t_score = 0.7 - ((temp_c - 26.0) * 0.175)

    # --- 2. Nem Cezası (ASHRAE) ---
    # İdeal: %30 - %60
    rh_penalty = 0.0
    if rh < 30:
        # %20 nemde %10 ceza, %0 nemde %20 ceza
        rh_penalty = (30 - rh) * 0.005 
    elif rh > 60:
        # %80 nemde %20 ceza
        rh_penalty = (rh - 60) * 0.01
    
    # Sıcaklık puanından nem cezasını düş (Minimum 0)
    final_score = max(0.0, t_score - rh_penalty)
    return final_score

def calculate_iaq_score(co2: float, voc_index: float) -> float:
    """
    WELL Building Standard & UBA (Alman Çevre Ajansı) Bazlı.
    """
    # --- 1. CO2 Puanı (WELL Standardı) ---
    # < 800 ppm: Mükemmel
    # 800-1000: İyi
    # 1000-1500: İdare Eder
    # > 1500: Kötü
    
    if co2 is None: co2_score = 0.0
    elif co2 <= 800:
        co2_score = 1.0
    elif 800 < co2 <= 1000:
        # 800->1.0, 1000->0.80 (Lineer düşüş)
        co2_score = 1.0 - ((co2 - 800) * 0.001) 
    elif 1000 < co2 <= 1500:
        # 1000->0.80, 1500->0.50 (Daha sert düşüş)
        co2_score = 0.80 - ((co2 - 1000) * 0.0006)
    else: # > 1500
        # 1500->0.50, 2500->0.0
        co2_score = max(0.0, 0.50 - ((co2 - 1500) * 0.0005))

    # --- 2. VOC Puanı (BME680 Index -> UBA Sınıfları) ---
    # 0-50: Seviye 1 (Mükemmel)
    # 51-100: Seviye 2 (İyi)
    # 101-150: Seviye 3 (Orta)
    # 151-200: Seviye 4 (Kötü)
    # 201+: Seviye 5 (Çok Kötü)
    
    if voc_index is None: voc_score = 0.5
    elif voc_index <= 50:
        voc_score = 1.0
    elif voc_index <= 100:
        # 50->1.0, 100->0.8
        voc_score = 1.0 - ((voc_index - 50) * 0.004)
    elif voc_index <= 200:
        # 100->0.8, 200->0.4
        voc_score = 0.8 - ((voc_index - 100) * 0.004)
    else:
        # 200->0.4, 400->0.0
        voc_score = max(0.0, 0.4 - ((voc_index - 200) * 0.002))

    # WELL Standardında en zayıf halka önemlidir ama biz yine de 
    # CO2'ye ağırlık verelim çünkü sensörü (SCD41) daha güvenilirdir.
    return (0.75 * co2_score) + (0.25 * voc_score)

def calc_comfort_score(temp_c, rh, co2, voc_index) -> float:
    """
    Genel Skor (ASHRAE 55 + WELL)
    """
    t_score = calculate_thermal_score(temp_c, rh)
    air_score = calculate_iaq_score(co2, voc_index)

    # İnsanlar termal konforu (sıcak/soğuk) hava kalitesinden daha çabuk hisseder.
    # %60 Termal, %40 Hava Kalitesi
    base_score = (0.6 * t_score) + (0.4 * air_score)

    return round(max(0.0, min(1.0, base_score)), 2)