RUMIYO SAYTI — alohida Render servis

1) GitHub'da yangi repo oching (masalan: rumiyo) va ichiga yuklang:
   server.py, gaplar_db.py, index.html, requirements.txt
   va "media" papka ichida 5 ta video: idle.mp4, speak.mp4, angry.mp4, angry_start.mp4, cry.mp4
   (ixtiyoriy: laugh.mp4)

2) Render -> New -> Web Service -> shu repo:
   Build Command:  pip install -r requirements.txt
   Start Command:  python server.py

3) Render -> Environment:
   TALK_SECRET  = botdagi TALK_SECRET bilan bir xil
   BOT_URL      = https://t.me/rumiyo_bot
   (Groq/OpenRouter/Gemini kalitlarini botdan qo'shasiz: /admin -> Kalitlar)

4) Botning Environment'iga qo'shing va qayta yoqing:
   RUMIYO_URL = https://<rumiyo-servis>.onrender.com

Suhbat (odam bilan) hozircha o'chiq: bot "Suhbat" tugmasi Rumiyoni ochadi.
Odam bilan suhbatni qaytarish uchun botga SUHBAT_ON=1 qo'shing.

--- Tugma bilan gapirish (jonli rejim) ---
LIVE_PTT=1 (standart): mikrofon faqat tugma bosilganda ochiladi. Tez bosish = yoqish/o'chirish, bosib turish = qo'yib yuborganda yuboriladi.
LIVE_PTT=0: eski doimiy eshitish rejimi.
Kunlik limit faqat gapirilgan vaqtni sanaydi (odam + Rumiyo ovozi); jimlik hisobga kirmaydi.
LIVE_IDLE=150: shuncha soniya jim bo'lsa qo'ng'iroq yopiladi. Limit: DAILY_MIN (standart 5 daqiqa).

--- Avatar ---
Avatar endi videolar emas, 2D SVG ustoz (index.html ichida): ko'z pirpiraydi, ko'zlar barmoq/sichqonchani kuzatadi,
og'iz Rumiyo ovozi balandligiga qarab ochiladi, holatlar: idle, speak, angry (qizarish, bug', jahl belgisi), laugh, cry, happy.
Jonli rejimda hissiyat Rumiyo aytgan matndan aniqlanadi (moodFrom funksiyasi). Avatarni bossangiz jahli chiqadi.
media/*.mp4 endi ishlatilmaydi (o'chirsa bo'ladi).
