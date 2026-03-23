"""
✈️ Ağrı → İstanbul Uçuş Fiyat Takip Botu
Telegram üzerinden çalışır | 3 saatte bir kontrol eder (SerpAPI - 250 sorgu/ay)
Ortak kontrol: kaç kullanıcı olursa olsun tek sorgu kullanılır.
"""

import os
import requests
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

TR_ZONE = ZoneInfo("Europe/Istanbul")
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes
)

# ─── AYARLAR ──────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "BURAYA_TOKEN_YAZIN")
SERPAPI_KEY    = os.environ.get("SERPAPI_KEY", "")

KALKIS       = "AJI"
VARIS        = "IST,SAW"
UCUS_TARIHI  = "2026-05-22"
YOLCU_SAYISI = 1

# 3 saat = 10800 saniye → günde 8 sorgu → ayda ~240 sorgu (250 limitin altında)
# Kaç kullanıcı olursa olsun tek ortak sorgu yapılır.
CHECK_INTERVAL = 10800

HEDEF_FIYAT = 3200

# ─── LOGGING ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)

# ─── FİYAT FONKSİYONLARI ─────────────────────────────────────────────────────

def get_serpapi_price():
    try:
        params = {
            "engine":        "google_flights",
            "departure_id":  "AJI",
            "arrival_id":    "IST,SAW",
            "outbound_date": UCUS_TARIHI,
            "adults":        str(YOLCU_SAYISI),
            "type":          "2",
            "currency":      "TRY",
            "hl":            "tr",
            "api_key":       SERPAPI_KEY,
        }
        r = requests.get("https://serpapi.com/search", params=params, timeout=15)
        r.raise_for_status()
        data = r.json()

        ucuslar = data.get("best_flights", []) or data.get("other_flights", [])
        if not ucuslar:
            log.warning("SerpAPI'den uçuş verisi gelmedi, simülasyona geçiliyor.")
            return get_simulated_price()

        fiyat_listesi = []
        for ucus in ucuslar:
            fiyat = ucus.get("price")
            if fiyat:
                flights  = ucus.get("flights", [])
                havayolu = flights[0].get("airline", "Bilinmiyor") if flights else "Bilinmiyor"
                fiyat_listesi.append({"havayolu": havayolu, "fiyat": int(fiyat)})

        if not fiyat_listesi:
            return get_simulated_price()

        fiyat_listesi.sort(key=lambda x: x["fiyat"])
        en_ucuz = fiyat_listesi[0]

        tum_fiyatlar = {}
        for f in fiyat_listesi[:5]:
            hw = f["havayolu"]
            if hw not in tum_fiyatlar:
                tum_fiyatlar[hw] = f["fiyat"]

        return {
            "fiyat":       en_ucuz["fiyat"],
            "havayolu":    en_ucuz["havayolu"],
            "tumFiyatlar": tum_fiyatlar,
            "kaynak":      "gercek"
        }

    except Exception as e:
        log.error(f"SerpAPI hatası: {e}, simülasyona geçiliyor.")
        return get_simulated_price()


def get_simulated_price():
    import random
    havayollari = {"THY": 3800, "Pegasus": 3400, "SunExpress": 3300}
    sonuclar = {
        hw: int(baz * (1 + random.uniform(-0.10, 0.10))) * YOLCU_SAYISI
        for hw, baz in havayollari.items()
    }
    en_ucuz = min(sonuclar, key=sonuclar.get)
    return {
        "fiyat":       sonuclar[en_ucuz],
        "havayolu":    en_ucuz,
        "tumFiyatlar": sonuclar,
        "kaynak":      "demo"
    }


def get_current_price():
    if SERPAPI_KEY:
        return get_serpapi_price()
    return get_simulated_price()

# ─── BOT DURUMU ───────────────────────────────────────────────────────────────

class BotDurumu:
    def __init__(self):
        self.aktif_kullanicilar: set[int]       = set()
        self.son_fiyatlar:       dict[int, int] = {}
        self.en_dusuk:           dict[int, int] = {}
        self.kontrol_sayisi:     dict[int, int] = {}
        # Ortak zamanlayıcı — tek sorgu, herkese gönderilir
        self.ortak_job_basladi:  bool           = False
        self.son_veri:           dict | None    = None

durum = BotDurumu()

# ─── MESAJ OLUŞTURMA ──────────────────────────────────────────────────────────

IMZA = "\n\nEmine'yi çok seviyorum ❤️"

def fiyat_mesaji_olustur(veri: dict, onceki_fiyat: int | None) -> tuple[str, str]:
    fiyat     = veri["fiyat"]
    havayolu  = veri["havayolu"]
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
        hedef_uyari = f"\n\n🔔 *HEDEF FİYATA ULAŞILDI!* ({HEDEF_FIYAT:,} TL ve altı)"
        durum_tur   = "hedef"

    tum_fiyatlar_str = "\n".join(
        f"  ✈️ {hw}: {f:,} TL"
        for hw, f in sorted(veri["tumFiyatlar"].items(), key=lambda x: x[1])
    )
    kaynak_str = "🌐 Google Flights" if veri["kaynak"] == "gercek" else "📡 Demo"

    mesaj = (
        f"✈️ *Ağrı (AJI) → İstanbul*\n"
        f"📅 {UCUS_TARIHI} | {YOLCU_SAYISI} yolcu\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🏆 *En ucuz: {havayolu}*\n"
        f"💰 *{fiyat:,} TL*"
        f"{degisim}"
        f"{hedef_uyari}\n\n"
        f"Tüm fiyatlar:\n{tum_fiyatlar_str}\n\n"
        f"🕐 {datetime.now(TR_ZONE).strftime('%H:%M')} | {kaynak_str}"
        f"{IMZA}"
    )
    return mesaj, durum_tur

# ─── TELEGRAM KOMUTLARI ───────────────────────────────────────────────────────

KOMUTLAR_METNI = (
    "\n\n📋 *Komutlar:*\n"
    "/start — Ana menüyü aç\n"
    "/dur — Takibi durdur\n"
    "/durum — Özet rapor gör\n\n"
    "Menüdeki butonlar:\n"
    "✅ *Takibi Başlat* — Her 3 saatte otomatik kontrol\n"
    "📊 *Şu Anki Fiyat* — Hemen şimdi kontrol et"
)

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    isim = update.effective_user.first_name
    klavye = [
        [InlineKeyboardButton("✅ Takibi Başlat", callback_data="baslat")],
        [InlineKeyboardButton("📊 Şu Anki Fiyat", callback_data="simdi")],
    ]
    await update.message.reply_text(
        f"Merhaba {isim}! 👋\n\n"
        f"✈️ *Uçuş Fiyat Takip Botu*\n\n"
        f"📍 Güzergah: *Ağrı → İstanbul*\n"
        f"📅 Tarih: *{UCUS_TARIHI}* (Cuma)\n"
        f"👤 Yolcu: *{YOLCU_SAYISI} kişi*\n"
        f"🎯 Hedef fiyat: *{HEDEF_FIYAT:,} TL ve altı*\n"
        f"⏱ Kontrol sıklığı: *Her 3 saatte bir*"
        f"{KOMUTLAR_METNI}"
        f"{IMZA}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(klavye)
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

        # Ortak zamanlayıcıyı sadece bir kez başlat
        if not durum.ortak_job_basladi:
            ctx.job_queue.run_repeating(
                ortak_kontrol,
                interval=CHECK_INTERVAL,
                first=5,
                name="ortak_takip",
            )
            durum.ortak_job_basladi = True
            log.info("Ortak zamanlayıcı başlatıldı.")

        await query.edit_message_text(
            f"✅ *Takip başlatıldı!*\n\n"
            f"Her *3 saatte bir* Google Flights'tan kontrol edeceğim.\n"
            f"Fiyat düşünce ve {HEDEF_FIYAT:,} TL altına inince sana yazacağım.\n\n"
            f"Durdurmak için: /dur"
            f"{IMZA}",
            parse_mode="Markdown"
        )
        log.info(f"Kullanıcı takibe eklendi: {kullanici_id}")

    elif query.data == "simdi":
        await query.edit_message_text("🔍 Google Flights kontrol ediliyor...")
        veri   = get_current_price()
        onceki = durum.son_fiyatlar.get(kullanici_id)
        mesaj, _ = fiyat_mesaji_olustur(veri, onceki)
        durum.son_fiyatlar[kullanici_id] = veri["fiyat"]
        await ctx.bot.send_message(kullanici_id, mesaj, parse_mode="Markdown")

async def dur(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kullanici_id = update.effective_user.id
    if kullanici_id not in durum.aktif_kullanicilar:
        await update.message.reply_text(
            "Zaten aktif takip yok.\n\n"
            "Başlatmak için /start yaz."
            f"{IMZA}"
        )
        return

    durum.aktif_kullanicilar.discard(kullanici_id)

    # Son kullanıcı da çıktıysa ortak zamanlayıcıyı durdur
    if not durum.aktif_kullanicilar:
        for job in ctx.job_queue.get_jobs_by_name("ortak_takip"):
            job.schedule_removal()
        durum.ortak_job_basladi = False
        log.info("Tüm kullanıcılar çıktı, ortak zamanlayıcı durduruldu.")

    kontrol      = durum.kontrol_sayisi.get(kullanici_id, 0)
    en_dusuk     = durum.en_dusuk.get(kullanici_id, "—")
    en_dusuk_str = f"{en_dusuk:,} TL" if isinstance(en_dusuk, int) else en_dusuk

    await update.message.reply_text(
        f"⛔ Takip durduruldu.\n\n"
        f"📊 Özet:\n"
        f"  Toplam kontrol: {kontrol} kez\n"
        f"  En düşük fiyat: {en_dusuk_str}\n\n"
        f"Yeniden başlatmak için /start yaz."
        f"{IMZA}"
    )

async def durum_komut(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kullanici_id = update.effective_user.id
    aktif     = kullanici_id in durum.aktif_kullanicilar
    son_fiyat = durum.son_fiyatlar.get(kullanici_id)
    en_dusuk  = durum.en_dusuk.get(kullanici_id)
    kontrol   = durum.kontrol_sayisi.get(kullanici_id, 0)

    await update.message.reply_text(
        f"📊 *Durum Raporu*\n\n"
        f"Takip: {'🟢 Aktif' if aktif else '🔴 Pasif'}\n"
        f"Son fiyat: {f'{son_fiyat:,} TL' if son_fiyat else '—'}\n"
        f"En düşük: {f'{en_dusuk:,} TL' if en_dusuk else '—'}\n"
        f"Kontrol sayısı: {kontrol}\n"
        f"Hedef fiyat: {HEDEF_FIYAT:,} TL\n"
        f"Kontrol aralığı: Her 3 saat\n"
        f"Aktif kullanıcı: {len(durum.aktif_kullanicilar)} kişi"
        f"{IMZA}",
        parse_mode="Markdown"
    )

# ─── ORTAK OTOMATİK KONTROL ───────────────────────────────────────────────────

async def ortak_kontrol(ctx: ContextTypes.DEFAULT_TYPE):
    """Tek sorgu yapar, tüm aktif kullanıcılara gönderir."""
    if not durum.aktif_kullanicilar:
        return

    try:
        veri  = get_current_price()
        fiyat = veri["fiyat"]
        log.info(f"Ortak kontrol yapıldı → {fiyat:,} TL | {len(durum.aktif_kullanicilar)} kullanıcı")

        for kullanici_id in list(durum.aktif_kullanicilar):
            onceki     = durum.son_fiyatlar.get(kullanici_id)
            mesaj, tur = fiyat_mesaji_olustur(veri, onceki)

            durum.son_fiyatlar[kullanici_id]   = fiyat
            durum.kontrol_sayisi[kullanici_id] = durum.kontrol_sayisi.get(kullanici_id, 0) + 1

            if durum.en_dusuk.get(kullanici_id) is None or fiyat < durum.en_dusuk[kullanici_id]:
                durum.en_dusuk[kullanici_id] = fiyat

            if True:  # Her kontrolde bildirim gönder
                await ctx.bot.send_message(kullanici_id, mesaj, parse_mode="Markdown")
                log.info(f"  → Bildirim: {kullanici_id} | {tur}")

    except Exception as e:
        log.error(f"Ortak kontrol hatası: {e}")

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    if TELEGRAM_TOKEN == "BURAYA_TOKEN_YAZIN":
        print("❌ HATA: TELEGRAM_TOKEN ayarlanmamış!")
        return
    if not SERPAPI_KEY:
        print("⚠️ UYARI: SERPAPI_KEY ayarlanmamış, demo modda çalışıyor.")

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",  start))
    app.add_handler(CommandHandler("dur",    dur))
    app.add_handler(CommandHandler("durum",  durum_komut))
    app.add_handler(CallbackQueryHandler(button_handler))

    log.info("✈️ Uçuş fiyat takip botu başlatıldı! (ortak kontrol, 3 saatte bir)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
