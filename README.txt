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

--- Ustoz tanlash (2 xil xarakter) ---
Salomlashuv ekranida o'quvchi ustozni tanlaydi (tanlov brauzerda eslab qolinadi):
  * Do'stona ustoz ("jonim" deb gaplashadi, iliq, hazilkash)  -> vrm/ustoz-jonim.vrm, ovoz: LIVE_VOICE_FRIENDLY (default Puck)
  * Jahldor ustoz (jahli tez, kulgili)                          -> vrm/ustoz-jahldor.vrm, ovoz: LIVE_VOICE (default Charon)
Mijoz tanlovni /ai/start (body.persona), /ai/live?p=... va /ai/turn (X-Persona) orqali yuboradi; server.py ichida
persona_of / set_persona / build_friendly. Ustoz almashsa suhbat tarixi tozalanadi.

--- 3D avatar (VRM) ---
Ikkala model ham CC0 (tijorat uchun ruxsat), manba: github.com/madjin/vrm-samples
  ustoz-jonim.vrm    = Sakurada_Fumiriya,  ustoz-jahldor.vrm = HairSample_Male (teksturalari kichraytirilgan).
vrm/rumiyo-vrm.js: three.js + @pixiv/three-vrm (MIT) dan yig'ilgan bundle (esbuild).
Boshqa VRM qo'yish: vrm/ustoz-*.vrm fayllarini almashtiring (aa/angry/happy/sad/relaxed/blink ifodalari bo'lsa yetarli).
WebGL yo'q yoki model yuklanmasa, avtomatik 2D SVG avatarga o'tadi.
