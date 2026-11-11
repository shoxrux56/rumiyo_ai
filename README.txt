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
