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

--- Ustoz tanlash ---
Salomlashuv ekrani 3 bosqich: 1) o'quvchi kimligi (o'g'il bola / qiz), 2) ustoz ko'rinishi (1 erkak, 2 ayol avatar),
3) xarakter: Do'stona ("jonim"), Jahldor, Ehtirosli (olovli, "Ahh!" deb kuladi), Shirin so'z. Xarakterlar har qanday avatar bilan ishlaydi.
Tanlov BIR MARTA qilinadi va serverda (sqlite `upick`) 7 kunga qulflanadi (PERSONA_LOCK_DAYS env, default 7; 0 = qulfsiz).
Brauzer almashsa ham o'sha ustoz chiqadi (GET /ai/me). 7 kundan keyin "Ustozni o'zgartirish" tugmasi paydo bo'ladi.
O'quvchi jinsi `ugender` jadvalida: murojaat (jonim/singlim...) va ruscha shakllar (сказал/сказала) shunga qarab.
Ovozlar: LIVE_VOICE_<M|F>_<FRIENDLY|ANGRY|FIERY|SWEET> env bilan almashtiriladi (default: M: Puck/Charon/Fenrir/Achird, F: Zephyr/Kore/Autonoe/Sulafat).
Mijoz tanlovni "avatar.xarakter" (masalan f1.fiery) ko'rinishida /ai/start, /ai/live?p=..&g=.. va /ai/turn (X-Persona, X-Gender) orqali yuboradi.

--- 3D avatar (VRM) ---
Suhbat ekrani anime/visual novel uslubida: ustoz BUTUN EKRANni egallaydi (bel ostigacha), gapirganda tepada dialog oynasi (ustoz nomi bilan), pastda mikrofon; qo'l ishoralari va tana tebranishi bor. Tepadagi tugmalar: 🎬/🪟 to'liq ekran <-> kichik ko'rinish (rumiyo_view2), 📜 suhbat tarixi. Salomlashuvda yuz ko'rinishi.
Hamma model CC0 (tijorat uchun ruxsat), manba: github.com/madjin/vrm-samples (VRoid beta namunalari), teksturalari kichraytirilgan:
  ustoz-m1 = Sakurada_Fumiriya, ustoz-f1 = Darkness_Shibu, ustoz-f2 = HairSample_Female. th-*.jpg: tanlash kartochkalari uchun rasmlar.
vrm/rumiyo-vrm.js: three.js + @pixiv/three-vrm (MIT) dan yig'ilgan bundle (esbuild).
Boshqa VRM qo'yish: vrm/ustoz-*.vrm fayllarini almashtiring (aa/angry/happy/sad/relaxed/blink ifodalari bo'lsa yetarli).
WebGL yo'q yoki model yuklanmasa, avtomatik 2D SVG avatarga o'tadi.
