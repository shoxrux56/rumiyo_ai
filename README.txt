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

--- Ustoz tanlash (4 xil xarakter) ---
Salomlashuv ekranida o'quvchi ustozni BIR MARTA tanlaydi (brauzerda eslab qolinadi); keyingi kirishlarda o'sha avatar chiqadi,
"Ustozni o'zgartirish" tugmasi bilan almashtiriladi.
  Erkak:  Do'stona ("jonim") -> ustoz-jonim.vrm, ovoz LIVE_VOICE_FRIENDLY (Puck);  Jahldor -> ustoz-jahldor.vrm, LIVE_VOICE (Charon)
  Ayol:   Ehtirosli -> ustoz-ehtirosli.vrm, LIVE_VOICE_FIERY (Kore);  Shirin so'z -> ustoz-shirin.vrm, LIVE_VOICE_SWEET (Aoede)
Mijoz tanlovni /ai/start (body.persona), /ai/live?p=... va /ai/turn (X-Persona) orqali yuboradi (server: PERSONA_STYLE, build_friendly).
O'quvchi jinsi: birinchi suhbatda ustoz "qizmisiz yoki o'g'il bolami?" deb so'raydi; javob (men qizman / yigitman ...) detect_gender
bilan aniqlanib, sqlite `ugender` jadvaliga yoziladi va keyingi safar qayta so'ralmaydi; shunga qarab murojaat va ruscha shakllar (сказал/сказала) tanlanadi.

--- 3D avatar (VRM) ---
Hamma model CC0 (tijorat uchun ruxsat), manba: github.com/madjin/vrm-samples (VRoid beta namunalari), teksturalari kichraytirilgan:
  ustoz-jonim = Sakurada_Fumiriya, ustoz-jahldor = HairSample_Male, ustoz-ehtirosli = Darkness_Shibu, ustoz-shirin = HairSample_Female.
vrm/rumiyo-vrm.js: three.js + @pixiv/three-vrm (MIT) dan yig'ilgan bundle (esbuild).
Boshqa VRM qo'yish: vrm/ustoz-*.vrm fayllarini almashtiring (aa/angry/happy/sad/relaxed/blink ifodalari bo'lsa yetarli).
WebGL yo'q yoki model yuklanmasa, avtomatik 2D SVG avatarga o'tadi.
