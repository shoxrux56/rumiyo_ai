#!/usr/bin/env python3
"""Tayyor ovozli gaplarni Gemini TTS orqali yaratadi (kechikishni yo'qotish uchun).

Ishlatish (server yoki kompyuterda, bir marta):
    pip install google-genai
    export GEMINI_API_KEY=...        # kalitni faqat shu yerda, chatga yozmang
    python3 tools/make_clips.py

Natija: clips/*.mp3 va clips/manifest.json (sayt shuni o'zi ishlatadi). Faqat yangi gaplar yaratiladi.
Qayta yaratish uchun clips/ ichidagi faylni o'chiring yoki:  python3 tools/make_clips.py --force
"""
import json, os, subprocess, sys, time, wave

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CL = os.path.join(HERE, "clips")
MODEL = os.getenv("TTS_MODEL", "gemini-2.5-flash-preview-tts")
# server.py dagi ovozlar bilan bir xil (jonli suhbat bilan mos tushsin)
VOICES = {("m", "fiery"): "Enceladus", ("f", "fiery"): "Aoede", ("m", "sweet"): "Achird", ("f", "sweet"): "Sulafat"}
for k, v in list(VOICES.items()):
    VOICES[k] = os.getenv("LIVE_VOICE_%s_%s" % (k[0].upper(), k[1].upper()), v)


def synth(client, types, text, voice):
    r = client.models.generate_content(
        model=MODEL, contents=text,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)))))
    return r.candidates[0].content.parts[0].inline_data.data       # xom PCM, 24 kHz, 16 bit, mono


def save(pcm, path_wav):
    with wave.open(path_wav, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(pcm)


def main():
    force = "--force" in sys.argv
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY topilmadi (export GEMINI_API_KEY=... qiling)")
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=key)
    os.makedirs(CL, exist_ok=True)
    data = json.load(open(os.path.join(HERE, "clips_phrases.json"), encoding="utf-8"))
    mpath = os.path.join(CL, "manifest.json")
    man = json.load(open(mpath, encoding="utf-8")) if os.path.exists(mpath) else {}
    for char, lines in data["openers"].items():
        style = data["styles"].get(char, "")
        for sex in ("f", "m"):
            bucket = man.setdefault("%s.%s" % (char, sex), [])
            have = {b["text"]: b for b in bucket}
            fresh = []
            for n, text in enumerate(lines):
                b = have.get(text)
                if b and os.path.exists(os.path.join(CL, b["file"])) and not force:
                    fresh.append(b); continue
                name = "open_%s_%s_%d.mp3" % (char, sex, n)
                try:
                    pcm = synth(client, types, style + '"' + text + '"', VOICES[(sex, char)])
                except Exception as e:
                    print("XATO", name, str(e)[:160]); time.sleep(2); continue
                wav = os.path.join(CL, "_t.wav"); save(pcm, wav)
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav, "-ac", "1", "-b:a", "64k", os.path.join(CL, name)], check=True)
                os.remove(wav)
                fresh.append({"file": name, "text": text})
                print("tayyor:", name); time.sleep(1.2)       # tezlik chegarasiga urilmaslik uchun
            man["%s.%s" % (char, sex)] = fresh
    json.dump(man, open(mpath, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("Tayyor! clips/manifest.json yangilandi. Saytni qayta deploy qiling (clips/ papkasi bilan).")


if __name__ == "__main__":
    main()
