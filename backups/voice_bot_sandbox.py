#!/usr/bin/env python
# Scraptraffic Voice Bot – GPT‑4o Audio Preview (Gradio 4)   24 Apr 2025

from __future__ import annotations
import os, base64, tempfile, threading, logging
from typing import List, Dict

import gradio as gr
from dotenv import load_dotenv
from openai import OpenAI

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s",
                    level=logging.INFO)
log = logging.getLogger("voice‑bot")

load_dotenv()
client = OpenAI()                    # needs OPENAI_API_KEY
MODEL  = "gpt-4o-audio-preview"

SYSTEM_PROMPT = """
Ты — оператор Scraptraffic. Спроси город, металл и объём; уточни особенности;
получи согласие; если цену — перенаправь; посторонние вопросы — извинись;
после «до свидания» завершай. В конце сформируй текстовую заявку.
""".strip()

# ── util: safe audio extraction ────────────────────────────────────────────
def maybe_b64_to_wav(msg) -> str:
    """Return temp‑file path or "" if model produced no audio."""
    if msg and getattr(msg, "audio", None) and msg.audio.data:
        data = base64.b64decode(msg.audio.data)
        tmp  = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        tmp.write(data); tmp.flush()
        return tmp.name
    return ""

# ── conversation memory (thread‑safe) ──────────────────────────────────────
h_lock = threading.Lock()
history: List[Dict] = []

def reset() -> List[Dict]:
    with h_lock:
        history.clear()
        history.append({"role": "system", "content": SYSTEM_PROMPT})
    log.info("history reset")
    return []

reset()

# ── mode switch (adds greeting) ────────────────────────────────────────────
def switch_mode(mode: str):
    reset()
    if mode != "Conversational":
        return [], ""
    greet = "Здравствуйте! Вы позвонили в Scraptraffic."
    with h_lock:
        history.append({"role": "assistant", "content": greet})

    rsp  = client.chat.completions.create(
        model      = MODEL,
        modalities = ["text", "audio"],
        audio      = {"voice": "alloy", "format": "wav"},
        messages   = history,
    )
    text = rsp.choices[0].message.content or "(speech‑only)"
    wav  = maybe_b64_to_wav(rsp.choices[0].message)     # ← safe
    with h_lock:
        history[-1]["content"] = text
    visible = [{"role": m["role"], "content": m["content"]}
               for m in history if m["role"] != "system"]
    return visible, wav

# ── main turn ──────────────────────────────────────────────────────────────
def run_turn(wav_path, mode, chat_state):
    if not wav_path:
        return chat_state, ""

    with h_lock:
        history.append({"role": "user", "content": "", "audio": wav_path})
        msgs = history.copy()

    try:
        rsp = client.chat.completions.create(
            model      = MODEL,
            modalities = ["text", "audio"],
            audio      = {"voice": "alloy", "format": "wav"},
            messages   = msgs,
        )
        bot_text = rsp.choices[0].message.content or "(speech‑only)"
        bot_wav  = maybe_b64_to_wav(rsp.choices[0].message)  # ← safe
    except Exception as exc:
        log.exception("OpenAI error")
        bot_text, bot_wav = f"⚠️ {exc}", ""

    with h_lock:
        history.append({"role": "assistant", "content": bot_text})
        chat_state.append({"role": "user",      "content": "(аудио)"})
        chat_state.append({"role": "assistant", "content": bot_text})

    if mode == "File Mode":
        bot_wav = ""
    return chat_state, bot_wav

# ── Gradio UI ──────────────────────────────────────────────────────────────
CSS = ".gradio-container {font-family: ui-sans-serif, system-ui, sans-serif}"

with gr.Blocks(title="Scraptraffic Voice Bot", css=CSS) as demo:
    gr.Markdown("### 📞 Scraptraffic Voice Bot (GPT‑4o Audio Preview)")
    mode    = gr.Radio(["File Mode", "Conversational"],
                       value="File Mode", label="Режим")
    chatbox = gr.Chatbot(type="messages", label="Диалог", height=380)
    mic     = gr.Audio(sources=["microphone"], type="filepath",
                       label="🎙️ Hold‑to‑talk")
    answer  = gr.Audio(label="🤖 Ответ", interactive=False, autoplay=True)
    reset_b = gr.Button("🔄 Reset")

    mode.change(switch_mode, mode, [chatbox, answer], queue=False)
    mic.stop_recording(run_turn,
                       inputs=[mic, mode, chatbox],
                       outputs=[chatbox, answer])
    reset_b.click(lambda: (reset(), ""), None, [chatbox, answer], queue=False)

if __name__ == "__main__":
    log.info("open  http://127.0.0.1:7860  in your browser")
    demo.launch(server_name="0.0.0.0", server_port=7860)







#!/usr/bin/env python
# Scraptraffic Voice Bot – GPT‑4o Audio Preview (file‑mode only)
# 25 Apr 2025

import base64
import tempfile
import uuid
import threading
import logging
import pathlib
from typing import List, Dict, Tuple, Optional

import gradio as gr
from dotenv import load_dotenv
from openai import OpenAI

# ── logging ───────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("scrap-bot")

# ── OpenAI init ───────────────────────────────────────────────────────────
load_dotenv()
client      = OpenAI()
MODEL_AUDIO = "gpt-4o-audio-preview"
MODEL_TEXT  = "gpt-4o"
VOICE       = "alloy"
WHISPER     = "whisper-1"
TTS         = "tts-1"

# ── system prompt & history ───────────────────────────────────────────────
SYSTEM_PROMPT = """
Ты — оператор Scraptraffic. Когда пользователь закончит говорить, уточни:
• город • металл • объём • особенности.
Если спрашивают цену — передай менеджеру. Посторонние темы — извинись.
После «до свидания» завершай. В конце сформируй текстовую заявку.
""".strip()

H_LOCK  = threading.Lock()
HISTORY: List[Dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
KEEP    = 8  # keep last N user+assistant pairs

def clear_history() -> List[Dict]:
    log.info("Resetting conversation history")
    with H_LOCK:
        HISTORY[:] = HISTORY[:1]
    return []

def trim_history() -> List[Dict]:
    with H_LOCK:
        base = HISTORY[:1]
        tail = HISTORY[1:]
    return base + tail[-(KEEP*2):]

def save_temp_wav(b64: str) -> str:
    data = base64.b64decode(b64)
    fn = pathlib.Path(tempfile.gettempdir()) / f"scrap_{uuid.uuid4().hex}.wav"
    with open(fn, "wb") as f:
        f.write(data)
    log.info(f"WAV written to {fn} ({len(data)} bytes)")
    return str(fn)

def greet() -> Tuple[List[Dict], Optional[str]]:
    log.info("Greeting user…")
    resp = client.chat.completions.create(
        model      = MODEL_AUDIO,
        modalities = ["text", "audio"],
        audio      = {"voice": VOICE, "format": "wav"},
        messages   = [{"role":"system", "content":SYSTEM_PROMPT}],
    )
    msg = resp.choices[0].message
    text = msg.content or "(speech-only)"
    b64  = getattr(getattr(msg, "audio", None), "data", "")
    wav  = save_temp_wav(b64) if b64 else None

    with H_LOCK:
        HISTORY.append({"role":"assistant","content":text})

    return [m for m in HISTORY if m["role"]!="system"], wav

def talk(mic_wav: str, chat_ui: List[Dict]) -> Tuple[List[Dict], Optional[str]]:
    log.info(f"Received audio file: {mic_wav}")
    if not mic_wav or not pathlib.Path(mic_wav).exists():
        log.warning("Audio file missing")
        return chat_ui, None

    data = pathlib.Path(mic_wav).read_bytes()
    b64  = base64.b64encode(data).decode("ascii")

    # audio‑chat payload must have content=""
    with H_LOCK:
        HISTORY.append({"role":"user", "content":"", "audio":{"data":b64}})

    prompt = trim_history()
    log.info("Sending audio→chat request")
    try:
        resp = client.chat.completions.create(
            model      = MODEL_AUDIO,
            modalities = ["text", "audio"],
            audio      = {"voice": VOICE, "format": "wav"},
            messages   = prompt,
        )
        msg     = resp.choices[0].message
        text    = msg.content or "(speech-only)"
        b64r    = getattr(getattr(msg, "audio", None), "data", "")
        bot_wav = save_temp_wav(b64r) if b64r else None

    except Exception:
        log.exception("Audio‑chat failed; using Whisper→text chat→TTS fallback")

        # 1) Transcribe
        with open(mic_wav, "rb") as f:
            tr = client.audio.transcriptions.create(model=WHISPER, file=f)
        user_text = tr.text
        log.info(f"Whisper: {user_text!r}")

        with H_LOCK:
            HISTORY.append({"role":"user", "content":user_text})

        # 2) Text chat
        text_resp = client.chat.completions.create(
            model    = MODEL_TEXT,
            messages = trim_history(),
        )
        text    = text_resp.choices[0].message.content
        log.info(f"GPT‑4o text reply: {text!r}")

        # 3) TTS
        tts     = client.audio.speech.create(model=TTS, voice=VOICE, input=text)
        bot_wav = save_temp_wav(tts["audio"]["data"])

    with H_LOCK:
        HISTORY.append({"role":"assistant","content":text})

    # update UI
    chat_ui.append({"role":"user",      "content":"(аудио)"})
    chat_ui.append({"role":"assistant", "content":text})

    return chat_ui, bot_wav

# ── Gradio UI ─────────────────────────────────────────────────────────────
with gr.Blocks(title="Scraptraffic Voice Bot (file‑mode demo)") as demo:
    gr.Markdown("### 📞 Scraptraffic — приём лома (file‑mode demo)")

    chat  = gr.Chatbot(type="messages", label="Диалог", height=360)
    mic   = gr.Audio(sources=["microphone"], label="🎙️ Hold to Talk",
                     type="filepath", streaming=False)
    reply = gr.Audio(label="🤖 Ответ", interactive=False, autoplay=True)
    reset = gr.Button("🔄 Reset")

    demo.load(fn=greet, inputs=None, outputs=[chat, reply])
    mic.stop_recording(fn=talk, inputs=[mic, chat], outputs=[chat, reply])
    reset.click(fn=lambda: (clear_history(), None),
                inputs=None, outputs=[chat, reply], queue=False)

if __name__ == "__main__":
    log.info("Starting… http://127.0.0.1:7860")
    demo.launch(server_name="0.0.0.0", server_port=7860, share=False)
