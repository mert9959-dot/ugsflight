"""
✈️ Ağrı → İstanbul Uçuş Fiyat Takip Botu
Telegram üzerinden çalışır | 30 dakikada bir kontrol eder
"""

import asyncio
import os
import random
import logging
from datetime import datetime, date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes
)

# ─── AYARLAR ──────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "BURAYA_TOKEN_YAZIN")

# Takip edilecek uçuş bilgileri
KALKIS       = "AJI"          # Ağrı havalimanı kodu
VARIS        = "IST"          # İstanbul (SAW da dahil edilir)
UCUS_TARIHI  = "2025-05-22"   # 22 Mayıs Cuma
YOLCU_SAYISI = 1

# Kontrol aralığı (saniye) – 1800 = 30 dakika
CHECK_INTERVAL = 1800

# Hedef fiyat: bu fiyatın altına düşünce bildir (TL)
# None yaparsanız sadece fiyat düşünce bildirim gönderir
HEDEF_FIYAT = 1500

# ─── GERÇEK ENTEGRASYON (opsiyonel) ───────────────────────────────────────────
#
# Gerçek fiyat almak için aşağıdaki servislerden birini kullanabilirsiniz:
#
# 1. Aviasales / Travelpayouts API (ücretsiz kayıt):
#    https://www.travelpayouts.com/developers/api
#    → Kayıt olun → API token alın → get_real_price() fonksiyonunu aktif edin
#
# 2. Google Flights (SerpAPI aracılığıyla):
#    https://serpapi.com  (ücretsiz 100 arama/ay)
#
# ─── SİMÜLASYON MODU (demo) ───────────────────────────────────────────────────
#
# Gerçek API olmadan test edebilmek için simüle edilmiş fiyatlar kullanılır.
# Gerçek entegrasyona geçince sadece get_current_price() içini değiştirin.

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)

# ─── FİYAT FONKSİYONLARI ─────────────────────────────────────────────────────

HAVAYOLLARI = {
    "THY":       {"baz": 1650, "emoji": "🔴"},
    "Pegasus":   {"baz": 1420, "emoji": "🟡"},
    "SunExpress":{"baz": 1380, "emoji": "🟠"},
}

def get_simulated_price():
    """Demo: Gerçekçi fiyat dalgalanması simüle eder."""
    sonuclar = {}
    for havayolu, bilgi in HAVAYOLLARI.items():
        baz   = bilgi["baz"] * YOLCU_SAYISI
        gurultu = random.uniform(-0.12, 0.10)
        fiyat = int(baz * (1 + gurultu))
        sonuclar[havayolu] = fiyat
    en_ucuz_airline = min(sonuclar, key=sonuclar.get)
    return {
        "fiyat":    sonuclar[en_ucuz_airline],
        "havayolu": en_ucuz_airline,
        "tumFiyatlar": sonuclar,
        "kaynak":   "demo"
    }

# ── Gerçek API kullanmak istiyorsanız bu fonksiyonu düzenleyin ────────────────
# def get_real_price():
#     import requests
#     url = "https://api.travelpayouts.com/v1/prices/cheap"
#     params = {
#         "origin": KALKIS,
#         "destination": VARIS,
#         "depart_date": UCUS_TARIHI,
#         "token": os.environ["AVIASALES_TOKEN"],
#         "currency": "try"
#     }
#     r = requests.get(url, params=params, timeout=10)
#     data = r.json().get("data", {})
#     # Veriyi parse edin ve fiyatı döndürün
#     ...

def get_current_price():
    """Mevcut fiyatı getirir. Gerçek API'ye geçmek için burası değiştirilir."""
    return get_simulated_price()

# ─── BOT DURUMU ──────────────────────────────────────────────────────────────

class BotDurumu:
    def __init__(self):
        self.aktif_kullanicilar: set[int] = set()
        self.son_fiyatlar: dict[int, int] = {}
        self.en_dusuk: dict[int, int] = {}
        self.kontrol_sayisi: dict[int, int] = {}

durum = BotDurumu()

# ─── YARDIMCI FONKSİYONLAR ───────────────────────────────────────────────────

def fiyat_mesaji_olustur(veri: dict, onceki_fiyat: int | None) -> tuple[str, str]:
    """Fiyat değişimine göre mesaj ve durum döndürür."""
    fiyat     = veri["fiyat"]
    havayolu  = veri["havayolu"]
    emoji_hw  = HAVAYOLLARI[havayolu]["emoji"]
    degisim   = ""
    durum_tur = "bilgi"

    if onceki_fiyat is not None:
        fark = fiyat - onceki_fiyat
        if fark < 0:
            degisim   = f"\n📉 *{abs(fark):,} TL düştü!* (önceki: {onceki_fiyat:,} TL)"
            durum_tur = "dusus"
        elif fark > 0:
            degisim   = f"\n📈 {fark:,} TL arttı (önceki: {onceki_fiyat:,} TL)"
            durum_tur = "artis"
        else:
            degisim   = "\n➡️ Fiyat değişmedi"

    hedef_uyari = ""
    if HEDEF_FIYAT and fiyat <= HEDEF_FIYAT:
        hedef_uyari = f"\n\n🔔 *HEDEF FİYATA ULAŞILDI!* ({HEDEF_FIYAT:,} TL altında)"
        durum_tur   = "hedef"

    diger_fiyatlar = "\n".join(
        f"  {HAVAYOLLARI[hw]['emoji']} {hw}: {f:,} TL"
        for hw, f in sorted(veri["tumFiyatlar"].items(), key=lambda x: x[1])
    )

    mesaj = (
        f"✈️ *Ağrı (AJI) → İstanbul*\n"
        f"📅 {UCUS_TARIHI} | {YOLCU_SAYISI} yolcu\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"{emoji_hw} *En ucuz: {havayolu}*\n"
        f"💰 *{fiyat:,} TL*"
        f"{degisim}"
        f"{hedef_uyari}\n\n"
        f"Tüm fiyatlar:\n{diger_fiyatlar}\n\n"
        f"🕐 {datetime.now().strftime('%H:%M')} | "
        f"{'📡 Demo' if veri['kaynak'] == 'demo' else '🌐 Canlı veri'}"
    )
    return mesaj, durum_tur

# ─── TELEGRAM KOMUTLARI ───────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kullanici_id = update.effective_user.id
    isim = update.effective_user.first_name

    klavye = [
        [InlineKeyboardButton("✅ Takibi Başlat", callback_data="baslat")],
        [InlineKeyboardButton("📊 Şu Anki Fiyat", callback_data="simdi")],
    ]
    markup = InlineKeyboardMarkup(klavye)

    await update.message.reply_text(
        f"Merhaba {isim}! 👋\n\n"
        f"✈️ *Uçuş Fiyat Takip Botu*\n\n"
        f"📍 Güzergah: *Ağrı → İstanbul*\n"
        f"📅 Tarih: *{UCUS_TARIHI}* (Cuma)\n"
        f"👤 Yolcu: *{YOLCU_SAYISI} kişi*\n"
        f"🎯 Hedef fiyat: *{HEDEF_FIYAT:,} TL*\n\n"
        f"Aşağıdan takibi başlatabilirsin:",
        parse_mode="Markdown",
        reply_markup=markup
    )

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kullanici_id = query.from_user.id

    if query.data == "baslat":
        if kullanici_id in durum.aktif_kullanicilar:
            await query.edit_message_text("⚠️ Takip zaten aktif! Durdurmak için /dur yaz.")
            return

        durum.aktif_kullanicilar.add(kullanici_id)
        durum.kontrol_sayisi[kullanici_id] = 0

        await query.edit_message_text(
            f"✅ *Takip başlatıldı!*\n\n"
            f"Her *30 dakikada bir* kontrol edeceğim.\n"
            f"Fiyat düşünce ve {HEDEF_FIYAT:,} TL altına inince sana yazacağım.\n\n"
            f"Durdurmak için: /dur",
            parse_mode="Markdown"
        )

        ctx.job_queue.run_repeating(
            lambda c: kontrol_et(c, kullanici_id),
            interval=CHECK_INTERVAL,
            first=5,
            name=f"takip_{kullanici_id}",
            chat_id=kullanici_id
        )
        log.info(f"Takip başlatıldı: kullanıcı {kullanici_id}")

    elif query.data == "simdi":
        await query.edit_message_text("🔍 Fiyat kontrol ediliyor...")
        veri = get_current_price()
        onceki = durum.son_fiyatlar.get(kullanici_id)
        mesaj, _ = fiyat_mesaji_olustur(veri, onceki)
        durum.son_fiyatlar[kullanici_id] = veri["fiyat"]
        await ctx.bot.send_message(kullanici_id, mesaj, parse_mode="Markdown")

async def dur(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kullanici_id = update.effective_user.id
    if kullanici_id not in durum.aktif_kullanicilar:
        await update.message.reply_text("Zaten aktif takip yok.")
        return

    durum.aktif_kullanicilar.discard(kullanici_id)
    jobs = ctx.job_queue.get_jobs_by_name(f"takip_{kullanici_id}")
    for job in jobs:
        job.schedule_removal()

    kontrol = durum.kontrol_sayisi.get(kullanici_id, 0)
    en_dusuk = durum.en_dusuk.get(kullanici_id, "—")
    en_dusuk_str = f"{en_dusuk:,} TL" if isinstance(en_dusuk, int) else en_dusuk

    await update.message.reply_text(
        f"⛔ Takip durduruldu.\n\n"
        f"📊 Özet:\n"
        f"  Toplam kontrol: {kontrol} kez\n"
        f"  En düşük fiyat: {en_dusuk_str}\n\n"
        f"Yeniden başlatmak için /start yaz.",
    )
    log.info(f"Takip durduruldu: kullanıcı {kullanici_id}")

async def durum_komut(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kullanici_id = update.effective_user.id
    aktif = kullanici_id in durum.aktif_kullanicilar
    son_fiyat = durum.son_fiyatlar.get(kullanici_id)
    en_dusuk = durum.en_dusuk.get(kullanici_id)
    kontrol = durum.kontrol_sayisi.get(kullanici_id, 0)

    await update.message.reply_text(
        f"📊 *Durum Raporu*\n\n"
        f"Takip: {'🟢 Aktif' if aktif else '🔴 Pasif'}\n"
        f"Son fiyat: {f'{son_fiyat:,} TL' if son_fiyat else '—'}\n"
        f"En düşük: {f'{en_dusuk:,} TL' if en_dusuk else '—'}\n"
        f"Kontrol sayısı: {kontrol}\n"
        f"Hedef fiyat: {HEDEF_FIYAT:,} TL\n"
        f"Kontrol aralığı: {CHECK_INTERVAL // 60} dakika",
        parse_mode="Markdown"
    )

# ─── OTOMATİK KONTROL FONKSİYONU ─────────────────────────────────────────────

async def kontrol_et(ctx: ContextTypes.DEFAULT_TYPE, kullanici_id: int):
    if kullanici_id not in durum.aktif_kullanicilar:
        return

    try:
        veri      = get_current_price()
        fiyat     = veri["fiyat"]
        onceki    = durum.son_fiyatlar.get(kullanici_id)
        mesaj, tur = fiyat_mesaji_olustur(veri, onceki)

        durum.son_fiyatlar[kullanici_id]  = fiyat
        durum.kontrol_sayisi[kullanici_id] = durum.kontrol_sayisi.get(kullanici_id, 0) + 1

        if durum.en_dusuk.get(kullanici_id) is None or fiyat < durum.en_dusuk[kullanici_id]:
            durum.en_dusuk[kullanici_id] = fiyat

        # Sadece anlamlı değişimlerde ya da hedef fiyata ulaşınca bildir
        if tur in ("dusus", "hedef") or onceki is None:
            await ctx.bot.send_message(kullanici_id, mesaj, parse_mode="Markdown")
            log.info(f"Bildirim gönderildi → kullanıcı {kullanici_id} | {fiyat:,} TL | {tur}")
        else:
            log.info(f"Kontrol tamam → {fiyat:,} TL ({tur}) | bildirim gönderilmedi")

    except Exception as e:
        log.error(f"Kontrol hatası: {e}")

# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    if TELEGRAM_TOKEN == "BURAYA_TOKEN_YAZIN":
        print("❌ HATA: TELEGRAM_TOKEN ayarlanmamış!")
        print("   Railway'de Variables bölümüne TELEGRAM_TOKEN ekleyin.")
        return

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start",  start))
    app.add_handler(CommandHandler("dur",    dur))
    app.add_handler(CommandHandler("durum",  durum_komut))
    app.add_handler(CallbackQueryHandler(button_handler))

    log.info("✈️ Uçuş fiyat takip botu başlatıldı!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
