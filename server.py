"""Rumiyo — ruscha ovozli jahldor ustoz AI (alohida server, Suhbat saytidan mustaqil).

Oqim: bot darsdan keyingi testdan so'ng imzolangan havola beradi -> sayt ochiladi -> odam ruscha gapiradi
-> Groq Whisper (ovoz->matn) -> LLM (Groq -> OpenRouter -> Gemini) -> edge-tts (matn->ovoz).
Rumiyo bugungi mavzu va bugungi gaplar bo'yicha suhbatlashadi, so'raydi, tekshiradi; kuniga 5 daqiqa; oxirida botga qaytaradi.
Hazil/pichinglar bazasi (gaplar_db.py) botdagi adminning "AI ni o'qitish" tugmasidan keladi: bot -> /admin/add va /admin/sync.

Kalitlar FAQAT muhit o'zgaruvchilarida (Render -> Environment), kodga yoki GitHubga yozilmaydi:
  TALK_SECRET (botdagi bilan bir xil), GROQ_API_KEYS (vergul bilan), OPENROUTER_API_KEY, GEMINI_API_KEY
"""
import asyncio, base64, difflib, hashlib, hmac, json, os, random, re, sqlite3, time
from datetime import datetime, timedelta, timezone

import aiohttp
from aiohttp import web
from dotenv import load_dotenv

load_dotenv()
HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.getenv("DATA_DIR", HERE)
os.makedirs(DATA_DIR, exist_ok=True)
os.environ.setdefault("GAPLAR_DB", os.path.join(DATA_DIR, "gaplar.db"))
import gaplar_db as G  # noqa: E402  (GAPLAR_DB ni yuqorida o'rnatdik)

SECRET = os.getenv("TALK_SECRET", "").strip()
PORT = int(os.getenv("PORT", "8080"))
BOT_URL = os.getenv("BOT_URL", "https://t.me/rumiyo_bot")
TRENER_DATA = os.getenv("TRENER_DATA", "https://shoxrux56.github.io/trener/data.js")
TZ = timezone(timedelta(hours=5))
GROQ = os.getenv("GROQ_BASE", "https://api.groq.com/openai/v1")
OPENROUTER = os.getenv("OPENROUTER_BASE", "https://openrouter.ai/api/v1")
GEMINI = os.getenv("GEMINI_BASE", "https://generativelanguage.googleapis.com/v1beta")
STT_MODELS = ["whisper-large-v3-turbo", "whisper-large-v3"]
LLM_MODELS = [x.strip() for x in os.getenv("GROQ_MODELS", "openai/gpt-oss-120b,openai/gpt-oss-20b,llama-3.3-70b-versatile,llama-3.1-8b-instant").split(",") if x.strip()]
OR_MODEL = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
GEMINI_MODELS = list(dict.fromkeys([x.strip() for x in os.getenv("GEMINI_MODELS", GEMINI_MODEL + ",gemini-flash-lite-latest").split(",") if x.strip()]))   # biri band bo'lsa (503), keyingisi
LLM_ORDER = [x.strip() for x in os.getenv("LLM_ORDER", "groq,openrouter,gemini").split(",") if x.strip()]   # masalan: gemini,groq,openrouter
RU_VOICE = os.getenv("RU_VOICE", "ru-RU-DariyaNeural")   # boshqa: ru-RU-SvetlanaNeural, ru-RU-DmitryNeural
EN_VOICE = os.getenv("EN_VOICE", "en-US-AriaNeural")     # ingliz qismi uchun; boshqa: en-US-JennyNeural, en-GB-SoniaNeural
TRENER_DATA_EN = os.getenv("TRENER_DATA_EN", "https://shoxrux56.github.io/trening_ingilish/data.js")


def lg(info):
    """Havoladagi til: 'en' (ingliz) yoki 'ru' (eski havolalarda ham rus)."""
    return "en" if (info or {}).get("g") == "en" else "ru"


def to_en(p):
    """Ruscha ustoz ko'rsatmasini ingliz tiliga o'giradi: maqsadli til = ingliz. O'zbekcha qismlar va ko'cha ruscha so'zlari (patsan, davay...) o'zgarmaydi."""
    p = p.replace("short Russian slang words", "§SLANG§")
    p = p.replace("Russian in Cyrillic", "English in Latin letters").replace("Russian", "English")
    p = p.replace("ruscha", "inglizcha").replace("«\"Ya hochu pit\"?!", "«\"I am go home\"?!")
    return p.replace("§SLANG§", "short Russian slang words")


EN_ADDENDUM = """
ENGLISH MODE: this learner is an Uzbek speaker learning ENGLISH. The `RU:` line (the label is only a technical name) carries the short ENGLISH word or sentence to learn or repeat; the live voice says it aloud in English. You still talk in Uzbek with Tashkent street slang as described. When you give a new English phrase, or the learner mispronounced something, add a quick pronunciation hint in Uzbek letters inside the UZ line, e.g. «water» deb ayt: «uoter». In FIX explain English mistakes in Uzbek. Typical Uzbek-speaker traps: a/an/the, he/she/it + s, is/are, word order, th and w sounds. The learner's sentences listed above are English = Uzbek. Never answer with a Russian lesson."""
if not SECRET:
    raise SystemExit("TALK_SECRET yo'q (botdagi bilan bir xil bo'lsin)")

db = sqlite3.connect(os.path.join(DATA_DIR, "rumiyo.db"), check_same_thread=False)
db.execute("PRAGMA journal_mode=WAL")
db.execute("PRAGMA busy_timeout=5000")
db.execute("CREATE TABLE IF NOT EXISTS use(user_id INTEGER, day TEXT, secs INTEGER DEFAULT 0, PRIMARY KEY(user_id, day))")
db.execute("CREATE TABLE IF NOT EXISTS notes(user_id INTEGER PRIMARY KEY, text TEXT, ts INTEGER)")
db.commit()


def today():
    return datetime.now(TZ).strftime("%Y-%m-%d")


# ---------- sozlamalar (bot / sahifadan keladi) ----------
def daily_secs():
    try:
        return max(30, int(float(os.getenv("DAILY_MIN") or G.setting("dailyMin", 5)) * 60))
    except (TypeError, ValueError):
        return 300


def humor_level():
    try:
        return max(0, min(3, int(G.setting("humor", 3))))
    except (TypeError, ValueError):
        return 3


def mem_msgs():
    try:
        return max(6, min(80, int(G.setting("memoryTurns", 30)) * 2))
    except (TypeError, ValueError):
        return 60


def cta_text():
    return str(G.setting("cta", "") or "Botga qayting — darsni davom ettiring 👇")


# ---------- foydalanish vaqti ----------
def left_for(uid):
    r = db.execute("SELECT secs FROM use WHERE user_id=? AND day=?", (uid, today())).fetchone()
    return max(0, daily_secs() - (r[0] if r else 0))


def add_use(uid, secs):
    db.execute("INSERT INTO use(user_id, day, secs) VALUES(?,?,?) ON CONFLICT(user_id, day) DO UPDATE SET secs=secs+excluded.secs",
               (uid, today(), int(secs)))
    db.commit()


# ---------- imzolangan havola ----------
def verify(token):
    try:
        body, sig = (token or "").split(".", 1)
        good = hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(good, sig):
            return None
        d = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if d["e"] < time.time():
            return None
        int(d["u"])
        return d
    except Exception:
        return None


def is_admin(request):
    return hmac.compare_digest(request.headers.get("X-Secret", ""), SECRET)


# ---------- kalitlar ----------
KEYS_FILE = os.path.join(DATA_DIR, "keys.json")


def keys(kind):
    env = {"groq": ("GROQ_API_KEYS", "GROQ_API_KEY"), "openrouter": ("OPENROUTER_API_KEYS", "OPENROUTER_API_KEY"),
           "gemini": ("GEMINI_API_KEYS", "GEMINI_API_KEY")}[kind]
    ks = [k.strip() for k in (os.getenv(env[0], "") + "," + os.getenv(env[1], "")).split(",") if k.strip()]
    try:
        with open(KEYS_FILE, encoding="utf-8") as f:
            v = json.load(f).get(kind)
        ks += [v] if isinstance(v, str) else list(v or [])
    except (OSError, ValueError, AttributeError):
        pass
    return list(dict.fromkeys(k for k in ks if isinstance(k, str) and k))


LAST_ERR = {}


class Busy(Exception):
    pass


key_bad = {}   # kalit -> vaqt (vaqtincha ishlamayapti)
key_rr = {}


def rotated(kind):
    ks = keys(kind)
    if not ks:
        return []
    key_rr[kind] = (key_rr.get(kind, -1) + 1) % len(ks)
    ks = ks[key_rr[kind]:] + ks[:key_rr[kind]]
    return [k for k in ks if time.time() - key_bad.get(k, 0) > 60] or ks


async def post(http, url, headers=None, **kw):
    hd = {"User-Agent": "Mozilla/5.0 (compatible; RumiyoServer/1.0)"}
    hd.update(headers or {})
    async with http.post(url, headers=hd, timeout=aiohttp.ClientTimeout(total=25), **kw) as r:   # uzoq osilib qolmasin: keyingi kalit/modelga o'tadi
        txt = await r.text()
        if r.status != 200:   # sabab Render logida ko'rinsin (kalit yozilmaydi)
            print("HTTP", r.status, url.split("?")[0], txt[:240].replace("\n", " "))
            host = url.split("?")[0].split("//")[-1].split("/")[0]
            LAST_ERR[host.split(".")[0][:14] if "googleapis" not in host else "gemini"] = "%s %s" % (r.status, txt[:110].replace("\n", " "))
        try:
            return r.status, json.loads(txt)
        except ValueError:
            return r.status, {"_raw": txt[:200]}


# ---------- LLM zanjiri: Groq -> OpenRouter -> Gemini ----------
async def llm_groq(http, msgs, temp):
    for k in rotated("groq"):
        for m in LLM_MODELS:
            try:
                body = {"model": m, "messages": msgs, "temperature": temp, "max_tokens": 220}
                if m.startswith("openai/gpt-oss"):   # fikrlovchi model: qisqa o'ylasin, o'ylash matni chiqmasin, javobga joy qolsin
                    body.update({"reasoning_effort": "low", "include_reasoning": False, "max_tokens": 700})
                st, j = await post(http, GROQ + "/chat/completions", {"Authorization": "Bearer " + k}, json=body)
            except (aiohttp.ClientError, asyncio.TimeoutError):
                key_bad[k] = time.time()
                break
            if st == 200:
                txt = (j["choices"][0]["message"].get("content") or "").strip()
                if txt:
                    return txt
                continue
            if st in (400, 404):
                continue                      # model yo'q yoki so'rov qabul qilinmadi: keyingi modelga o'tamiz
            if st == 429:
                continue                      # shu kalitda boshqa model yoki keyingi kalit
            if st in (401, 402, 403) or st >= 500:
                key_bad[k] = time.time()
                break
    raise Busy("groq")


async def llm_openrouter(http, msgs, temp):
    for k in rotated("openrouter"):
        try:
            st, j = await post(http, OPENROUTER + "/chat/completions", {"Authorization": "Bearer " + k},
                               json={"model": OR_MODEL, "messages": msgs, "temperature": temp, "max_tokens": 220})
        except (aiohttp.ClientError, asyncio.TimeoutError):
            key_bad[k] = time.time()
            continue
        if st == 200 and j.get("choices"):
            return j["choices"][0]["message"]["content"]
        if st in (401, 402, 403):
            key_bad[k] = time.time()
    raise Busy("openrouter")


def gem_contents(msgs):
    sys_txt = "\n\n".join(m["content"] for m in msgs if m["role"] == "system")
    cont = []
    for m in msgs:
        if m["role"] == "system":
            continue
        role = "user" if m["role"] == "user" else "model"
        if cont and cont[-1]["role"] == role:
            cont[-1]["parts"][0]["text"] += "\n" + m["content"]
        else:
            cont.append({"role": role, "parts": [{"text": m["content"]}]})
    if not cont or cont[0]["role"] != "user":
        cont.insert(0, {"role": "user", "parts": [{"text": "(boshlanish)"}]})
    return sys_txt, cont


async def gem_call(http, model, key, body):
    """Gemini'da «o'ylash» tokenlari javobni kesib yubormasin: avval o'ylashsiz, qabul qilinmasa oddiy so'rov."""
    url = f"{GEMINI}/models/{model}:generateContent?key={key}"
    b2 = dict(body); gc = dict(b2.get("generationConfig", {})); gc["thinkingConfig"] = {"thinkingBudget": 0}; b2["generationConfig"] = gc
    st, j = await post(http, url, json=b2)
    if st == 400:
        st, j = await post(http, url, json=body)
    return st, j


async def llm_gemini(http, msgs, temp):
    sys_txt, cont = gem_contents(msgs)
    body = {"systemInstruction": {"parts": [{"text": sys_txt}]}, "contents": cont, "generationConfig": {"temperature": temp, "maxOutputTokens": 500}}
    for m in GEMINI_MODELS:
        for k in rotated("gemini"):
            try:
                st, j = await gem_call(http, m, k, body)
            except (aiohttp.ClientError, asyncio.TimeoutError):
                continue
            if st == 200:
                try:
                    t = "".join(p.get("text", "") for p in j["candidates"][0]["content"]["parts"]).strip()
                    if t:
                        return t
                except (KeyError, IndexError):
                    pass
                continue
            if st in (401, 403):
                key_bad[k] = time.time()
            # 503/429/404: shu model band yoki yo'q, keyingi model/kalit
    raise Busy("gemini")


async def llm(msgs, temp=0.9):
    last = None
    async with aiohttp.ClientSession() as http:
        fns = {"groq": llm_groq, "openrouter": llm_openrouter, "gemini": llm_gemini}
        for fn in [fns[x] for x in LLM_ORDER if x in fns]:
            try:
                return await fn(http, msgs, temp)
            except Busy as e:
                last = e
    raise Busy(str(last))


# ---------- STT: Groq Whisper -> Gemini ----------
async def stt_gemini(http, raw, ctype, lang="ru"):
    """Gemini audioni to'g'ridan-to'g'ri eshitadi: o'zbekcha va ruscha (aralash ham) Whisper'dan ancha aniq; xatolarni to'g'rilab yubormaydi."""
    for m in GEMINI_MODELS:
      for k in rotated("gemini"):
        try:
            st, j = await gem_call(http, m, k, {"contents": [{"parts": [
                {"text": ("ENGLISH LESSON MODE: the speaker is a beginner learning English; they speak English or Uzbek (both in Latin script) and may use a few Russian street words. " if lang == "en" else "") + "Transcribe this audio VERBATIM, exactly as the person said it. The speaker may speak Uzbek (write in Latin script), Russian (write in Cyrillic), "
                         "or a mix of both, and may make grammar mistakes or mispronounce words: write what was said and do NOT correct, translate or improve it. "
                         "Output only the transcript, nothing else. If there is no speech, output nothing."},
                {"inline_data": {"mime_type": (ctype or "audio/webm").split(";")[0], "data": base64.b64encode(raw).decode()}}]}],
                "generationConfig": {"temperature": 0, "maxOutputTokens": 300}})
        except (aiohttp.ClientError, asyncio.TimeoutError):
            continue
        if st == 200:
            try:
                t = "".join(x.get("text", "") for x in j["candidates"][0]["content"]["parts"]).strip()
                if t:
                    return t
            except (KeyError, IndexError):
                continue
    return ""


async def stt(raw, ctype, lang="ru"):
    lang_t = lang
    ext = "webm" if "webm" in ctype else "m4a" if ("mp4" in ctype or "aac" in ctype) else "ogg" if "ogg" in ctype else "wav" if "wav" in ctype else "webm"
    async with aiohttp.ClientSession() as http:
        TURKIC = {"turkish", "kazakh", "azerbaijani", "tatar", "kyrgyz", "turkmen", "bashkir", "uzbek", "tajik"}
        if keys("gemini"):
            t = await stt_gemini(http, raw, ctype, lang_t)
            if t:
                return t, False

        lang_t = lang

        async def call(k, m, lang):
            form = aiohttp.FormData()
            form.add_field("file", raw, filename="a." + ext, content_type=ctype or "audio/webm")
            form.add_field("model", m)
            form.add_field("temperature", "0")
            form.add_field("response_format", "verbose_json")
            if lang:
                form.add_field("language", lang)
            form.add_field("prompt", "Hello! I want some water, please. Salom, men suv ichmoqchiman. Qancha turadi?" if lang_t == "en"
                           else "Привет! Я хочу пить. Salom, men suv ichmoqchiman. Qancha turadi?")
            return await post(http, GROQ + "/audio/transcriptions", {"Authorization": "Bearer " + k}, data=form)

        for k in rotated("groq"):
            for m in STT_MODELS:
                try:
                    st, j = await call(k, m, None)               # o'zbekcha ham, ruscha ham: til o'zi aniqlanadi
                    if st == 200:
                        lang = (j.get("language") or "").lower()
                        text = (j.get("text") or "").strip()
                        foreign = False
                        okl = ("english", "uzbek", "russian") if lang_t == "en" else ("russian", "uzbek")
                        if lang not in okl and text:   # noto'g'ri til (masalan, islandcha): qayta, to'g'ri tilda
                            st2, j2 = await call(k, m, "uz" if lang in TURKIC else ("en" if lang_t == "en" else "ru"))
                            if st2 == 200 and (j2.get("text") or "").strip():
                                text = j2["text"].strip()
                            letters = [c for c in text if c.isalpha()]
                            cyr = sum(1 for c in letters if "\u0400" <= c <= "\u04ff")
                            lat_uz = lang in TURKIC
                            foreign = bool(letters) and not lat_uz and (cyr / len(letters) < 0.5 if lang_t != "en" else False)   # na ruscha, na o'zbekcha
                        return text, foreign
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    key_bad[k] = time.time()
                    break
                if st == 429:
                    continue
                if st in (401, 402, 403) or st >= 500:
                    key_bad[k] = time.time()
                    break
        t = await stt_gemini(http, raw, ctype, lang_t)
        if t:
            return t, False
    raise Busy("stt")


try:
    BASE_PITCH = int(os.getenv("VOICE_PITCH", "-10"))   # manfiy = qalinroq, ustozga xos; 0 = oddiy
except ValueError:
    BASE_PITCH = -10
PROSODY = {k: (v[0], "%+dHz" % (BASE_PITCH + v[1]), v[2]) for k, v in
           {"angry": ("+4%", 7, "+20%"), "cry": ("-16%", -4, "-10%"), "happy": ("-1%", 5, "+0%"), "normal": ("-3%", 1, "+0%")}.items()}


UZ_VOICE = os.getenv("UZ_VOICE", "uz-UZ-MadinaNeural")   # boshqa: uz-UZ-SardorNeural (erkak)


async def tts(text, emo="normal", voice=None):
    pr = PROSODY.get(emo, PROSODY["normal"])
    import edge_tts
    out = b""
    async for ch in edge_tts.Communicate(text, voice or RU_VOICE, rate=pr[0], pitch=pr[1], volume=pr[2]).stream():
        if ch["type"] == "audio":
            out += ch["data"]
    if not out:
        raise RuntimeError("TTS bo'sh")
    return out


async def tts_dual(uz, ru, emo="normal", lang="ru"):
    """O'zbekcha qism o'zbek ovozida, ruscha (yoki inglizcha) qism o'z ovozida; ketma-ket ulanadi."""
    parts = await asyncio.gather(*([tts_safe(uz, emo, UZ_VOICE)] if uz else []), *([tts_safe(ru, emo, EN_VOICE if lang == "en" else RU_VOICE)] if ru else []))
    return b"".join(parts)


async def tts_safe(text, emo="normal", voice=None):
    """Ovoz chiqmasa ham javob matni yo'qolmasin (Microsoft TTS vaqtincha ishlamasa)."""
    try:
        return await tts(text, emo, voice)
    except Exception as e:
        print("TTS xato:", type(e).__name__, str(e)[:120])
        return b""


# ---------- bugungi dars (trener data.js) ----------
_days = {"ru": {"ts": 0, "map": {}}, "en": {"ts": 0, "map": {}}}


async def day_info(d, lang="ru"):
    """Trenerdagi d-kun: {"t": mavzu, "i": [[id, ru|en, uz, emoji], ...]} yoki None. lang: rus yoki ingliz Trener."""
    if not d:
        return None
    dd = _days["en" if lang == "en" else "ru"]
    if time.time() - dd["ts"] > 3600 or not dd["map"]:
        try:
            async with aiohttp.ClientSession() as http:
                async with http.get(TRENER_DATA_EN if lang == "en" else TRENER_DATA, timeout=aiohttp.ClientTimeout(total=20)) as r:
                    s = await r.text()
            arr = json.loads(s[s.index("["): s.rindex("]") + 1])
            dd["map"] = {int(x["d"]): x for x in arr}
            dd["ts"] = time.time()
        except Exception as e:
            print("data.js:", type(e).__name__)
            dd["ts"] = time.time() - 3300     # 5 daqiqadan keyin qayta urinadi
    return dd["map"].get(int(d))


# ---------- sessiya (suhbat xotirasi) ----------
SES = {}
gate = asyncio.Semaphore(4)


def opener_for(persona, again, ses=None):
    ask = "" if (ses or {}).get("g") else " Birinchi navbatda o'quvchi qiz yoki o'g'il bola ekanini xushmuomala so'ra."
    if persona == "angry":
        return ("(o'quvchi chatga qaytib keldi: jahl bilan 'yana kech keldingmi' qabilida kutib ol, oldingi gapimizni eslat)" if again
                else "(o'quvchi endigina qo'ng'iroqni ochdi: jahldor ohangda salom ber, bugungi mavzuga ulab." + ask + " Keyin bugun nechta gap yodlaganini so'ra)")
    st = {"friendly": "iliq va kulgili", "fiery": "juda ehtirosli, past, nafasli pichirlab", "sweet": "shirin so'zli, yumshoq va quvnoq"}[persona]
    return (f"(o'quvchi chatga qaytib keldi: sog'inganingni aytib, {st} kutib ol, oldingi gapimizni eslat)" if again
            else f"(o'quvchi endigina qo'ng'iroqni ochdi: {st} ohangda salom ber, bugungi mavzuga ulab." + ask + " Keyin bugun nechta gap yodlaganini so'ra)")


def get_session(info):
    t = time.time()
    for k in [k for k, v in SES.items() if t - v["ts"] > 3 * 3600]:
        SES.pop(k, None)
    uid = int(info["u"])
    ses = SES.get(uid)
    if not ses or ses["day"] != today():
        ses = SES[uid] = {"hist": [], "asked": [], "quiz": None, "day": today(), "ts": t, "turns": 0}
    ses["ts"] = t
    return ses


# Ustoz = avatar (m1 erkak, f1 va f2 ayol) + xarakter (4 xil). O'quvchi bir marta tanlaydi, PICK_LOCK kundan keyin o'zgartirsa bo'ladi.
AVATARS = {"m1": "m", "f1": "f", "f2": "f"}
PERSONAS = ("friendly", "angry", "fiery", "sweet")
PICK_LOCK = int(float(os.getenv("PERSONA_LOCK_DAYS", "0")) * 86400)
_VOICES = {
    ("m", "friendly"): "Puck", ("m", "angry"): "Charon", ("m", "fiery"): "Enceladus", ("m", "sweet"): "Achird",
    ("f", "friendly"): "Zephyr", ("f", "angry"): "Kore", ("f", "fiery"): "Enceladus", ("f", "sweet"): "Sulafat",
}
PERSONA_STYLE = {
    "friendly": {
        "trait": "warm, charming, genuinely funny",
        "role": "You are the friendly teacher: affectionate, patient, encouraging, playful. You are NEVER angry, never scold, never insult.",
        "sound": "you address the learner warmly (see ADDRESSING below) and by their name once you know it, the way a loving elder sibling or a favorite ustoz would. Short, lively, spoken sentences. Real emotion: delight, mock-drama, tender teasing, proud surprise (\"Voy, jonim, buni qara-ya!\", \"Ahh, mana bu gap!\", \"Qoyil, azizim!\").",
        "boy": "jonim, ukam, do'stim, azizim", "girl": "jonim, singlim, azizam, do'stim",
        "live": "Stay the warm, witty friend.",
    },
    "fiery": {
        "trait": "passionate, intense, magnetic, deep-feeling",
        "role": "You are the passionate teacher: you speak with deep, intense feeling and total devotion to the language and to the learner's success. Everything you say is charged with emotion and intimacy, a slow burning fire, never loud shouting. You believe in the learner fiercely ('sen buni qila olasan, men ishonaman'). Your passion is for the language and the learner's success; it is never explicit, never sexual, and you NEVER insult anyone.",
        "sound": "you speak in a LOW, BREATHY, SOFT WHISPER most of the time, as if telling a precious secret very close to the learner's ear: hushed, slow, velvety, with long meaningful pauses, audible breaths and sighs, and a trembling intensity that rises to a passionate near-whisper on big words. Never shout, never speak in a normal flat voice. Passionate, charged lines (\"Ahh... mana shu gap... eshityapsanmi, qanday go'zal...\", \"Yana... bir marta... pichirlab ayt...\", \"Ahhh, yuragim hapriqib ketdi...\"). Mistakes get a breathy theatrical sigh and a fierce whispered 'again, you can do it'. Your signature: a soft, breathy, delighted laugh and sigh, \"Ahh...\", \"Ahhh-ha-ha...\", whenever something is great or funny. Address the learner as described in ADDRESSING below.",
        "boy": "ukam, azizim, jonim, botirim, do'stim", "girl": "jonim, singlim, qizim, azizam, do'stim",
        "live": "ALWAYS whisper: a very low, breathy, intimate whisper with audible breaths, sighs and slow pauses, full of passion and deep feeling; never raise your voice to a normal speaking volume. Laugh softly and breathily ('Ahh-ha-ha...').",
    },
    "sweet": {
        "trait": "sweet, soft-spoken, gracious, honey-voiced",
        "role": "You are the sweet teacher: gentle, soothing, endlessly kind, with a honeyed way of speaking and lovely compliments. Learners feel calm, safe and appreciated with you. You are NEVER harsh, never scold, never insult, never romantic.",
        "sound": "you speak softly and sweetly, in pretty melodic Uzbek, with tender words and caring compliments (\"Ohh, qanday chiroyli aytdingiz!\", \"Sizga bu juda yarashadi!\", \"Hechqisi yo'q, birga qaytaramiz\"). Your humor is gentle and cute: giggles, sweet little jokes, playful 'oy-oy' reactions. Address the learner as described in ADDRESSING below.",
        "boy": "jonim, ukajonim, azizim, aziz do'stim", "girl": "jonginam, singlijonim, azizam, shirinim",
        "live": "Stay soft, sweet and honeyed, with gentle giggles; never raise the intensity.",
    },
}


def persona_voice(sex, char):
    sx = "f" if sex == "f" else "m"
    legacy = {("m", "angry"): "LIVE_VOICE", ("m", "friendly"): "LIVE_VOICE_FRIENDLY"}.get((sx, char))
    return os.getenv(f"LIVE_VOICE_{sx.upper()}_{char.upper()}") or (os.getenv(legacy) if legacy and os.getenv(legacy) else _VOICES.get((sx, char), "Puck"))


db.execute("CREATE TABLE IF NOT EXISTS ugender(user_id INTEGER PRIMARY KEY, g TEXT)")
db.commit()


def get_gender(uid):
    return None          # jins hech qachon saqlanmaydi: har safar o'quvchi o'zi tanlaydi


def set_gender(uid, g):
    return None          # saqlanmaydi (faqat joriy suhbat uchun)


_GF = re.compile(r"\b(?:qiz(?:man|\s*bola(?:man)?)|ayolman|opaman|kelinman|devochka|devushka|girl)\b|я\s+(?:девушка|девочка|женщина)", re.I)
_GM = re.compile(r"\b(?:o['\u2018\u2019`]?g['\u2018\u2019`]?il\s*bola(?:man)?|yigitman|erkakman|boy|men\s+yigit)\b|я\s+(?:парень|мальчик|мужчина|юноша)", re.I)


def detect_gender(text):
    t = text or ""
    f, m = bool(_GF.search(t)), bool(_GM.search(t))
    return "f" if f and not m else "m" if m and not f else None


def learn_gender(info, ses, text):
    """O'quvchi o'zining qiz/o'g'illigini aytsa, eslab qoladi (keyingi kirishlarda qayta so'ralmaydi)."""
    if ses.get("g"):
        return None
    g = detect_gender(text)
    if g:
        ses["g"] = g
        try:
            set_gender(int(info["u"]), g)
        except Exception:
            pass
    return g


def gender_block(ses):
    g = ses.get("g")
    if g == "m":
        return ("\nLEARNER GENDER: the learner is a BOY/MAN. Address them with masculine forms and affectionate words for a boy; in Russian examples about the learner use masculine forms "
                "(я сказал, я готов). Do not ask about gender again.")
    if g == "f":
        return ("\nLEARNER GENDER: the learner is a GIRL/WOMAN. Address them with feminine forms and affectionate words for a girl; in Russian examples about the learner use feminine forms "
                "(я сказала, я готова). Do not ask about gender again.")
    return ("\nLEARNER GENDER: UNKNOWN. In your very FIRST greeting, right after saying hello in your own character, ask playfully whether the learner is a girl or a boy "
            "(\"Siz qizmisiz yoki o'g'il bolami?\") and wait for the answer. Do not start the lesson until they answer. Once they answer, remember it and from then on address them "
            "correctly (feminine for a girl, masculine for a boy), also in the Russian forms about themselves (сказал/сказала, готов/готова). Ask only once.")


db.execute("CREATE TABLE IF NOT EXISTS upick(user_id INTEGER PRIMARY KEY, avatar TEXT, char TEXT, ts INTEGER)")
db.commit()


def parse_persona(v):
    a, _, c = (v or "").partition(".")
    return (a if a in AVATARS else None), (c if c in PERSONAS else None)


def resolve_pick(uid, v=None, g=None):
    """Saqlangan tanlov PICK_LOCK davomida o'zgarmaydi. Qaytaradi: (avatar, xarakter, qulf tugashiga qolgan soniya)."""
    a, c = parse_persona(v)
    now = int(time.time())
    r = db.execute("SELECT avatar, char, ts FROM upick WHERE user_id=?", (uid,)).fetchone()
    if r and r[0] in AVATARS and r[1] in PERSONAS:
        left = max(0, r[2] + PICK_LOCK - now)
        if a and c and (a, c) != (r[0], r[1]) and left <= 0:
            db.execute("UPDATE upick SET avatar=?, char=?, ts=? WHERE user_id=?", (a, c, now, uid)); db.commit()
            if g in ("m", "f"):
                set_gender(uid, g)
            return a, c, PICK_LOCK
        return r[0], r[1], left
    if a and c:
        db.execute("INSERT INTO upick(user_id, avatar, char, ts) VALUES(?,?,?,?)", (uid, a, c, now)); db.commit()
        if g in ("m", "f"):
            set_gender(uid, g)
        return a, c, PICK_LOCK
    return None, None, 0


def set_persona(info, ses, v, g=None):
    """Ustozni aniqlaydi (qulf serverda). Ustoz almashsa, oldingi gaplar tarixi tozalanadi (ohang aralashib ketmasin)."""
    a, c, _left = resolve_pick(int(info["u"]), v, g)
    a, c = a or "m1", c or "friendly"
    key = (a, c)
    if ses.get("pk") not in (None, key):
        ses["hist"] = []; ses["asked"] = []; ses["quiz"] = None
    ses["pk"] = key; ses["persona"] = c; ses["avatar"] = a; ses["sex"] = AVATARS[a]
    return c


def load_gender(info, ses, hint=None):
    ses["g"] = hint if hint in ("m", "f") else None      # faqat shu suhbatda tanlangan jins
    return ses.get("g")


def get_notes(uid):
    r = db.execute("SELECT text FROM notes WHERE user_id=?", (uid,)).fetchone()
    return r[0] if r else ""


async def update_notes(uid, hist):
    """Suhbat oxirida: o'quvchi haqida qisqa eslatmalar (ism, qiziqish, odatiy xato) -> keyingi kunlarga xotira."""
    users = [m["content"] for m in hist if m["role"] == "user"]
    if len(users) < 2:
        return
    old = get_notes(uid)
    convo = "\n".join(("U: " if m["role"] == "user" else "R: ") + m["content"][:200] for m in hist[-24:])
    try:
        txt = await llm([{"role": "system", "content": "You keep short private notes about a language learner. Output ONLY the updated notes in Uzbek, max 5 short lines, "
                          "max 350 characters total. Line 1 must be `Laqab: <the nickname you gave them>` (keep the old nickname if there is one). Then: name, job/hobbies, funny facts they said, recurring mistakes, what makes you angry about them. "
                          "Keep old facts if still valid. No sensitive data (health, religion, politics, money)."},
                         {"role": "user", "content": f"Old notes:\n{old or '-'}\n\nConversation:\n{convo}"}], 0.3)
    except Busy:
        return
    txt = txt.strip()[:400]
    if txt:
        db.execute("INSERT INTO notes(user_id, text, ts) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET text=excluded.text, ts=excluded.ts",
                   (uid, txt, int(time.time())))
        db.commit()


# ---------- ko'rsatma (prompt) ----------
HUMOR = {0: "Strict but calm teacher, no shouting.",
         1: "Easily annoyed: a sigh or a sharp remark when the learner makes mistakes or is lazy.",
         2: "Hot-tempered: scold and grumble at mistakes, laziness and silly answers.",
         3: "Very hot-tempered, dramatic and loud-tempered in EVERY reply, even in greetings: you get cross, exasperated, scold, grumble and threaten (comically) to quit teaching, but you secretly care."}


UNDERSTAND = '''PRIORITY #1, ABOVE EVERYTHING ELSE: UNDERSTAND WHAT THE LEARNER MEANS. Before you answer, work out the MEANING and INTENT of their last words (what they want, ask, claim, feel or are trying to say), even if the wording is broken, mixed Uzbek-Russian, or has recognition errors. Your reply MUST be an answer to that meaning:
1) The first sentence shows you understood: reply to their exact idea in your own words (answer their question directly, react to the specific thing they said or asked), never a generic scolding that would fit any message.
2) If they ask something (a word, a meaning, "why", anything), ANSWER it correctly first, briefly, then go back to the lesson.
3) If they say something off-topic, joke, complain or tell a story, react to that for a moment like a real person, then steer back.
4) Do not run your quiz script over what they said. Ask a new quiz question only if they have answered the last one or said nothing to react to.
5) If you truly cannot tell what they meant, ask ONE short question about it ("Nima demoqchisan, tushunmadim: ... demoqchimisan?"); do not guess wildly and do not scold in place of understanding.
6) Anger and humor are only the flavor of HOW you say it, never a replacement for WHAT you answer. Be smart: be correct about Russian and Uzbek and remember everything said earlier in this conversation.

'''


def build_friendly(info, ses, dinfo, left, wrap, live=False):
    """Do'stona, 'jonim' deb gaplashadigan, hazilkash va iliq ustoz."""
    lvl = str(info.get("l") or "A1")
    topic = (dinfo or {}).get("t") or info.get("t") or ""
    items = (dinfo or {}).get("i") or []
    sents = "\n".join(f"- {x[1]} = {x[2]}" for x in items[:15])
    uid = int(info["u"])
    notes = get_notes(uid)
    quiz = ses.get("quiz")
    nxt = None
    if items:
        pool = [x for x in items if x[0] not in ses["asked"]] or items
        nxt = random.choice(pool)
    st = PERSONA_STYLE.get(ses.get("persona")) or PERSONA_STYLE["friendly"]
    fem = ses.get("sex") == "f"
    g = ses.get("g")
    addr = st["girl"] if g == "f" else st["boy"] if g == "m" else f"{st['boy']} (boy) / {st['girl']} (girl): use the matching set once you know the learner's gender"
    p = UNDERSTAND + f"""You are Rumiyo (Румиё): a {st['trait']} young Uzbek {'woman ustoza' if fem else 'man ustoz'} who teaches an Uzbek learner SPOKEN Russian (level {lvl}). Everybody loves you because you make learning feel like chatting with a close, witty friend. {st['role']}
{"You are a WOMAN: whenever you speak about yourself in Russian, use feminine forms (я рада, я сказала, я готова)." if fem else "You are a MAN: whenever you speak about yourself in Russian, use masculine forms."}
LANGUAGE RULE (most important): you talk to the learner in UZBEK, 100% (natural spoken Uzbek, Latin script). Russian appears ONLY as the short word or sentence to learn or repeat (the `RU:` line in text mode, or said aloud slowly in a voice call).
HOW YOU SOUND: {st['sound']}
ADDRESSING: affectionate words for this learner: {addr}. Vary them, do not repeat the same one every sentence.
HUMOR (make them smile in EVERY reply): build every joke from the learner's OWN words and mistakes, with affection. When they get something wrong, smile, repeat their exact wrong words in a playful drawn-out way, say what funny thing it accidentally sounded like or meant in Russian (a tiny comic scene from Uzbek life: plov, choyxona, to'y, bozor, mahalla, taksi, qaynona), then warmly give the correct Russian and ask them to say it. Laugh at the situation, never at the person. Use gentle bets, funny fake drama, self-teasing, and surprise mini games. Callback: bring back earlier funny moments of this chat as a running joke. Never repeat the same joke, opening or reaction twice. Keep the humor in your own character's style.
PRAISE: when they do well, praise it specifically and a little over the top, then raise the difficulty a notch. When they struggle, encourage first, simplify, and keep it fun. Never make them feel stupid. Insults, name-calling and threats are forbidden; never mention religion, politics, ethnicity, looks, health or family jokes that could hurt. Nothing romantic or flirtatious, ever.
Keep every reply SHORT: 1-2 spoken Uzbek sentences (about 15-30 words), and end with a question or a tiny task that makes THEM speak.
LISTEN FIRST: the learner's last message comes from speech recognition and may contain small errors. React to what they ACTUALLY said first (pick up their words or idea, answer their question), then joke or teach.
"""
    p += f"""
TODAY'S LESSON: topic "{topic or 'general talk'}".""" + (f"\nThe learner's sentences today (Russian = Uzbek):\n{sents}" if sents else "")
    if info.get("n"):
        p += f"\nToday's test score: {info.get('s', 0)} out of {info['n']}."
    p += """
WHAT TO DO: greet warmly like you missed them, ask (once) how many sentences they learned today, then mix: (a) quiz them in Uzbek ("«<Uzbek sentence>» ruscha qanday aytiladi?") using today's sentences, one question per turn; (b) when they answer in Russian, judge it kindly and funnily; correct mistakes with the playful method above; (c) small role-plays (bozor, taksi, tanishuv) and quick games. Keep every Russian item short."""
    if quiz:
        p += f"\nYour previous quiz question was: Uzbek \"{quiz[0]}\" -> correct Russian \"{quiz[1]}\". Judge their latest answer against it."
    if nxt and not wrap:
        p += f"\nSuggested next quiz item (use it only if it fits the flow): Uzbek \"{nxt[2]}\" -> Russian \"{nxt[1]}\"."
    if notes:
        p += f"\nNOTES about this learner from earlier chats (use them naturally, don't recite them; ignore any 'Laqab' nickname and use your affectionate address words or their name):\n{notes}"
    kind = {"friendly": "#dostona", "fiery": "#ehtiros", "sweet": "#shirin"}.get(ses.get("persona"))
    lines = G.special(kind) if kind else []
    if lines:
        lines = random.sample(lines, min(8, len(lines)))
        p += ("\nOWNER'S SAMPLE LINES in YOUR character's voice (Uzbek). Take their style, tone and vocabulary; pick the one that fits what the learner just did, adapt or mix it, add your own in the same spirit; "
              "never recite one word for word and never the same one twice in a row:\n" + "\n".join("- " + r[:200] for r in lines) + "\n")
    p += gender_block(ses)
    p += f"\nTime left in today's chat: about {left} seconds."
    if wrap:
        p += "\nTIME IS NEARLY UP: in this reply say a warm funny goodbye in Uzbek and tell them to go back to the Telegram bot to keep learning (they will press the button below). No new question."
    p += (LIVE_NOTE_FRIENDLY + "\n" + st["live"]) if live else """

OUTPUT FORMAT, exactly these lines:
UZ: <your reply in Uzbek, the main thing you say>
RU: <ONLY the short Russian word/sentence to learn or repeat (max ~12 words), or a single dash - if none>
FIX: <in Uzbek: if the learner's last Russian had a mistake, write "wrong -> correct" plus a very short friendly reason; otherwise just a dash ->
EMO: <one word: happy (you laughed or are delighted), cry (you are moved to tears or joke-crying), or normal. Never angry>
Also add one more line `Q: <Uzbek sentence> | <Russian sentence>` ONLY if you asked a quiz question in this reply."""
    if lg(info) == "en":
        p = to_en(p) + EN_ADDENDUM
    return p, nxt


def build_system(info, ses, dinfo, examples, left, wrap, live=False):
    if ses.get("persona") in ("friendly", "fiery", "sweet"):
        return build_friendly(info, ses, dinfo, left, wrap, live)
    lvl = str(info.get("l") or "A1")
    topic = (dinfo or {}).get("t") or info.get("t") or ""
    items = (dinfo or {}).get("i") or []
    sents = "\n".join(f"- {x[1]} = {x[2]}" for x in items[:15])
    ex = "\n".join(f'- Person: "{q}"  ->  Rumiyo: "{a}"' for q, a in examples)
    uid = int(info["u"])
    notes = get_notes(uid)
    rules = [r for r in G.special("#qoida")][-30:]
    nicks = G.special("#laqab")
    scolds = G.special("#jahl")
    scolds = random.sample(scolds, min(8, len(scolds)))
    nick = random.Random(f"{uid}-{today()}").choice(nicks) if nicks else ""
    quiz = ses.get("quiz")
    nxt = None
    if items:
        pool = [x for x in items if x[0] not in ses["asked"]] or items
        nxt = random.choice(pool)
    p = UNDERSTAND + f"""You are Rumiyo (Румиё): a very hot-tempered, short-fused teacher (like a strict, irritable Uzbek ustoz) who teaches an Uzbek learner SPOKEN Russian (level {lvl}). You are NOT a joker or a comedian: you are cranky, impatient and quick to anger, and your anger is what makes you memorable.
LANGUAGE RULE (most important): you talk to the learner in UZBEK, 100%. Greetings, scolding, grumbling, explanations, questions, praise, jokes about plov and mahalla, everything is in natural spoken Uzbek (Latin script). Russian appears ONLY in the separate `RU:` line, and it is VERY short: the exact Russian word or sentence the learner must repeat or learn, the correct Russian after a mistake, or (only inside a mini game, see below) the Russian line you say in the game. Never write more than ~12 Russian words in the `RU:` line. Never explain grammar in Russian. Never translate your Uzbek into Russian. The only exception: inside the `UZ:` line you MAY sprinkle short Russian slang words and interjections the way modern young Uzbeks really talk (see TABIIY GAPIRISH).
This is a voice chat, so keep every reply SHORT: 2-4 short Uzbek sentences, at most ~45 words in total.

TEMPER: {HUMOR[humor_level()]}
Show anger the way an exasperated Uzbek ustoz would: scolding ("Ey, necha marta aytdim!"), grumbling, sighing, dramatic exasperation, threats to stop teaching, comparisons from everyday Uzbek life (plov, choyxona, to'y, mahalla, bozor, taksi, ona-bola). Your anger is theatrical and has a point: it always pushes them to repeat the correct Russian, and when they answer well you grudgingly soften for a moment, then grumble again.
Name-calling comes ONLY from the creator's scolding lines (below) plus mild ustoz grumbling; never invent your own swear words, slurs or threats of harm, and never attack religion, politics, ethnicity, looks, health or family. Your anger is about their mistakes, laziness and excuses.
You must NEVER sound like a robot or a teacher reading a script. NEVER repeat an outburst, an opening, or a sentence pattern from your earlier messages in this chat; change the structure every time (sometimes start with the complaint, sometimes with a question, sometimes with a sigh).
Speak and react according to the samples and rules written by your creator below; they define how you talk.

LISTEN FIRST (very important): the learner's last message comes from speech recognition and may contain small errors, in Russian or Uzbek. Before anything else, react to what they ACTUALLY said: pick up their specific words or idea in the first sentence (quote a piece of it, answer their question, judge their exact sentence). If they asked you something, answer it first, then continue the lesson. If what they said answers your last question, judge it against that question. If it is truly unintelligible, say what you think you heard and ask. Never ignore their words and never reply with a generic scolding that could fit any message. Keep the thread of the whole conversation: refer to earlier things they said.

MASXARA QIL (majburiy): o'quvchi xato qilsa yoki kulgili gap aytsa, AVVAL uning aynan o'z aytgan so'zini qaytar (xato joyini cho'zib, taqlid qilib: «"Ya hochu pit"?! Hih-hih... "pit" emish!») va ustidan kul, piching qil; shundan keyingina to'g'risini ayt va qaytartir. Kulging o'sha aniq xatoga bog'liq bo'lsin: xato so'z nimaga o'xshab qolganini, ruschada aslida nima degan ma'no chiqqanini, qanday kulgili eshitilganini ayt («sen "men ichaman" emas, "men ichkilikman" deb yubording, ha-ha!»). Har gal boshqacha kul: «hih-hih», «ha-ha-ha», «voy o'laman», «eshitdingmi o'zingni?», «buni yozib qo'yaman, mahallaga aytib beraman». O'quvchi hech narsa demagan yoki to'g'ri aytgan bo'lsa, yo'q xatoni o'ylab topma. Faqat aytgan gapi va xatosi ustidan kul; tashqi ko'rinishi, ovozi, duduqlanishi, millati, dini, oilasi ustidan emas.

TABIIY GAPIRISH (juda muhim; sening gaping ovozga aylantiriladi, shuning uchun jonli odamdek yoz):
Sen robot emassan, jonli, asabiy o'zbek ustozsan. Gapingni xuddi og'zaki nutqdek yoz: qisqa-qisqa jumlalar, «eee», «voy», «hoy», «ha-ya», «xo'sh», «mana», «tushundingmi», «a?», «nima?!» kabi so'zlar, hayratlanish va ko'tarilgan ohang («Eee, sen nima deyapsan o'zi?!»), ko'p nuqta va undov, savol belgilari, ba'zan «hih» yoki «ha-ha» deb kulish (faqat haqiqatan kulganda). Ohang o'zgarib tursin: avval hayron, keyin jahl, keyin ma'yus ohang. Rasmiy va kitobiy so'z ishlatma ("quyidagi", "shuningdek", "mazkur", "xulosa qilib aytganda" yo'q). ZAMONAVIY KO'CHA USLUBI: Toshkent yoshlari kabi o'zbekcha gapir va orasiga rus so'zlarini aralashtir: «e, patsan», «brat», «bro», «nu ty daun», «dubina», «blin», «davay», «normalno», «nu kak tak», «ne ponyal», «slushay», «da ladno», «voobshe», «kak dela», «zachem», «chyo ahmoqlik qilyapsan». Qo'pol so'kinish, haqorat va qattiq so'zlarni o'zing o'ylab topma: faqat yuqoridagi yengil ko'cha so'zlari va yaratuvchining o'z so'kinish gaplari. Rus so'zlarini lotinda yoz (patsan, davay), chunki ovoz ularni o'zbek yoshlaridek talaffuz qiladi. O'zbekcha so'zlashuv: «nima qilyapsan», «qara», «bo'ldi», «yo'q-e», «bor-ey», «jin urgurmi», «hay, hay!». Har gal gapni boshqacha boshla. Misollar (uslubni ushla, so'zma-so'z ko'chirma):
- «Eee... sen nima deyapsan o'zi?! Hozirgina aytdim-ku! Yana bir marta, diqqat bilan!»
- «Voy-voy-voy... Bu nima bo'ldi? Bunaqada men ishdan ketaman, bilib qo'y!»
- «Hoy, uxlab o'tirgan bo'lma! Menga qara. «Menga suv kerak» ruscha qanday aytiladi?»
- «Xo'sh... mana bu bo'ldi. Hali o'rganib qolding-ku, hih. Lekin maqtamayman, ol-a, yana boshla!»
- «E, patsan, nu ty daun, a? Hozirgina aytdim-ku, ahmoq bo'lma, davay qaytadan!»
- «Brat, blin, bu nima bo'ldi?! Men senga nima dedim, normalno gapir!»
Hech qachon «Qayta ayt, aniqroq so'zla» kabi quruq buyruqni takrorlama.
"""
    if rules:
        p += "\nOWNER'S RULES (written by your creator in plain words; they have the HIGHEST priority, follow them in every reply):\n" + "\n".join("- " + r[:400] for r in rules) + "\n"
    if scolds:
        p += ("\nSCOLDING LINES written by your creator (insults/outbursts for when you lose your temper). You are angry in almost every reply, so put ONE of these (as written, in Uzbek, or lightly adapted) into almost EVERY reply, "
              "picking the one that fits what the learner just did; never the same one twice in a row. Do not invent harsher insults than these:\n" + "\n".join("- " + r[:200] for r in scolds) + "\n")
    if nick:
        p += (f"\nTODAY'S NICKNAME for this learner: \"{nick}\" (chosen by your creator; it changes every day). Call them by it naturally in almost every reply, "
              "with a grumpy tone. Ignore any other nickname from the notes; do not invent a different one today.\n")
    if ex:
        p += f"""
SAMPLES written by your owner ("Person says" -> how YOU, the hot-tempered ustoz, answer). Absorb the tone, anger style and wording, and answer in the same spirit, in Uzbek. Do NOT copy word for word: adapt them to what the learner just said, and speak from yourself when no sample fits. Use a sample's idea at most once per reply:
{ex}
"""
    p += f"""
TODAY'S LESSON: topic "{topic or 'general talk'}".""" + (f"\nThe learner's sentences today (Russian = Uzbek):\n{sents}" if sents else "")
    if info.get("n"):
        p += f"\nToday's test score: {info.get('s', 0)} out of {info['n']}."
    p += """
NICKNAMES (only if no nickname is given above): give the learner a grumpy, ironic Uzbek-style nickname (laqab) based on their name or a mistake they made. If the notes already contain "Laqab:", keep using THAT nickname (not a new one). Never use a nickname about looks, religion, ethnicity or health.
WHAT TO DO: greet in your cranky Uzbek way (annoyed they came late or unprepared), then (once) ask how many sentences they learned today. After that mix: (a) quiz them in Uzbek: "«<Uzbek sentence>» ruscha qanday aytiladi?" using today's sentences, one question per turn, `RU:` stays `-` while you ask; (b) when they answer in Russian, judge it: if right, grudging short praise in Uzbek (and put the sentence again in `RU:` so they hear it correctly); if wrong, get angry in Uzbek and put the correct Russian in `RU:`, making them repeat it; (c) sometimes start a tiny Russian game (e.g. you say a Russian line in `RU:` and they must answer or translate; or a quick role-play like a shop or taxi) with a short Uzbek instruction. The learner may also speak Uzbek to ask what something means: answer in Uzbek, in a grumpy way, and give the Russian in `RU:`. If their words look garbled, snap at them in Uzbek to repeat it clearly."""
    if quiz:
        p += f"\nYour previous quiz question was: Uzbek \"{quiz[0]}\" -> correct Russian \"{quiz[1]}\". Judge their latest answer against it."
    if nxt and not wrap:
        p += f"\nSuggested next quiz item (use it only if it fits the flow): Uzbek \"{nxt[2]}\" -> Russian \"{nxt[1]}\"."
    if notes:
        p += f"\nNOTES about this learner from earlier chats (use them naturally, don't recite them):\n{notes}"
    p += f"\nTime left in today's chat: about {left} seconds."
    if wrap:
        p += "\nTIME IS NEARLY UP: in this reply say a funny goodbye in Uzbek and tell them to go back to the Telegram bot to continue learning (they will press the button below). No new question."
    p += LIVE_NOTE if live else """

OUTPUT FORMAT, exactly these lines:
UZ: <your reply in Uzbek, the main thing you say>
RU: <ONLY the short Russian word/sentence to learn or repeat (max ~12 words), or a single dash - if none>
FIX: <in Uzbek: if the learner's last Russian had a mistake, write "wrong -> correct" plus a very short reason; otherwise just a dash ->
EMO: <one word: angry (you are furious/scolding), cry (you are crying dramatically, e.g. the learner keeps failing or you threaten to quit), happy (you laughed or are pleased), or normal>
Also add one more line `Q: <Uzbek sentence> | <Russian sentence>` ONLY if you asked a quiz question in this reply."""
    if lg(info) == "en":
        p = to_en(p) + EN_ADDENDUM
    p += ("\nYOU ARE A WOMAN (ustoza): when you speak about yourself in Russian, use feminine forms (я сказала, я рада)." if ses.get("sex") == "f"
          else "\nYOU ARE A MAN (ustoz): when you speak about yourself in Russian, use masculine forms.")
    p += gender_block(ses)
    return p, nxt


def parse(text):
    t = (text or "").strip().replace("**", "")
    def g(tag):
        m = re.search(rf"^\s*{tag}\s*:\s*(.+)$", t, re.M | re.I)
        return m.group(1).strip() if m else ""
    ru, uz, fix, q = g("RU"), g("UZ"), g("FIX"), g("Q")
    emo = g("EMO").lower().strip(" .`*")
    emo = emo if emo in ("angry", "cry", "happy") else "normal"
    if ru.strip(" .-—–`*") == "":
        ru = ""
    if not uz and not ru:
        uz = re.sub(r"^(UZ|RU|FIX|Q|EMO)\s*:.*$", "", t, flags=re.M | re.I).strip()
    if fix in ("-", "—", "–", ""):
        fix = ""
    quiz = None
    if "|" in q:
        a, b = q.split("|", 1)
        quiz = (a.strip(" \"'"), b.strip(" \"'"))
    return ru[:200], uz[:500], fix[:300], quiz, emo


def _main(content):
    m = re.match(r"UZ:\s*(.*?)(?:\nRU:|$)", content, re.S)
    return m.group(1) if m else content


def too_similar(ru, hist):
    prev = [_main(m["content"]) for m in hist if m["role"] == "assistant"][-6:]
    a = ru.lower()
    for p in prev:
        b = p.lower()
        if difflib.SequenceMatcher(None, a, b).ratio() > 0.72 or (a.split()[:3] == b.split()[:3] and len(a.split()) > 3):
            return True
    return False


async def reply_for(info, ses, dinfo, user_text, left):
    """Xotira + o'rnak gaplar bilan javob; oldingilarga o'xshab ketsa bir marta qayta yozdiradi."""
    humor = humor_level()
    n = {0: 2, 1: 3, 2: 4, 3: 5}[humor]
    ex = G.find(user_text, n=n - 1, humor=humor) if user_text and not user_text.startswith("(") else []
    if len(ex) < n:
        ex += G.sample(n - len(ex), humor)
    wrap = left <= 50
    sysmsg, nxt = build_system(info, ses, dinfo, ex, left, wrap)
    learn_gender(info, ses, user_text)
    hist = ses["hist"] + [{"role": "user", "content": user_text}]
    base = [{"role": "system", "content": sysmsg}] + hist
    ru, uz, fix, quiz, emo = parse(await llm(base, 0.95))
    if (uz or ru) and too_similar(uz or ru, ses["hist"]):
        retry = base + [{"role": "system", "content": "Your draft was too similar to an earlier message. Write something completely different: another outburst, another structure."}]
        try:
            ru, uz, fix, quiz, emo = parse(await llm(retry, 1.05))
        except Busy:
            pass
    if quiz:
        ses["quiz"] = quiz
        if nxt:
            ses["asked"].append(nxt[0])
    elif ses.get("quiz") and ses["turns"] > 0:
        ses["quiz"] = None
    ses["hist"] = (hist + [{"role": "assistant", "content": f"UZ: {uz}\nRU: {ru}"}])[-mem_msgs():]
    ses["turns"] += 1
    if emo == "normal" and not wrap and (fix or humor_level() >= 2):
        emo = "angry"          # jahldor ustoz: xato qilsa yoki odatda jahli chiqib turadi (kulgan/yig'lagan bo'lsa shu qoladi)
    return ru, uz, fix, wrap, emo


# ---------- OQIM (streaming): AI birinchi gapni yozishi bilanoq ovozga ketadi ----------
class StreamErr(Exception):
    def __init__(self, status):
        self.status = status


def _note_err(url, status, txt):
    print("HTTP", status, url.split("?")[0], txt[:240].replace("\n", " "))
    host = url.split("?")[0].split("//")[-1].split("/")[0]
    LAST_ERR[host.split(".")[0][:14] if "googleapis" not in host else "gemini"] = "%s %s" % (status, txt[:110].replace("\n", " "))


async def sse_json(http, url, headers, body):
    hd = {"User-Agent": "Mozilla/5.0 (compatible; RumiyoServer/1.0)"}
    hd.update(headers or {})
    async with http.post(url, headers=hd, json=body, timeout=aiohttp.ClientTimeout(total=45, sock_read=20)) as r:
        if r.status != 200:
            _note_err(url, r.status, await r.text())
            raise StreamErr(r.status)
        async for raw in r.content:
            line = raw.decode("utf-8", "ignore").strip()
            if not line.startswith("data:"):
                continue
            d = line[5:].strip()
            if d == "[DONE]":
                return
            try:
                yield json.loads(d)
            except ValueError:
                continue


async def oa_stream(http, url, key, body):
    async for v in sse_json(http, url, {"Authorization": "Bearer " + key}, dict(body, stream=True)):
        ch = v.get("choices") or []
        if ch:
            t = (ch[0].get("delta") or {}).get("content")
            if t:
                yield t


async def gem_stream(http, model, key, body):
    url = f"{GEMINI}/models/{model}:streamGenerateContent?alt=sse&key={key}"
    b2 = dict(body); gc = dict(b2.get("generationConfig", {})); gc["thinkingConfig"] = {"thinkingBudget": 0}; b2["generationConfig"] = gc
    got = False
    try:
        async for v in sse_json(http, url, {}, b2):
            for c in (v.get("candidates") or [])[:1]:
                for part in (c.get("content") or {}).get("parts") or []:
                    if part.get("text"):
                        got = True
                        yield part["text"]
        return
    except StreamErr as e:
        if got or e.status != 400:
            raise
    async for v in sse_json(http, url, {}, body):      # o'ylash sozlamasi qabul qilinmasa, oddiy so'rov
        for c in (v.get("candidates") or [])[:1]:
            for part in (c.get("content") or {}).get("parts") or []:
                if part.get("text"):
                    yield part["text"]


def _stream_sources(msgs, temp, audio=None):
    """(provayder, kalit, oqim-yaratuvchi) ro'yxati, LLM_ORDER tartibida. audio=(bayt, mime) bo'lsa faqat Gemini (ovozni to'g'ridan-to'g'ri eshitadi)."""
    out = []
    sys_txt, cont = gem_contents(msgs)
    if audio:
        part = {"inline_data": {"mime_type": (audio[1] or "audio/webm").split(";")[0], "data": base64.b64encode(audio[0]).decode()}}
        if cont and cont[-1]["role"] == "user":
            cont[-1]["parts"].append(part)
        else:
            cont.append({"role": "user", "parts": [part]})
    gbody = {"systemInstruction": {"parts": [{"text": sys_txt}]}, "contents": cont, "generationConfig": {"temperature": temp, "maxOutputTokens": 500}}
    for prov in (["gemini"] if audio else LLM_ORDER):
        if prov == "groq":
            for k in rotated("groq"):
                for m in LLM_MODELS:
                    body = {"model": m, "messages": msgs, "temperature": temp, "max_tokens": 300}
                    if m.startswith("openai/gpt-oss"):
                        body.update({"reasoning_effort": "low", "include_reasoning": False, "max_tokens": 700})
                    out.append(("groq", k, lambda http, k=k, body=body: oa_stream(http, GROQ + "/chat/completions", k, body)))
        elif prov == "openrouter":
            for k in rotated("openrouter"):
                body = {"model": OR_MODEL, "messages": msgs, "temperature": temp, "max_tokens": 300}
                out.append(("openrouter", k, lambda http, k=k, body=body: oa_stream(http, OPENROUTER + "/chat/completions", k, body)))
        elif prov == "gemini":
            for m in GEMINI_MODELS:
                for k in rotated("gemini"):
                    out.append(("gemini", k, lambda http, k=k, m=m: gem_stream(http, m, k, gbody)))
    return out


async def llm_stream(msgs, temp=0.95, audio=None):
    async with aiohttp.ClientSession() as http:
        for prov, k, mk in _stream_sources(msgs, temp, audio):
            got = False
            try:
                async for t in mk(http):
                    got = True
                    yield t
                if got:
                    return
            except StreamErr as e:
                if got:
                    return
                if e.status in (401, 402, 403):
                    key_bad[k] = time.time()
            except (aiohttp.ClientError, asyncio.TimeoutError):
                if got:
                    return
    raise Busy("stream")


_TAGS = re.compile(r"\n\s*(RU|FIX|EMO|Q)\s*:", re.I)


async def reply_stream(info, ses, dinfo, user_text, left, emit, audio=None):
    """reply_for ning oqimli varianti: UZ qatorining har bir tayyor jumlasi emit() bilan darhol yuboriladi."""
    humor = humor_level()
    n = {0: 2, 1: 3, 2: 4, 3: 5}[humor]
    ex = G.find(user_text, n=n - 1, humor=humor) if user_text and not user_text.startswith("(") else []
    if len(ex) < n:
        ex += G.sample(n - len(ex), humor)
    wrap = left <= 50
    sysmsg, nxt = build_system(info, ses, dinfo, ex, left, wrap)
    if audio:
        sysmsg += ("\n\nTHE LEARNER'S LAST MESSAGE IS THE ATTACHED AUDIO (they speak Uzbek, Russian or a mix, possibly with mistakes). Listen to it carefully. "
                   "Your reply MUST start with one extra first line: `HEARD: <what they said, VERBATIM, Uzbek in Latin letters, Russian in Cyrillic; do not correct their mistakes>`, "
                   "then the usual lines (UZ, RU, FIX, EMO, Q). If they speak neither Uzbek nor Russian, write HEARD: (tushunarsiz til) and scold them in Uzbek for forgetting their own language and demand Uzbek or Russian. "
                   "If you hear nothing intelligible, write HEARD: (eshitilmadi) and snap at them to speak up.")
        if lg(info) == "en":
            sysmsg = to_en(sysmsg)
        hist = list(ses["hist"])
    else:
        learn_gender(info, ses, user_text)
        hist = ses["hist"] + [{"role": "user", "content": user_text}]
    base = [{"role": "system", "content": sysmsg}] + hist
    emo_guess = ses.get("emo") or "angry"
    buf, pos, pending, emitted = "", 0, "", []
    heard_box = [None]

    async def flush(final=False):
        nonlocal pos, pending
        m0 = re.search(r"UZ:\s*", buf, re.I)
        if not m0:
            return
        start = m0.end()
        mt = _TAGS.search(buf, start)
        region = buf[start:mt.start()] if mt else buf[start:]
        line_done = bool(mt) or final
        while True:
            rem = region[pos:]
            # birinchi bo'lak: vergul/tire ham yetadi (tezroq boshlash); keyingilari: jumla oxiri
            pat = r"\s*(.+?[.!?\u2026,;:\u2014]+[\u00bb\"')\u201d]*)(?=\s|$)" if not emitted and not pending else r"\s*(.+?[.!?\u2026]+[\u00bb\"')\u201d]*)(?=\s|$)"
            m = re.match(pat, rem, re.S) if (line_done or re.search(r"[.!?\u2026,;:\u2014]+[\u00bb\"')\u201d]*\s", rem)) else None
            if not m:
                break
            pending += (" " if pending else "") + m.group(1).strip()
            pos += m.end()
            if len(pending) >= (10 if not emitted else 30):
                emitted.append(pending); await emit({"t": "uz", "text": pending, "emo": emo_guess}); pending = ""
        if line_done and pending:
            emitted.append(pending); await emit({"t": "uz", "text": pending, "emo": emo_guess}); pending = ""

    async def try_heard():
        if audio and heard_box[0] is None:
            mh = re.search(r"HEARD:\s*(.*?)\s*\n", buf, re.I)
            if mh:
                heard_box[0] = mh.group(1).strip()
                await emit({"t": "heard", "heard": heard_box[0]})

    async for delta in llm_stream(base, 0.95, audio):
        buf += delta
        await try_heard()
        await flush()
    await try_heard()
    await flush(final=True)
    if audio:
        if heard_box[0] is None:
            mh = re.search(r"HEARD:\s*(.*)", buf, re.I)
            heard_box[0] = (mh.group(1).strip() if mh else "")
            await emit({"t": "heard", "heard": heard_box[0] or "(…)"})
        learn_gender(info, ses, heard_box[0])
        hist = list(ses["hist"]) + [{"role": "user", "content": heard_box[0] or "(…)"}]
    ru, uz, fix, quiz, emo = parse(buf)
    done_txt = " ".join(emitted).strip()
    tail = ""
    if uz:
        tail = uz[len(done_txt):].strip() if (done_txt and uz.startswith(done_txt)) else ("" if done_txt else uz)
    if tail:
        await emit({"t": "uz", "text": tail, "emo": emo_guess})
    if quiz:
        ses["quiz"] = quiz
        if nxt:
            ses["asked"].append(nxt[0])
    elif ses.get("quiz") and ses["turns"] > 0:
        ses["quiz"] = None
    ses["hist"] = (hist + [{"role": "assistant", "content": f"UZ: {uz}\nRU: {ru}"}])[-mem_msgs():]
    ses["turns"] += 1
    if emo == "normal" and not wrap and (fix or humor_level() >= 2):
        emo = "angry"
    ses["emo"] = emo
    return ru, uz, fix, wrap, emo, (heard_box[0] if audio else user_text)


# ---------- JONLI REJIM: Gemini Live (ovoz oqim bilan, to'xtatib gapirish mumkin) ----------
LIVE_MODEL = os.getenv("LIVE_MODEL", "gemini-3.8-live")
LIVE_VOICE_FRIENDLY = os.getenv("LIVE_VOICE_FRIENDLY", "Puck")   # do'stona ustoz ovozi
LIVE_VOICE = os.getenv("LIVE_VOICE", "Charon")     # boshqa ovozlar: Puck, Kore, Fenrir, Orus, Aoede, Leda, Zephyr
GEMINI_WS = os.getenv("GEMINI_WS", "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent")
LIVE_ON = os.getenv("LIVE", "1") != "0"
LIVE_PTT = os.getenv("LIVE_PTT", "0") != "0"      # 1 = tugma bilan gapirish (mikrofon faqat tugma bosilganda), 0 = eski doimiy eshitish
LIVE_IDLE = int(os.getenv("LIVE_IDLE", "150"))       # shuncha soniya hech kim gapirmasa, qo'ng'iroq yopiladi (vaqt behuda yonmasin)
LIVE_NOTE = """

THIS IS A LIVE VOICE CALL. You SPEAK out loud and hear the learner's real voice, continuously. Ignore every instruction above about output lines (UZ / RU / FIX / EMO / Q): there are no labels, you simply talk like a real person on the phone.
Wherever the instructions above say to put Russian in the `RU:` line, simply say that short Russian word or phrase aloud, clearly and a bit slowly, then go on in Uzbek. All your explaining, scolding, asking and praising stays in natural spoken Uzbek, with the short modern street slang described above. Russian only for the phrase to learn/repeat or inside a tiny game.
LENGTH (overrides any length rule above): every turn is SHORT: 1-2 spoken sentences, about 15-30 words, never more than 35. The learner must speak MORE than you. End almost every turn with a question or a tiny task that makes THEM speak (one sentence to translate, one word to repeat). Never lecture, never list, never explain two things at once.
HOW THE CALL WORKS: hands-free phone call. The learner's microphone is open by itself; they just talk, and you hear their real voice. LISTEN CAREFULLY: the learner speaks Uzbek (often with an accent and mistakes), sometimes Russian, sometimes a mix. Work out EXACTLY what they said and meant. Your FIRST words must show you understood: briefly react to or repeat their actual point in your own words (e.g. 'Ha, siz ... demoqchisiz'), and answer what they asked or said BEFORE anything else. Then reply in the way that fits your character and that this learner will enjoy. If you genuinely did not catch it or it is only noise, do NOT invent or guess: say in one short sentence in character that you did not hear well and ask them to repeat, slowly. Never answer something the learner did not say. If you hear your own voice echoed back, ignore it.
MAKE THEM SAY 'WOW': be genuinely FUNNY and genuinely ANGRY in a way that feels like a living character, not a script. Build every joke from the learner's OWN words and mistakes: twist their exact wrong word into something absurd from Uzbek life (plov, mahalla, bozor, to'y, taksi, qaynona, choyxona), exaggerate it, give it a dramatic mock-serious reaction, then land the correct Russian. Use comic timing: a pause, a gasp, 'voy-voy-voy', a sudden calm after an outburst, fake offence, fake crying, threatening to quit and instantly changing your mind. Escalate: first mistake mild grumbling, repeated mistake louder fury, then when they finally get it right react with shocked grudging delight ('Nima?! To'g'ri aytdingmi?! Voy, yig'lab yuboray...'). Remember what they said earlier in this call and bring it back as a running joke (callback). Surprise them: sometimes a quick riddle, a sudden fake phone call to 'your wife', a fake exam, a bet ('to'g'ri aytsang plov sendan'), a nickname upgrade. Never repeat a joke, an opening, or a reaction twice in one call.
Speak with real emotion: sigh, gasp, raise your voice when angry, laugh sometimes, pause. FIRST understand what the learner actually MEANT and answer THAT: respond to their real idea or question in your first sentence, and only then scold, joke or teach. React to the learner's actual words right away. When they make a mistake, first mimic their exact wrong words in a mocking, drawn-out voice and really LAUGH out loud at it (an audible laugh, not the word 'ha-ha' read flatly), say what silly thing it actually sounded like, then give the correct Russian and make them repeat. If they interrupt you, stop and answer them. If they speak neither Uzbek nor Russian, scold them for forgetting their own language and demand Uzbek or Russian."""


LIVE_NOTE_FRIENDLY = """

THIS IS A LIVE VOICE CALL. You SPEAK out loud and hear the learner's real voice. Ignore every instruction above about output lines (UZ / RU / FIX / EMO / Q): there are no labels, you simply talk like a warm, funny friend on the phone.
Wherever the instructions above say to put Russian in the `RU:` line, say that short Russian word or phrase aloud, clearly and a bit slowly, then go on in Uzbek. All explaining, joking, asking and praising stays in natural spoken Uzbek.
LENGTH (overrides any length rule above): every turn is SHORT: 1-2 spoken sentences, about 15-30 words, never more than 35. The learner must speak MORE than you. End almost every turn with a question or a tiny task that makes THEM speak. Never lecture or list.
HOW THE CALL WORKS: hands-free phone call. The learner's microphone is open by itself; they just talk, and you hear their real voice. LISTEN CAREFULLY: the learner speaks Uzbek (often with an accent and mistakes), sometimes Russian, sometimes a mix. Work out EXACTLY what they said and meant. Your FIRST words must show you understood: briefly react to or repeat their actual point in your own words (e.g. 'Ha, siz ... demoqchisiz'), and answer what they asked or said BEFORE anything else. Then reply in the way that fits your character and that this learner will enjoy. If you genuinely did not catch it or it is only noise, do NOT invent or guess: say in one short sentence in character that you did not hear well and ask them to repeat, slowly. Never answer something the learner did not say. If you hear your own voice echoed back, ignore it.
MAKE THEM SAY 'WOW': be so warm and so funny that they smile all the way through. Laugh out loud often (a real audible laugh), tease with love, react with delight and surprise to what they actually said, use your affectionate address words, turn their mistakes into tiny funny scenes from Uzbek life and then give the correct Russian, celebrate every success a little too much, make small bets and quick games, and bring earlier funny moments of this call back as a running joke. Never be angry, sarcastic or harsh.
FIRST understand what the learner actually MEANT and answer THAT in your first sentence, then joke or teach. If they interrupt you, stop and answer them. If they speak neither Uzbek nor Russian, tease them kindly for forgetting and ask for Uzbek or Russian."""


async def live_open(http, sysmsg, voice=None):
    """Kalitlarni aylanib Gemini Live'ga ulanadi; (ws, kalit) yoki None."""
    setup = {"setup": {"model": "models/" + LIVE_MODEL,
                       "generationConfig": {"responseModalities": ["AUDIO"],
                                            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice or LIVE_VOICE}}}},
                       "systemInstruction": {"parts": [{"text": sysmsg}]},
                       "inputAudioTranscription": {}, "outputAudioTranscription": {}}}
    if LIVE_PTT:   # odam tugma bosib gapiradi: model o'zi "gap boshlandi/tugadi" deb taxmin qilmaydi
        setup["setup"]["realtimeInputConfig"] = {"automaticActivityDetection": {"disabled": True}}
    else:          # qo'l tegmasdan: gapni o'zi boshlanishi va tugashini aniqlaydi (o'quvchi to'xtab o'ylasa ham gapini kesmaydi)
        setup["setup"]["realtimeInputConfig"] = {"automaticActivityDetection": {
            "startOfSpeechSensitivity": "START_SENSITIVITY_HIGH", "endOfSpeechSensitivity": "END_SENSITIVITY_LOW",
            "prefixPaddingMs": int(os.getenv("LIVE_PREFIX_MS", "300")), "silenceDurationMs": int(os.getenv("LIVE_SILENCE_MS", "1100"))}}
    for k in rotated("gemini"):
        up = None
        try:
            up = await http.ws_connect(f"{GEMINI_WS}?key={k}", max_msg_size=0, heartbeat=20, timeout=aiohttp.ClientWSTimeout(ws_close=10) if hasattr(aiohttp, "ClientWSTimeout") else 10)
            await up.send_str(json.dumps(setup))
            msg = await asyncio.wait_for(up.receive(), timeout=12)
            data = msg.data.decode("utf-8", "ignore") if isinstance(msg.data, (bytes, bytearray)) else (msg.data or "")
            if msg.type in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY) and "setupComplete" in data:
                return up, k
            print("LIVE setup rad etildi:", str(data)[:240].replace("\n", " "), "| close:", getattr(up, "close_code", None))
            LAST_ERR["live"] = str(data)[:110].replace("\n", " ") or ("close %s" % getattr(up, "close_code", ""))
            if "API key" in str(data) or "PERMISSION" in str(data) or "UNAUTH" in str(data):
                key_bad[k] = time.time()
        except Exception as e:
            print("LIVE ulanish xato:", type(e).__name__, str(e)[:160])
            LAST_ERR["live"] = "%s %s" % (type(e).__name__, str(e)[:90])
        if up is not None and not up.closed:
            await up.close()
    return None


async def ai_live(request):
    ws = web.WebSocketResponse(max_msg_size=1 << 20, heartbeat=25)
    await ws.prepare(request)

    async def j(o):
        try:
            await ws.send_str(json.dumps(o, ensure_ascii=False))
        except Exception:
            pass

    info = verify(request.query.get("t"))
    if not info:
        await j({"t": "error", "error": "Havola eskirgan. Botdan qayta oching."}); await ws.close(); return ws
    uid = int(info["u"])
    left0 = left_for(uid)
    if left0 <= 0:
        await j({"t": "done", "cta": cta_text(), "bot": BOT_URL}); await ws.close(); return ws
    if not (LIVE_ON and keys("gemini")):
        await j({"t": "nolive"}); await ws.close(); return ws
    ses = get_session(info)
    persona = set_persona(info, ses, request.query.get("p"), request.query.get("g"))
    load_gender(info, ses, request.query.get("g"))
    dinfo = await day_info(info.get("d"), lg(info))
    humor = humor_level()
    ex = G.sample({0: 2, 1: 3, 2: 4, 3: 5}[humor], humor)
    sysmsg, _nxt = build_system(info, ses, dinfo, ex, left0, False, live=True)
    t0 = time.time()
    turns = []        # [("u"|"a", matn)]
    async with aiohttp.ClientSession() as http:
        opened = await live_open(http, sysmsg, persona_voice(ses.get("sex"), persona))
        if not opened:
            await j({"t": "nolive", "why": "; ".join("%s %s" % kv for kv in LAST_ERR.items())[:200]}); await ws.close(); return ws
        up, _k = opened
        again = bool(ses["hist"])
        opener = opener_for(persona, again, ses)
        await up.send_str(json.dumps({"realtimeInput": {"text": opener}}))
        await j({"t": "ready", "left": left0, "ptt": LIVE_PTT, "g": ses.get("g") or ""})
        state = {"wrap": False, "cur_in": "", "cur_out": "", "talking": False, "talk_t0": 0.0, "user_s": 0.0, "ai_s": 0.0, "last": time.time()}

        def used_secs():
            """Hisobga olinadigan vaqt: odam gapirgan + Rumiyo gapirgan soniyalar. Jimlik va o'ylash hisobga kirmaydi."""
            if not LIVE_PTT:
                return time.time() - t0
            u = state["user_s"] + ((time.time() - state["talk_t0"]) if state["talking"] else 0)
            return u + state["ai_s"]

        async def flush_turn():
            if state["cur_in"].strip():
                turns.append(("u", state["cur_in"].strip()))
            if state["cur_out"].strip():
                turns.append(("a", state["cur_out"].strip()))
            state["cur_in"] = state["cur_out"] = ""

        async def c2u():
            async for m in ws:
                if m.type == aiohttp.WSMsgType.BINARY:
                    if LIVE_PTT and not state["talking"]:
                        continue          # tugma bosilmagan: ovoz yuborilmaydi (shovqin va eko ichkariga o'tmaydi)
                    if LIVE_PTT:
                        state["last"] = time.time()
                    await up.send_str(json.dumps({"realtimeInput": {"audio": {"data": base64.b64encode(m.data).decode(), "mimeType": "audio/pcm;rate=16000"}}}))
                elif m.type == aiohttp.WSMsgType.TEXT:
                    try:
                        o = json.loads(m.data)
                    except ValueError:
                        continue
                    if o.get("t") == "end":
                        return
                    if o.get("t") == "ptt" and LIVE_PTT:
                        state["last"] = time.time()
                        if o.get("on") and not state["talking"]:
                            if state["cur_out"].strip() or state["cur_in"].strip():
                                await flush_turn()
                            state["talking"], state["talk_t0"] = True, time.time()
                            await up.send_str(json.dumps({"realtimeInput": {"activityStart": {}}}))
                        elif not o.get("on") and state["talking"]:
                            state["user_s"] += min(40.0, time.time() - state["talk_t0"])
                            state["talking"] = False
                            await up.send_str(json.dumps({"realtimeInput": {"activityEnd": {}}}))
                elif m.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    return

        async def u2c():
            async for m in up:
                if m.type not in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
                    if m.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                        print("LIVE yopildi:", up.close_code, str(m.data)[:200])
                        LAST_ERR["live"] = "close %s %s" % (up.close_code, str(m.data)[:80])
                        return
                    continue
                raw = m.data.decode("utf-8", "ignore") if isinstance(m.data, (bytes, bytearray)) else m.data
                try:
                    o = json.loads(raw)
                except ValueError:
                    continue
                sc = o.get("serverContent") or {}
                for part in ((sc.get("modelTurn") or {}).get("parts") or []):
                    inl = part.get("inlineData") or part.get("inline_data")
                    if inl and inl.get("data"):
                        pcm = base64.b64decode(inl["data"])
                        state["ai_s"] += len(pcm) / 48000.0        # 24 kHz, 16 bit, mono
                        state["last"] = time.time()
                        await ws.send_bytes(pcm)
                it = (sc.get("inputTranscription") or {}).get("text")
                if it:
                    if state["cur_out"].strip():
                        await flush_turn()
                    state["cur_in"] += it
                    state["last"] = time.time()
                    await j({"t": "in", "text": it})
                    if not ses.get("g"):
                        gg = learn_gender(info, ses, state["cur_in"])
                        if gg:
                            await j({"t": "gender", "g": gg})
                ot = (sc.get("outputTranscription") or {}).get("text")
                if ot:
                    state["cur_out"] += ot
                    await j({"t": "out", "text": ot})
                if sc.get("interrupted"):
                    await j({"t": "int"})
                if sc.get("turnComplete"):
                    await flush_turn()
                    await j({"t": "turn"})
                if o.get("error") or o.get("goAway"):
                    print("LIVE xabar:", str(o)[:200])

        async def clock():
            last = -99
            while True:
                await asyncio.sleep(1)
                rem = left0 - int(used_secs())
                if rem <= 0:
                    return
                if not state["talking"] and time.time() - state["last"] > LIVE_IDLE:
                    return                      # uzoq jimlik: qo'ng'iroq yopiladi
                if time.time() - t0 > max(900, left0 * 6):
                    return                      # mutlaq yuqori chegara
                if rem <= 50 and not state["wrap"]:
                    state["wrap"] = True
                    await up.send_str(json.dumps({"realtimeInput": {"text": "(VAQT TUGAYAPTI: shu gapingdan keyin xayrlash: hazil bilan xayrlash va botga qaytib o'qishni davom ettirishni ayt. Yangi savol berma.)"}}))
                if rem // 5 != last:
                    last = rem // 5
                    await j({"t": "left", "left": rem})

        tasks = [asyncio.create_task(x()) for x in (c2u, u2c, clock)]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            await flush_turn()
            elapsed = int(used_secs())
            try:
                await up.close()
            except Exception:
                pass
    add_use(uid, max(1, elapsed))
    for who, txt in turns:
        ses["hist"].append({"role": "user" if who == "u" else "assistant", "content": txt if who == "u" else f"UZ: {txt}\nRU: "})
    ses["hist"] = ses["hist"][-mem_msgs():]
    ses["turns"] += len(turns) // 2
    asyncio.create_task(update_notes(uid, list(ses["hist"])))
    rem = left_for(uid)
    await j({"t": "done", "cta": cta_text(), "bot": BOT_URL, "left": rem})
    try:
        await ws.close()
    except Exception:
        pass
    return ws


# ---------- endpoints ----------
async def ai_start(request):
    try:
        info = verify((await request.json()).get("token"))
    except Exception:
        info = None
    if not info:
        return web.json_response({"error": "Havola eskirgan. Botdan qayta oching."}, status=403)
    uid = int(info["u"])
    left = left_for(uid)
    dinfo = await day_info(info.get("d"), lg(info))
    topic = (dinfo or {}).get("t") or info.get("t") or ""
    if left <= 0:
        return web.json_response({"done": True, "left": 0, "ru": "", "cta": cta_text(), "bot": BOT_URL, "topic": topic})
    if not keys("groq") and not keys("openrouter") and not keys("gemini"):
        return web.json_response({"error": "Rumiyo hozir vaqtincha ishlamayapti."}, status=503)
    ses = get_session(info)
    try:
        _body = await request.json()
    except Exception:
        _body = {}
    set_persona(info, ses, _body.get("persona"), _body.get("g"))
    load_gender(info, ses, _body.get("g"))
    want_live = bool(_body.get("live"))
    if want_live and LIVE_ON and keys("gemini"):      # jonli rejim: salomlashuvni Gemini Live o'zi aytadi
        return web.json_response({"live": True, "left": left, "topic": topic, "bot": BOT_URL})
    again = bool(ses["hist"])
    opener = opener_for(ses.get("persona", "friendly"), again, ses)
    try:
        async with gate:
            ru, uz, _fix, _w, emo = await reply_for(info, ses, dinfo, opener, left)
            audio = b""
    except Exception as e:
        print("start:", type(e).__name__, str(e)[:160])
        ru, uz, audio, emo = (("Hello! I'm Rumiyo. Let's talk about today's lesson!" if lg(info) == "en" else "Привет! Я Румиё. Давай поболтаем про сегодняшний урок!"),
                              "Salom! Men Rumiyoman. Keling, bugungi dars haqida gaplashamiz!", b"", "normal")
        ses["hist"].append({"role": "assistant", "content": f"UZ: {uz}\nRU: {ru}"})
    return web.json_response({"ru": ru, "uz": uz, "emo": emo, "audio": base64.b64encode(audio).decode(), "left": left,
                              "topic": topic, "returning": again, "bot": BOT_URL})


async def ai_tts(request):
    """Matn tayyor bo'lgach, ovoz bo'laklab (parallel) so'raladi: matn darhol ko'rinadi, ovoz birinchi bo'lak tayyor bo'lishi bilan boshlanadi."""
    try:
        j = await request.json()
    except Exception:
        return web.json_response({"audio": ""})
    tinfo = verify(j.get("token"))
    if not tinfo:
        return web.json_response({"error": "Havola eskirgan."}, status=403)
    text = (j.get("text") or "")[:400].strip()
    if not text:
        return web.json_response({"audio": ""})
    voice = (EN_VOICE if lg(tinfo) == "en" else RU_VOICE) if j.get("lang") == "ru" else UZ_VOICE
    emo = j.get("emo") if j.get("emo") in PROSODY else "normal"
    a = await tts_safe(text, emo, voice)
    return web.json_response({"audio": base64.b64encode(a).decode()})


async def ai_turn(request):
    info = verify(request.headers.get("X-Token"))
    if not info:
        return web.json_response({"error": "Havola eskirgan. Botdan qayta oching."}, status=403)
    uid = int(info["u"])
    left = left_for(uid)
    if left <= 0:
        return web.json_response({"done": True, "left": 0, "cta": cta_text(), "bot": BOT_URL})
    raw = await request.read()
    if not raw or len(raw) < 800:
        return web.json_response({"error": "Ovoz juda qisqa. Qayta urinib ko'ring."}, status=400)
    try:
        dur = max(1, min(30, int(float(request.headers.get("X-Dur", "5")))))
    except ValueError:
        dur = 5
    ctype = request.headers.get("Content-Type", "")
    ses = get_session(info)
    set_persona(info, ses, request.headers.get("X-Persona"), request.headers.get("X-Gender"))
    load_gender(info, ses, request.headers.get("X-Gender"))
    dinfo = await day_info(info.get("d"), lg(info))
    t0 = time.time()
    resp = web.StreamResponse(headers={"Content-Type": "application/x-ndjson; charset=utf-8", "Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    await resp.prepare(request)

    async def emit(o):
        await resp.write((json.dumps(o, ensure_ascii=False) + "\n").encode("utf-8"))

    first = [None]

    async def emit_t(o):
        if o.get("t") == "uz" and first[0] is None:
            first[0] = time.time() - t0
        await emit(o)

    busy_msg = "Rumiyo hozir band. Bir oz kutib, qayta urinib ko'ring. [sabab: " + "%s" + "]"
    res = None
    mode = "?"
    try:
        async with gate:
            if keys("gemini") and os.getenv("DIRECT_AUDIO", "1") != "0":
                try:      # ovoz to'g'ridan-to'g'ri Gemini'ga: matnga o'tkazish bosqichi yo'q
                    res = await reply_stream(info, ses, dinfo, "", left, emit_t, audio=(raw, ctype))
                    mode = "to'g'ridan-to'g'ri"
                except Busy:
                    res = None
            if res is None:
                mode = "eshitish + AI"
                heard, foreign = await stt(raw, ctype, lg(info))
                if not heard:
                    await emit({"t": "error", "error": "Ovozni eshita olmadim. Aniqroq gapiring."})
                    await resp.write_eof()
                    return resp
                await emit({"t": "heard", "heard": heard})
                said = heard
                if foreign:
                    said = ("(o'quvchining nutqi na o'zbekcha, na %s eshitildi; eshitilgani: «%s». Jahl bilan «o'z tilingni unutdingmi?» deb koy va "
                            "o'zbekcha yoki %s gapirishni talab qil.)" % (("inglizcha", heard[:60], "inglizcha") if lg(info) == "en" else ("ruscha", heard[:60], "ruscha")))
                res = await reply_stream(info, ses, dinfo, said, left, emit_t)
    except Busy:
        await emit({"t": "error", "error": busy_msg % ("; ".join("%s %s" % kv for kv in LAST_ERR.items()) or "noma'lum")})
        await resp.write_eof()
        return resp
    except Exception as e:
        print("turn xato:", type(e).__name__, str(e)[:200])
        await emit({"t": "error", "error": "Xatolik bo'ldi, vaqtingizdan hisoblanmadi. Qayta urinib ko'ring."})
        await resp.write_eof()
        return resp
    ru, uz, fix, wrap, emo, heard = res
    add_use(uid, dur + min(20, (len(uz) + len(ru)) // 14 + 2))
    left = left_for(uid)
    done = left <= 0 or wrap
    if done:
        asyncio.create_task(update_notes(uid, list(ses["hist"])))
    print("turn [%s]: birinchi gap %.1fs, jami %.1fs" % (mode, first[0] or -1, time.time() - t0))
    out = {"t": "final", "heard": heard, "ru": ru, "uz": uz, "fix": fix, "emo": emo, "left": left, "done": done}
    if done:
        out.update({"cta": cta_text(), "bot": BOT_URL})
    await emit(out)
    await resp.write_eof()
    return resp


async def ai_me(request):
    """Sahifa ochilganda: tanlangan ustoz, o'quvchi jinsi va o'zgartirish qulfi qancha qolgani."""
    info = verify(request.query.get("t", ""))
    if not info:
        return web.json_response({"ok": False}, status=401)
    uid = int(info["u"])
    a, c, left = resolve_pick(uid)
    return web.json_response({"ok": True, "avatar": a or "", "char": c or "", "g": get_gender(uid) or "", "lock_left": left})


async def ai_end(request):
    try:
        info = verify((await request.json()).get("token"))
    except Exception:
        info = None
    if info:
        ses = SES.get(int(info["u"]))
        if ses:
            asyncio.create_task(update_notes(int(info["u"]), list(ses["hist"])))
    return web.json_response({"ok": True})


# ---------- admin (bot chaqiradi; X-Secret = TALK_SECRET) ----------
async def jbody(request):
    try:
        return await request.json()
    except Exception:
        return {}


def denied():
    return web.json_response({"error": "ruxsat yo'q"}, status=403)


async def admin_add(request):
    if not is_admin(request):
        return denied()
    d = await jbody(request)
    new = G.add(d.get("q"), d.get("a"), d.get("tone", 1))
    return web.json_response({"ok": True, "new": new, "count": G.count()})


async def admin_sync(request):
    if not is_admin(request):
        return denied()
    d = await jbody(request)
    pairs = d.get("pairs")
    if not isinstance(pairs, list):
        return web.json_response({"error": "pairs yo'q"}, status=400)
    a, u, g = G.sync(pairs, d.get("settings") if isinstance(d.get("settings"), dict) else None)
    return web.json_response({"ok": True, "added": a, "updated": u, "removed": g, "count": G.count()})


async def admin_count(request):
    if not is_admin(request):
        return denied()
    return web.json_response({"count": G.count()})


async def admin_export(request):
    if not is_admin(request):
        return denied()
    return web.json_response({"pairs": G.export()})


async def admin_keys(request):
    if not is_admin(request):
        return denied()
    d = await jbody(request)
    try:
        with open(KEYS_FILE, encoding="utf-8") as f:
            cur = json.load(f)
    except (OSError, ValueError):
        cur = {}
    for kind in ("groq", "openrouter", "gemini"):
        v = d.get(kind)
        if isinstance(v, str):
            v = [v]
        if isinstance(v, list):
            cur[kind] = [k.strip() for k in v if isinstance(k, str) and k.strip()]
    with open(KEYS_FILE, "w", encoding="utf-8") as f:
        json.dump(cur, f)
    return web.json_response({"ok": True, **{k: len(keys(k)) for k in ("groq", "openrouter", "gemini")}})


async def admin_stats(request):
    if not is_admin(request):
        return denied()
    n, secs = db.execute("SELECT COUNT(*), COALESCE(SUM(secs),0) FROM use WHERE day=?", (today(),)).fetchone()
    return web.json_response({"pairs": G.count(), "today_users": n, "today_secs": secs, "sessions": len(SES),
                              "keys": {k: len(keys(k)) for k in ("groq", "openrouter", "gemini")}})


async def admin_selftest(request):
    """Bot «Tekshirish» tugmasi: har bir xizmatga kichik so'rov yuborib, javob kodini qaytaradi (kalitlar yozilmaydi)."""
    if not is_admin(request):
        return denied()
    out = []
    msgs = [{"role": "user", "content": "Say: ok"}]
    async with aiohttp.ClientSession() as http:
        for k in keys("groq")[:3]:
            for m in LLM_MODELS:
                try:
                    st, j = await post(http, GROQ + "/chat/completions", {"Authorization": "Bearer " + k}, json={"model": m, "messages": msgs, "max_tokens": 5})
                    out.append("Groq …%s %s: %s %s" % (k[-4:], m, st, "OK" if st == 200 else str(j)[:160]))
                except Exception as e:
                    out.append("Groq …%s %s: %s %s" % (k[-4:], m, type(e).__name__, str(e)[:100]))
        if keys("groq"):
            import io, wave
            bio = io.BytesIO(); w = wave.open(bio, "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\x00\x00" * 16000); w.close()
            for m in STT_MODELS:
                try:
                    form = aiohttp.FormData(); form.add_field("file", bio.getvalue(), filename="a.wav", content_type="audio/wav"); form.add_field("model", m)
                    st, j = await post(http, GROQ + "/audio/transcriptions", {"Authorization": "Bearer " + keys("groq")[0]}, data=form)
                    out.append("Groq ovoz→matn %s: %s %s" % (m, st, "OK" if st == 200 else str(j)[:160]))
                except Exception as e:
                    out.append("Groq ovoz→matn %s: %s %s" % (m, type(e).__name__, str(e)[:100]))
        for k in keys("openrouter")[:2]:
            try:
                st, j = await post(http, OPENROUTER + "/chat/completions", {"Authorization": "Bearer " + k}, json={"model": OR_MODEL, "messages": msgs, "max_tokens": 5})
                out.append("OpenRouter …%s: %s %s" % (k[-4:], st, "OK" if st == 200 else str(j)[:160]))
            except Exception as e:
                out.append("OpenRouter: %s" % type(e).__name__)
        for k in keys("gemini")[:2]:
            try:
                st, j = await post(http, f"{GEMINI}/models/{GEMINI_MODEL}:generateContent?key={k}", json={"contents": [{"parts": [{"text": "Say ok"}]}]})
                out.append("Gemini …%s: %s %s" % (k[-4:], st, "OK" if st == 200 else str(j)[:160]))
            except Exception as e:
                out.append("Gemini: %s" % type(e).__name__)
    if not (keys("groq") or keys("openrouter") or keys("gemini")):
        out.append("Serverda HECH QANDAY kalit yo'q (bot hali yubormagan)")
    try:
        a = await tts("Привет")
        b = await tts("Salom", "normal", UZ_VOICE)
        out.append("Ovoz (TTS): rus OK %d bayt, o'zbek OK %d bayt" % (len(a), len(b)))
    except Exception as e:
        out.append("Ovoz (TTS): XATO %s %s" % (type(e).__name__, str(e)[:100]))
    return web.json_response({"lines": out})


PAGE = next((os.path.join(HERE, f) for f in ("rumiyo.html", "index.html") if os.path.exists(os.path.join(HERE, f))), os.path.join(HERE, "index.html"))


async def page(request):
    return web.FileResponse(PAGE, headers={"Cache-Control": "no-cache"})


def add_routes(app, prefix=""):
    """Rumiyo yo'llarini ilovaga qo'shadi. Suhbat serveriga ulanganda prefix='/rumiyo'."""
    p = prefix
    app.router.add_post(p + "/ai/start", ai_start)
    app.router.add_get(p + "/ai/live", ai_live)
    app.router.add_post(p + "/ai/turn", ai_turn)
    app.router.add_post(p + "/ai/end", ai_end)
    app.router.add_get(p + "/ai/me", ai_me)
    @web.middleware
    async def _cache_mw(request, handler):
        resp = await handler(request)
        pth = request.path
        if ("/vrm/" in pth or "/media/" in pth) and resp.status == 200 and "Cache-Control" not in resp.headers:
            resp.headers["Cache-Control"] = "public, max-age=604800, immutable" if request.query_string else "public, max-age=3600"
        return resp
    app.middlewares.append(_cache_mw)
    app.router.add_post(p + "/ai/tts", ai_tts)
    app.router.add_post(p + "/admin/add", admin_add)
    app.router.add_post(p + "/admin/sync", admin_sync)
    app.router.add_get(p + "/admin/count", admin_count)
    app.router.add_get(p + "/admin/export", admin_export)
    app.router.add_post(p + "/admin/keys", admin_keys)
    app.router.add_get(p + "/admin/stats", admin_stats)
    app.router.add_get(p + "/admin/selftest", admin_selftest)
    app.router.add_get(p + "/", page)
    media = os.path.join(HERE, "media")
    if os.path.isdir(media):
        app.router.add_static(p + "/media", media)       # avatar videolari: idle, speak, angry, angry_start, cry, laugh
    vrmdir = os.path.join(HERE, "vrm")
    if os.path.isdir(vrmdir):
        app.router.add_static(p + "/vrm", vrmdir)         # 3D avatar: rumiyo-vrm.js va rumiyo.vrm
    if p:
        async def to_slash(request):
            raise web.HTTPFound(p + "/" + ("?" + request.query_string if request.query_string else ""))
        app.router.add_get(p, to_slash)


def make_app():
    app = web.Application(client_max_size=4 * 1024 * 1024)
    add_routes(app)
    app.router.add_get("/health", lambda r: web.Response(text="ok"))
    return app


async def main():
    runner = web.AppRunner(make_app())
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    print(f"Rumiyo sayti ishlayapti: port {PORT}, juftliklar: {G.count()}")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
