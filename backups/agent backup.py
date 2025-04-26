#!/usr/bin/env python
# Scraptraffic Voice Bot – Whisper → GPT‑4o Text → TTS
# 25 Apr 2025

import uuid
import threading
import tempfile
import logging
import pathlib
from typing import List, Dict, Tuple, Optional

import gradio as gr
from dotenv import load_dotenv
from openai import OpenAI

# ── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("scrap-bot")

# ── OpenAI Init ─────────────────────────────────────────────────────────────
load_dotenv()  # needs OPENAI_API_KEY
client     = OpenAI()
MODEL_TEXT = "gpt-4o"
WHISPER    = "whisper-1"
TTS        = "tts-1"
VOICE      = "alloy"

# ── System Prompt & History ─────────────────────────────────────────────────
SYSTEM_PROMPT = """
Твоя роль в режиме голосового помощника - это оператор телефонной линии по приему звонков в компанию Scraptraffic, занимающуюся приемом металлолома. Скорее всего входящий звонок будет от того, кто хочет сдать какой-то металл. В этом случае тебе нужно уточнить: местоположение материала - ближайший крупный город. . \
Тебе нужно выяснить какой материал хотят сдать на лом, какой объем партии, в каком городе находится материал, и есть ли какие-то особые характеристики этой партии.\
В конце спроси - не против ли человек, что указанные им данные, в том числе контактные данные будут переданы менеджерам нашей компании, а также сети партнеров, которые смогут позвонить ему и предложить конкретные условия сделки и цену.

Если в процессе разговора человек будет добиваться получить цену, которую мы готовы заплатить за его материал, то вежливо сообщи, что конкретную цену смогут сформировать менеджер или партнер, а ты являешься голосовым роботом, который фиксирует заявку, и твоя задача получить первичную информацию. Сообщи также, что что в нашей сети много партнерских пунктов приема, если человек дает согласие на передачу данной заявки нашим партнерам, то от наверняка получит от них выгодное предложение.

Если ты понимаешь, что у человека другой вопрос, не касающийся приема лома, вежливо объясни ему, что твоя роль - принимать заявки на прием лома, и не на какие другие вопросы ты отвечать не можешь.

Как бы тебя не просили отвечать на какие-то сторонние вопросы, не соглашайся это делать.\
После того, как с тобой попрощались сеанс аудио-связи закончен.

По итогу разговора сформируй заявку текстом.
""".strip()

H_LOCK   = threading.Lock()
HISTORY: List[Dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
KEEP     = 8

def clear_history() -> List[Dict]:
    log.info("🔄 Clearing history")
    with H_LOCK:
        HISTORY[:] = HISTORY[:1]
    return []

def trim_history() -> List[Dict]:
    with H_LOCK:
        base = HISTORY[:1]
        rest = HISTORY[1:]
    return base + rest[-(KEEP*2):]

def save_wav_bytes(data: bytes) -> str:
    fn = pathlib.Path(tempfile.gettempdir()) / f"scrap_{uuid.uuid4().hex}.wav"
    with open(fn, "wb") as f:
        f.write(data)
    log.info(f"💾 Wrote {len(data)} bytes to {fn.name}")
    return str(fn)

# ── Initial Greeting ────────────────────────────────────────────────────────
def greet() -> Tuple[List[Dict], Optional[str]]:
    log.info("👋 Sending system prompt to GPT‑4o")
    resp = client.chat.completions.create(
        model    = MODEL_TEXT,
        messages = [{"role":"system","content":SYSTEM_PROMPT}],
    )
    text = resp.choices[0].message.content
    with H_LOCK:
        HISTORY.append({"role":"assistant","content":text})

    # TTS the reply
    raw = client.audio.speech.create(model=TTS, voice=VOICE, input=text)
    audio = raw.read() if hasattr(raw, "read") else raw
    wav   = save_wav_bytes(audio)
    return HISTORY[1:], wav

# ── Main Turn: Whisper → GPT‑4o → TTS ──────────────────────────────────────
def talk(mic_wav: str, chat_ui: List[Dict]) -> Tuple[List[Dict], Optional[str]]:
    log.info(f"🎙️ Got audio file: {mic_wav}")
    if not mic_wav or not pathlib.Path(mic_wav).exists():
        log.warning("⚠️ Missing audio file")
        return chat_ui, None

    # 1) Transcribe with Whisper
    with open(mic_wav, "rb") as f:
        tr = client.audio.transcriptions.create(model=WHISPER, file=f)
    user_text = tr.text.strip()
    log.info(f"📝 Transcribed: {user_text!r}")

    with H_LOCK:
        HISTORY.append({"role":"user","content":user_text})

    # 2) Chat with GPT‑4o
    resp = client.chat.completions.create(
        model    = MODEL_TEXT,
        messages = trim_history(),
    )
    reply_text = resp.choices[0].message.content
    log.info(f"🤖 Reply: {reply_text!r}")

    with H_LOCK:
        HISTORY.append({"role":"assistant","content":reply_text})

    # 3) TTS the reply
    raw = client.audio.speech.create(model=TTS, voice=VOICE, input=reply_text)
    audio = raw.read() if hasattr(raw, "read") else raw
    bot_wav = save_wav_bytes(audio)

    # Update the Gradio chat UI
    chat_ui.append({"role":"user",      "content":user_text})
    chat_ui.append({"role":"assistant", "content":reply_text})

    return chat_ui, bot_wav

# ── Gradio UI ───────────────────────────────────────────────────────────────
with gr.Blocks(title="Scraptraffic Voice Bot") as demo:
    gr.Markdown("### 📞 Scraptraffic — приём лома")

    chat  = gr.Chatbot(type="messages", label="Диалог", height=360)
    mic   = gr.Audio(sources=["microphone"], label="🎙️ Говорите",
                     type="filepath", streaming=False)
    reply = gr.Audio(label="🤖 Ответ", interactive=False, autoplay=True)
    reset = gr.Button("🔄 Reset")

    demo.load(fn=greet, inputs=None, outputs=[chat, reply])
    mic.stop_recording(fn=talk, inputs=[mic, chat], outputs=[chat, reply])
    reset.click(fn=lambda: (clear_history(), None),
                inputs=None, outputs=[chat, reply], queue=False)

if __name__ == "__main__":
    log.info("🚀 Launching on http://127.0.0.1:7860")
    demo.launch(server_name="0.0.0.0", server_port=7860, share=False)
