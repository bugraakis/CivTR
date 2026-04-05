# Civilization VI Discord Botu

Ses kanalındaki oyuncular için FFA ve Takımlı oyun kurulumu yapan Discord botu.

## Kurulum

```bash
pip install -r requirements.txt
cp .env.example .env
# .env dosyasını açıp DISCORD_TOKEN değerini doldur
python bot.py
```

## Discord Developer Portal Ayarları

Bot tokenini almak için:
1. https://discord.com/developers/applications → New Application
2. Bot sekmesi → Token kopyala → `.env` dosyasına yapıştır
3. Bot sayfasında şu **Privileged Gateway Intents**'leri aç:
   - **Server Members Intent**

## Komutlar

| Komut | Açıklama |
|-------|----------|
| `/ffa` | Harita oylaması → Civ ban → Lider havuzu dağıtımı |
| `/takim` | Kaç takım istediğini sorar, oyuncuları rastgele dağıtır |
| `/yardim` | Komut listesini gösterir |

## Nasıl Çalışır?

### FFA
- `/ffa` komutunu yaz
- **Harita seçimi**: 7 harita butonu çıkar, herkes oy verir, embed canlı güncellenir
- **Civ ban**: Tek mesajda herkes banlamak istediği medeniyetin emojisini koyar, Onayla'ya basar
- **Havuz dağıtımı**: Kalan liderler oyunculara eşit paylaştırılır

### Takımlı
- `/takim` komutunu yaz
- Bot kaç takım istediğini sorar (2–6 takım, oyuncu sayısına göre)
- Lider ban aşamasından sonra oyuncular takımlara rastgele dağıtılır

## Medeniyet Emojileri (FFA Ban için)

`civ_emojis.py` dosyasında her medeniyete Discord emoji karşılığını ekle:

```python
"America": "<:civ_america:123456789>",
```
