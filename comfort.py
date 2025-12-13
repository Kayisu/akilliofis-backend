import math

def calculate_thermal_score(temp_c: float, rh: float) -> float:
    """
    ASHRAE 55 Standartlarına benzer bir yaklaşımla termal konforu hesaplar.
    İdeal ofis sıcaklığı: 21-25.5 derece arası.
    İdeal nem: %30 - %60 arası.
    """
    if temp_c is None or rh is None:
        return 0.0

    # 1. Hissedilen Sıcaklık Etkisi (Basitleştirilmiş)
    # Nem yüksekse sıcaklık olduğundan daha sapmalı hissedilir
    deviation = 0.0
    
    # İdeal sıcaklık noktası (mevsime göre değişir ama ofis için ortalama 23.5)
    target_temp = 23.5 
    
    # Eğer nem çok yüksekse (>%60), sıcaklık sapması daha rahatsız edici olur
    humidity_penalty_factor = 1.0
    if rh > 60:
        humidity_penalty_factor = 1.0 + ((rh - 60) / 100.0) # %80 nemde 1.2 kat daha kötü hissettirir
    elif rh < 30:
        humidity_penalty_factor = 1.1 # Çok kuru hava da rahatsız edicidir

    # Sıcaklık farkı
    temp_diff = abs(temp_c - target_temp) * humidity_penalty_factor

    # Gaussian (Çan Eğrisi) Fonksiyonu:
    # Fark 0 ise skor 1.0, Fark arttıkça "S" şeklinde yumuşak düşüş.
    # sigma=3.5 demek; +/- 3.5 derece sapmada konfor %60'a düşer.
    thermal_score = math.exp(-(temp_diff**2) / (2 * (3.5**2)))

    return thermal_score

def calculate_iaq_score(co2: float, voc_index: float) -> float:
    """
    İç Hava Kalitesi (IAQ) Skoru.
    CO2 ve VOC değerlerine göre bilişsel performansı baz alır.
    """
    if co2 is None: 
        co2_score = 0.0
    else:
        # CO2 Değerlendirmesi (ASHRAE & Pettenkofer Limitleri)
        # < 600: Mükemmel (1.0)
        # 600-1000: İyi (Lineer düşüş)
        # 1000-1500: Dikkat (Hızlı düşüş - Baş ağrısı başlangıcı)
        # > 1500: Kötü
        if co2 <= 600:
            co2_score = 1.0
        elif co2 >= 2000:
            co2_score = 0.0
        else:
            # 600 ile 2000 arasında 'ters' bir curve.
            # 1000 ppm kritik eşik olduğu için oraya gelince puanı 0.7'nin altına çekmeye çalışırız.
            co2_score = 1.0 - ((co2 - 600) / 1400.0) ** 1.5  # Üstel ceza

    if voc_index is None:
        voc_score = 0.5 # Bilinmiyor
    else:
        # BME680 VOC Index (0-500 arası)
        # 0-50: İyi
        # 51-150: Orta
        # >150: Kötü
        if voc_index <= 50:
            voc_score = 1.0
        elif voc_index >= 350:
            voc_score = 0.0
        else:
            voc_score = 1.0 - ((voc_index - 50) / 300.0)

    # Hava kalitesinde "en kötü olan belirleyicidir".
    # Yani VOC temiz ama CO2 2000 ise, hava kötüdür. Ortalamasını almayız.
    # Yine de %20 yumuşatma payı bırakalım.
    return (0.8 * min(co2_score, voc_score)) + (0.2 * ((co2_score + voc_score) / 2))

def calc_comfort_score(temp_c, rh, co2, voc_index) -> float:
    """
    Genel ofis konfor skoru (0.0 - 1.0)
    """
    # 1. Alt Skorları Hesapla
    t_score = calculate_thermal_score(temp_c, rh)
    air_score = calculate_iaq_score(co2, voc_index)

    # 2. Ağırlıklı Ortalamayı Belirle
    # İnsanlar termal konforsuzluğu (sıcak/soğuk) anında hisseder (Yüksek Ağırlık)
    # Havasızlığı (CO2) daha geç fark eder ama verimi düşer (Orta Ağırlık)
    
    # Ancak: Eğer herhangi biri "Çok Kötü" seviyesindeyse (< 0.4), genel skor asla yüksek olmamalı.
    base_score = (0.6 * t_score) + (0.4 * air_score)

    # 3. Kırılma Faktörü (Penalty)
    # Eğer sıcaklık skoru veya hava skoru 0.4'ün altındaysa, genel skoru aşağı çek.
    if t_score < 0.4 or air_score < 0.4:
        base_score = base_score * 0.7  # %30 ceza uygula

    return round(max(0.0, min(1.0, base_score)), 2)


