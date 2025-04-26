#!/usr/bin/env python3
"""
phone_agent.py – Scraptraffic voice bot for OpenAI Realtime API
────────────────────────────────────────────────────────────────
• Lists mics with --list-devices, choose one with --mic N
• Lists speakers with --list-output-devices, choose one with --output-device N
• WebRTC-VAD if installed; pure-Python energy VAD otherwise
• Starts recording after ≥100 ms of consecutive speech
• Sends utterance when ≥2000 ms of continuous silence is detected
• Won’t start a new turn until the assistant finishes the previous one
• Press “q” in the console to quit
• Bot will finish speaking entirely before listening again
• All audio played live via a RawOutputStream
• Displays a single-line “mic status” indicator (Voice vs Silence)
• Prints “👂 Your turn — listening now” whenever the assistant is done
"""

from __future__ import annotations
import argparse
import asyncio
import os
import sys
import time

import numpy as np
import sounddevice as sd
from pynput import keyboard

from openai_realtime_client import RealtimeClient, TurnDetectionMode

# ── Optional WebRTC-VAD ─────────────────────────────────────
try:
    import webrtcvad  # type: ignore
    HAVE_WEBRTC = True
except ModuleNotFoundError:
    HAVE_WEBRTC = False

# ── Constants ────────────────────────────────────────────────
SAMPLE_RATE            = 16_000
FRAME_MS               = 30
FRAME_SAMPLES          = SAMPLE_RATE * FRAME_MS // 1_000

# how many frames of speech to start (100 ms → ~4 frames)
START_SPEECH_FRAMES    = 100 // FRAME_MS  
# how many total frames minimum to accept an utterance (400 ms → ~13 frames)
MIN_UTTERANCE_FRAMES   = 400 // FRAME_MS  
# how many frames of silence to end (2000 ms → ~66 frames)
END_SILENCE_FRAMES     = 2000 // FRAME_MS  

CALIBRATE_SEC          = 1.5
ENERGY_MULT            = 4.0
ENERGY_OFFSET          = 0.003

# Realtime API emits 24 kHz 16-bit PCM
OUTPUT_SR              = 24_000

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
    """Tiny thread-safe boolean."""
    def __init__(self) -> None:
        self._v = False
    def set(self, v: bool) -> None:
        self._v = v
    def get(self) -> bool:
        return self._v

async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list-devices", action="store_true",
                        help="List available input (mic) devices")
    parser.add_argument("--list-output-devices", action="store_true",
                        help="List available output (speaker) devices")
    parser.add_argument("--mic", type=int,
                        help="Index of input device from --list-devices")
    parser.add_argument("--output-device", type=int,
                        help="Index of output device from --list-output-devices")
    parser.add_argument("--debug-level", choices=("none","energy"),
                        default="energy", help="Show energy vs threshold")
    args = parser.parse_args()

    # ── List devices ──────────────────────────────────────────
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

    # ── Pick mic & speaker ────────────────────────────────────
    mic_index = args.mic if args.mic is not None else inputs[0][0]
    out_index = args.output_device if args.output_device is not None else sd.default.device[1]
    print(f"✔️ Using mic #{mic_index}:    {devices[mic_index]['name']}")
    print(f"✔️ Using speaker #{out_index}: {devices[out_index]['name']}")

    api_key = os.getenv("OPENAI_API_KEY") or sys.exit("OPENAI_API_KEY not set")

    loop               = asyncio.get_running_loop()
    speaking           = Flag()      # assistant audio playing
    awaiting_response  = Flag()      # waiting on assistant
    send_lock          = asyncio.Lock()
    assistant_done_evt = asyncio.Event()

    # ── PCM output stream ─────────────────────────────────────
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

    # ── Energy debug ──────────────────────────────────────────
    def debug_energy(e: float, thr: float):
        print(f"\rEnergy {e:.4f} thr {thr:.4f}", end="", flush=True)

    # ── Event handlers ────────────────────────────────────────
    def _on_audio_start(*_):
        speaking.set(True)
    def _on_audio_end(*_):
        speaking.set(False)
        if awaiting_response.get():
            awaiting_response.set(False)
            assistant_done_evt.set()
            print("\n👂 Your turn — listening now")
    def _on_response_done(*_):
        awaiting_response.set(False)
        assistant_done_evt.set()
        print("\n👂 Your turn — listening now")
    def _on_interrupted(*_):
        speaking.set(False)
        awaiting_response.set(False)
        assistant_done_evt.set()
        print("\n👂 Your turn — listening now")

    # ── Connect to Realtime API ───────────────────────────────
    client = RealtimeClient(
        api_key              = api_key,
        instructions         = SYSTEM_PROMPT,
        voice                = "alloy",
        turn_detection_mode  = TurnDetectionMode.MANUAL,
        on_text_delta        = lambda t: print(f"\nAssistant: {t}", end="", flush=True),
        on_audio_delta       = play_audio_safe,
        extra_event_handlers = {
            "response.audio.delta":  _on_audio_start,
            "response.audio.done":   _on_audio_end,
            "response.done":         _on_response_done,
            "response.interrupted":  _on_interrupted,
        }
    )

    keyboard.Listener(on_press=lambda k: loop.stop() if getattr(k,"char","")=="q" else None).start()
    await client.connect()
    asyncio.create_task(client.handle_messages())

    # ── Greeting ───────────────────────────────────────────────
    print("🎙️ Connected – playing greeting …")
    assistant_done_evt.clear()
    awaiting_response.set(True)
    async with send_lock:
        await client.create_response()
    await assistant_done_evt.wait()
    print("👂 Your turn — listening now")

    # ── Static VAD calibration ─────────────────────────────────
    if HAVE_WEBRTC:
        vad = webrtcvad.Vad(3)  # Aggressiveness mode 3
        print("[INFO] WebRTC VAD enabled, mode=3")
    else:
        print("[INFO] energy VAD")
        print(f"🔇 Calibrating {CALIBRATE_SEC}s ambient…")
        amb = sd.rec(int(CALIBRATE_SEC*SAMPLE_RATE),
                     samplerate=SAMPLE_RATE,
                     channels=1,
                     dtype="float32",
                     device=mic_index)
        sd.wait()
        rms = float(np.sqrt((amb[:,0]**2).mean()))
        energy_threshold = rms*ENERGY_MULT + ENERGY_OFFSET
        print(f"✅ Threshold: {energy_threshold:.6f}")

    # ── Speech capture, frame-based hangover ───────────────────
    in_speech     = False
    voice_frames  = 0
    silence_frames = 0
    buffer        = bytearray()

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
        nonlocal in_speech, voice_frames, silence_frames, buffer

        if speaking.get() or awaiting_response.get():
            return

        samples = indata[:,0]
        energy  = float(np.sqrt((samples**2).mean()))
        pcm     = (samples * 32767).astype('int16').tobytes()

        # debug energy (only in fallback)
        if args.debug_level=="energy" and not HAVE_WEBRTC:
            debug_energy(energy, energy_threshold)

        # VAD decision
        if HAVE_WEBRTC:
            is_voice = vad.is_speech(pcm, SAMPLE_RATE)
        else:
            is_voice = energy > energy_threshold

        # mic status indicator
        status = "🔊" if is_voice else "🔈"
        print(f"\r[{status}]", end="", flush=True)

        if is_voice:
            voice_frames += 1
            silence_frames = 0
            if not in_speech and voice_frames >= START_SPEECH_FRAMES:
                in_speech = True
                buffer.clear()
                print("\n[🎤 Recording …]")
            if in_speech:
                buffer.extend(pcm)
        else:
            silence_frames += 1
            if not in_speech:
                voice_frames = 0
            elif silence_frames < END_SILENCE_FRAMES:
                buffer.extend(pcm)
            else:
                # end of utterance
                in_speech = False
                print("\n[Speech ended]")
                if len(buffer)//2//FRAME_SAMPLES >= MIN_UTTERANCE_FRAMES:
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
        print("\n🔴 Speak when ready (press q to quit) …")
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            pass

    out_stream.stop()
    out_stream.close()
    await client.close()
    print("\n👋 Bye")

if __name__=="__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
