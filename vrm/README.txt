
--- Tezlik (performance) ---
- Modellar yengillashtirilgan (~3 MB), kontur (outline) o'chirilgan, DPR/FPS avtomatik pasayadi.
- Sekin kompyuterda avtomatik 2D (SVG) ga o'tadi; qo'lda: ⚡ tugmasi, qaytarish: 3D tugmasi (rumiyo_lite).
- Modellar navbat bilan yuklanadi (tez almashtirganda xato bermaydi).
- Kesh: ?v=5 (yangilanganda raqamni oshiring).

--- Qo'l tegmasdan suhbat (hands-free) ---
- Standart: avtomatik (LIVE_PTT=0): ustoz gapini tugatgach mikrofon o'zi ochiladi; yopiq paytda tugmada chiziq. Tugma bilan rejim: LIVE_PTT=1.
- Mikrofon tugmasi: Rumiyo gapirayotganda bosilsa - to'xtatadi; jim paytda - mikrofonni o'chiradi/yoqadi.
- Gap tugashi: LIVE_SILENCE_MS (1100), LIVE_PREFIX_MS (300). Eski tugmali rejim: LIVE_PTT=1.
- Jins (o'g'il/qiz) HECH QACHON saqlanmaydi: har safar kirganda tanlanadi. Ustoz va xarakter saqlanadi, istalgan payt o'zgartiriladi.
- 3D ishlamasa: ustozning rasmi ko'rsatiladi; "yengil rejim" endi saqlanmaydi.

--- Tayyor ovozli salomlashuvlar (kechikishni kamaytiradi) ---
- clips_phrases.json: gaplar ro'yxati (ehtirosli va shirin ustoz). tools/make_clips.py ularni Gemini TTS bilan ovozga aylantiradi (clips/).
- Sayt kirganda tayyor yozuvni darrov chaladi, ustoz esa jim turib, siz gapirishingizni kutadi (&np=1).
- Yozuv topilmasa yoki chalinmasa, ustoz avvalgidek o'zi salom beradi.
- Yangi gap qo'shish: clips_phrases.json ga yozing va skriptni qayta ishga tushiring (faqat yangilari yaratiladi).
- AVTOMATIK: skriptni ishga tushirish shart emas. Ehtirosli yoki shirin ustoz birinchi marta tanlanganda server fonda tayyor salomlashuvlarni
  Gemini TTS bilan o'zi yaratib clips/ ga saqlaydi (birinchi odam odatdagidek eshitadi, keyingilar darrov). Render bepul tarifida
  qayta deploy/uyqudan keyin papka tozalanadi va yozuvlar yana o'zi yaratiladi. TTS_MODEL env bilan model o'zgartiriladi.
