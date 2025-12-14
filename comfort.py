import math

def calculate_thermal_score(temp_c: float, rh: float) -> float:
    """
    Termal konfor (Sıcaklık + Nem).
    İdeal: 21-25.5 °C arası.
    """
    if temp_c is None or rh is None:
        return 0.0

    target_temp = 23.5 
    
    # Nem etkisi (Basitleştirilmiş)
    # Nem %30-60 arasıysa ceza yok (1.0).
    # Dışındaysa sıcaklık farkını biraz daha "kötü" hissettirir.
    humidity_penalty = 1.0
    if rh > 60:
        humidity_penalty = 1.0 + ((rh - 60) / 200.0) # Daha yumuşak eğim
    elif rh < 30:
        humidity_penalty = 1.05 

    temp_diff = abs(temp_c - target_temp) * humidity_penalty

    # Gaussian dağılımı (Çan eğrisi)
    # Sigma değerini 3.5'ten 4.0'a çıkardım. 
    # Bu, sıcaklık değişimlerine karşı skoru biraz daha toleranslı yapar.
    thermal_score = math.exp(-(temp_diff**2) / (2 * (4.0**2)))

    return thermal_score

def calculate_iaq_score(co2: float, voc_index: float) -> float:
    """
    İç Hava Kalitesi (IAQ).
    Burada VOC'un etkisini azalttık, CO2'yi ana belirleyici yaptık.
    """
    # --- 1. CO2 Skoru (Ana Faktör) ---
    if co2 is None: co2_score = 0.0
    else:
        # < 800 ppm: Mükemmel
        # 800 - 1500 ppm: Yavaş düşüş
        # > 1500 ppm: Hızlı düşüş
        if co2 <= 800:
            co2_score = 1.0
        elif co2 >= 2500: # Üst limiti artırdım, hemen 0 olmasın
            co2_score = 0.0
        else:
            # Lineer bir düşüş yerine yumuşak bir curve
            co2_score = 1.0 - ((co2 - 800) / 1700.0) ** 1.2

    # --- 2. VOC Skoru (Yardımcı Faktör) ---
    if voc_index is None: voc_score = 0.5
    else:
        # 0-100: İyi
        # 100-300: Orta
        # > 300: Kötü
        if voc_index <= 100:
            voc_score = 1.0
        elif voc_index >= 450:
            voc_score = 0.0
        else:
            voc_score = 1.0 - ((voc_index - 100) / 350.0)

    # --- BİRLEŞTİRME ---
    # ESKİ: En kötü olan belirler (Min kuralı) -> Sıçrama yapar.
    # YENİ: Ağırlıklı Ortalama.
    # Karbondioksit (Havasızlık) daha somut bir veridir, %70 etkilesin.
    # VOC (Koku/Kimyasal) biraz daha oynaktır, %30 etkilesin.
    
    final_air_score = (0.7 * co2_score) + (0.3 * voc_score)
    
    return final_air_score

def calc_comfort_score(temp_c, rh, co2, voc_index) -> float:
    """
    Genel Ofis Konfor Skoru (0.0 - 1.0)
    Sıçramaları önlemek için 'Penalty/Ceza' mantığı kaldırıldı.
    """
    t_score = calculate_thermal_score(temp_c, rh)
    air_score = calculate_iaq_score(co2, voc_index)

    # Ağırlıklar: Isıl konfor hala insan hissiyatı için en önemlisidir.
    # %60 Termal, %40 Hava Kalitesi
    base_score = (0.6 * t_score) + (0.4 * air_score)

    # Sınırlandırma (Clamping)
    return round(max(0.0, min(1.0, base_score)), 2)