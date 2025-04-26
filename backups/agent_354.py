#!/usr/bin/env python3
"""
phone_agent.py – Scraptraffic voice bot for OpenAI Realtime API (v3)
────────────────────────────────────────────────────────────────
Key Improvements:
• Lower-latency 20 ms frames (instead of 30 ms)
• Throttled mic-status updates (max 10 Hz)
• Push-to-talk, dynamic VAD (WebRTC or energy-based)
• Stronger turn-detection thresholds to avoid background noise
• Maintains single-turn-at-a-time, no mid-response cuts
• Dynamic ambient recalibration when idle
• Async-safe, non-blocking audio I/O
• Extended websocket connect timeout and retry logic
"""

from __future__ import annotations
import argparse
import asyncio
import os
import sys
import time
import threading

import numpy as np
import sounddevice as sd
from pynput import keyboard

# ── Monkey-patch websockets to extend connect timeout ───────────────────────
import websockets
from websockets.client import connect as _ws_orig_connect

def _ws_connect(uri, *args, open_timeout: float = 60.0, **kwargs):
    """
    Wrapper around websockets.connect to set a higher open_timeout.
    """
    return _ws_orig_connect(uri, *args, open_timeout=open_timeout, **kwargs)

websockets.connect = _ws_connect

from openai_realtime_client import RealtimeClient, TurnDetectionMode

# ── Optional WebRTC-VAD ───────────────────────────────────────────────────
try:
    import webrtcvad  # type: ignore
    HAVE_WEBRTC = True
except ModuleNotFoundError:
    HAVE_WEBRTC = False

# ── Audio & VAD parameters ─────────────────────────────────────────────────
SAMPLE_RATE            = 16_000
FRAME_MS               = 20                          # smaller frame for lower latency
FRAME_SAMPLES          = SAMPLE_RATE * FRAME_MS // 1_000

START_SPEECH_FRAMES    = 200  // FRAME_MS            # ~200 ms of speech to start
MIN_UTTERANCE_FRAMES   = 600  // FRAME_MS            # ~600 ms total to send
END_SILENCE_FRAMES     = 2000 // FRAME_MS            # ~2000 ms of silence to end

CALIBRATE_SEC          = 1.5
ENERGY_MULT            = 4.0
ENERGY_OFFSET          = 0.003

OUTPUT_SR              = 24_000                      # assistant audio

SYSTEM_PROMPT = """
Твоя роль в режиме голосового помощника - это оператор телефонной линии по приему звонков в компанию Scraptraffic, занимающуюся приемом металлолома. Скорее всего входящий звонок будет от того, кто хочет сдать какой-то металл. В этом случае тебе нужно уточнить: местоположение материала - ближайший крупный город. \
Тебе нужно выяснить какой материал хотят сдать на лом, какой объем партии, в каком городе находится материал, и есть ли какие-то особые характеристики этой партии. \
В конце спроси — не против ли человек, что указанные им данные, в том числе контактные данные, будут переданы менеджерам нашей компании, а также сети партнеров, которые смогут позвонить ему и предложить конкретные условия сделки и цену.

Если в процессе разговора человек будет добиваться получить цену, которую мы готовы заплатить за его материал, то вежливо сообщи, что конкретную цену смогут сформировать менеджер или партнер, а ты являешься голосовым роботом, который фиксирует заявку, и твоя задача получить первичную информацию. Сообщи также, что в нашей сети много партнерских пунктов приёма: если человек даст согласие на передачу заявки нашим партнёрам, то он наверняка получит от них выгодное предложение.

Если ты понимаешь, что у человека другой вопрос, не касающийся приема лома, вежливо объясни ему, что твоя роль — принимать заявки на прием лома, и на какие другие вопросы ты не можешь ответить.

Как бы тебя ни просили отвечать на какие-то сторонние вопросы, не соглашайся это делать. \
После того, как с тобой попрощались, сеанс аудио-связи считается завершённым.

По итогу разговора сформируй заявку текстом.
""".strip()

class Flag:
    """Thread-safe boolean flag."""
    def __init__(self) -> None:
        self._v = False
    def set(self, v: bool) -> None:
        self._v = v
    def get(self) -> bool:
        return self._v

async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list-devices",        action="store_true")
    parser.add_argument("--list-output-devices", action="store_true")
    parser.add_argument("--mic",                 type=int)
    parser.add_argument("--output-device",       type=int)
    parser.add_argument("--debug-level",         choices=("none","energy"), default="energy")
    parser.add_argument("--push-to-talk",        action="store_true")
    args = parser.parse_args()

    # ── List or select devices ───────────────────────────────────────────────
    devices = sd.query_devices()
    inputs  = [(i,d) for i,d in enumerate(devices) if d["max_input_channels"]>0]
    outputs = [(i,d) for i,d in enumerate(devices) if d["max_output_channels"]>0]

    if args.list_devices:
        print("Input devices:")
        for i,d in inputs:
            print(f"[{i}] {d['name']}")
        return
    if args.list_output_devices:
        print("Output devices:")
        for i,d in outputs:
            print(f"[{i}] {d['name']}")
        return

    mic_index = args.mic if args.mic is not None else inputs[0][0]
    out_index = args.output_device if args.output_device is not None else sd.default.device[1]
    print(f"✔️ Mic #{mic_index}:     {devices[mic_index]['name']}")
    print(f"✔️ Speaker #{out_index}: {devices[out_index]['name']}")

    # ── Prepare OpenAI client & event flags ─────────────────────────────────
    api_key = os.getenv("OPENAI_API_KEY") or sys.exit("OPENAI_API_KEY not set")
    loop               = asyncio.get_running_loop()
    speaking           = Flag()      # assistant is playing audio
    awaiting_response  = Flag()      # waiting on assistant to finish
    send_lock          = asyncio.Lock()
    assistant_done_evt = asyncio.Event()

    # ── PCM output stream ─────────────────────────────────────────────────
    out_stream = sd.RawOutputStream(
        samplerate=OUTPUT_SR,
        channels=1,
        dtype="int16",
        blocksize=FRAME_SAMPLES,
        device=out_index,
    )
    out_stream.start()
    def play_audio_safe(pcm: bytes) -> None:
        try:
            out_stream.write(pcm)
        except:
            pass

    # ── Energy debug ──────────────────────────────────────────────────────
    def debug_energy(e: float, thr: float):
        print(f"\rEnergy {e:.4f}  thr {thr:.4f}", end="", flush=True)

    # ── Event handlers ────────────────────────────────────────────────────
    def _on_audio_start(*_):
        speaking.set(True)
    def _on_audio_end(*_):
        speaking.set(False)
        if awaiting_response.get():
            awaiting_response.set(False)
            assistant_done_evt.set()
            print("\n👂 Your turn — listening now")
    def _on_response_done(*_):
        if awaiting_response.get():
            awaiting_response.set(False)
            assistant_done_evt.set()
            print("\n👂 Your turn — listening now")
    def _on_interrupted(*_):
        speaking.set(False)
        if awaiting_response.get():
            awaiting_response.set(False)
            assistant_done_evt.set()
            print("\n👂 Your turn — listening now")

    # ── Initialize OpenAI Realtime client ─────────────────────────────────
    client = RealtimeClient(
        api_key             = api_key,
        instructions        = SYSTEM_PROMPT,
        voice               = "alloy",
        turn_detection_mode = TurnDetectionMode.MANUAL,
        on_text_delta       = lambda t: print(f"\nAssistant: {t}", end="", flush=True),
        on_audio_delta      = play_audio_safe,
        extra_event_handlers= {
            "response.audio.delta":  _on_audio_start,
            "response.audio.done":   _on_audio_end,
            "response.done":         _on_response_done,
            "response.interrupted":  _on_interrupted,
        }
    )

    # ── Quit key handler ─────────────────────────────────────────────────
    keyboard.Listener(
        on_press=lambda k: loop.stop() if getattr(k, "char", "") == "q" else None
    ).start()

    # ── Connect with retry on timeout ──────────────────────────────────────
    retries = 3
    for attempt in range(1, retries+1):
        try:
            print(f"🔌 Connecting to server (attempt {attempt}/{retries})…")
            await client.connect()
            break
        except TimeoutError:
            if attempt < retries:
                print("❗ Connection timed out, retrying in 5 s…")
                await asyncio.sleep(5)
            else:
                print("❌ Unable to connect after retries, exiting.")
                sys.exit(1)

    asyncio.create_task(client.handle_messages())

    # ── Initial greeting ─────────────────────────────────────────────────
    print("🎙️ Connected – playing greeting …")
    assistant_done_evt.clear()
    awaiting_response.set(True)
    async with send_lock:
        await client.create_response()
    await assistant_done_evt.wait()
    print("👂 Your turn — listening now")

    # ── VAD setup & initial calibration ──────────────────────────────────
    if HAVE_WEBRTC:
        vad = webrtcvad.Vad(3)
        print("[INFO] WebRTC VAD enabled, mode=3")
    else:
        print("[INFO] Energy VAD fallback")
        print(f"🔇 Calibrating ambient for {CALIBRATE_SEC}s…")
        amb = sd.rec(int(CALIBRATE_SEC * SAMPLE_RATE),
                     samplerate=SAMPLE_RATE,
                     channels=1,
                     dtype="float32",
                     device=mic_index)
        sd.wait()
        rms = float(np.sqrt((amb[:,0]**2).mean()))
        vad_config = {"threshold": rms * ENERGY_MULT + ENERGY_OFFSET}
        print(f"✅ Initial threshold: {vad_config['threshold']:.6f}")

        def recalibrate():
            while True:
                time.sleep(60)
                if not speaking.get() and not awaiting_response.get():
                    tmp = sd.rec(int(CALIBRATE_SEC * SAMPLE_RATE),
                                 samplerate=SAMPLE_RATE,
                                 channels=1,
                                 dtype="float32",
                                 device=mic_index)
                    sd.wait()
                    rms2 = float(np.sqrt((tmp[:,0]**2).mean()))
                    vad_config["threshold"] = rms2 * ENERGY_MULT + ENERGY_OFFSET
                    print(f"\n🔄 New threshold: {vad_config['threshold']:.6f}")

        threading.Thread(target=recalibrate, daemon=True).start()

    # ── Optional push-to-talk ───────────────────────────────────────────────
    talk_pressed = Flag()
    if args.push_to_talk:
        from pynput.keyboard import Key, Listener
        def on_press(k):
            if k == Key.space:
                talk_pressed.set(True)
        def on_release(k):
            if k == Key.space:
                talk_pressed.set(False)
        Listener(on_press=on_press, on_release=on_release).start()
        print("🎤 Push-to-talk: hold SPACE to speak")

    # ── Speech capture & turn logic ────────────────────────────────────────
    in_speech      = False
    voice_frames   = 0
    silence_frames = 0
    buffer         = bytearray()
    last_status    = None
    last_stat_t    = 0.0

    async def send_utt(data: bytes) -> None:
        assistant_done_evt.clear()
        awaiting_response.set(True)
        async with send_lock:
            try:
                await client.stream_audio(data)
                await client.create_response()
            finally:
                await assistant_done_evt.wait()

    def callback(indata, *_):
        nonlocal in_speech, voice_frames, silence_frames, buffer, last_status, last_stat_t

        # block while assistant is speaking or awaiting response
        if speaking.get() or awaiting_response.get():
            return
        if args.push_to_talk and not talk_pressed.get():
            return

        samples = indata[:,0]
        energy  = float(np.sqrt((samples**2).mean()))
        pcm     = (samples * 32767).astype("int16").tobytes()

        if args.debug_level == "energy" and not HAVE_WEBRTC:
            debug_energy(energy, vad_config["threshold"])

        if HAVE_WEBRTC:
            is_voice = vad.is_speech(pcm, SAMPLE_RATE)
        else:
            is_voice = energy > vad_config["threshold"]

        # throttle status updates to 10 Hz
        now = time.time()
        status = "🔊" if is_voice else "🔈"
        if status != last_status or (now - last_stat_t) > 0.1:
            print(f"\r[{status}]", end="", flush=True)
            last_status, last_stat_t = status, now

        if is_voice:
            voice_frames += 1
            silence_frames = 0
            if not in_speech and voice_frames >= START_SPEECH_FRAMES:
                in_speech = True
                buffer.clear()
                print("\n[🎤 Recording…]")
            if in_speech:
                buffer.extend(pcm)
        else:
            silence_frames += 1
            if not in_speech:
                voice_frames = 0
            elif silence_frames < END_SILENCE_FRAMES:
                buffer.extend(pcm)
            else:
                if in_speech:
                    in_speech = False
                    print("\n[Speech ended]")
                    utter_frames = len(buffer) // 2 // FRAME_SAMPLES
                    if utter_frames >= MIN_UTTERANCE_FRAMES:
                        asyncio.run_coroutine_threadsafe(send_utt(bytes(buffer)), loop)
                buffer.clear()
                voice_frames = silence_frames = 0

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        blocksize=FRAME_SAMPLES,
        device=mic_index,
        callback=callback,
    ):
        print("\n🔴 Ready (press q to quit)…")
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            pass

    out_stream.stop()
    out_stream.close()
    await client.close()
    print("\n👋 Bye")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
