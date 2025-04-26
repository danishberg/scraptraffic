#!/usr/bin/env python3
"""
phone_agent.py – Scraptraffic voice bot for OpenAI Realtime API
────────────────────────────────────────────────────────────────
• Lists mics with --list-devices, choose one with --mic N  
• WebRTC-VAD if installed; pure-Python energy VAD otherwise  
• Starts recording after ≥200 ms of speech; needs ≥400 ms total to send  
• Sends utterance when ≥2 s of silence is detected  
• Won’t start a new turn until the assistant finishes the previous one  
• Press “q” in the console to quit
"""

from __future__ import annotations
import argparse, asyncio, os, sys, time
from typing import Optional

import numpy as np
import sounddevice as sd
from pynput import keyboard
from openai_realtime_client import RealtimeClient, AudioHandler, TurnDetectionMode

# ── Optional WebRTC-VAD ────────────────────────────────────────
try:
    import webrtcvad  # type: ignore
    HAVE_WEBRTC = True
except ModuleNotFoundError:
    HAVE_WEBRTC = False

# ── Constants ──────────────────────────────────────────────────
SAMPLE_RATE              = 16_000
FRAME_MS                 = 30
FRAME_SAMPLES            = SAMPLE_RATE * FRAME_MS // 1_000
START_SPEECH_MS          = 200
START_SPEECH_FRAMES      = START_SPEECH_MS // FRAME_MS
END_SILENCE_MS           = 2_000
END_SILENCE_FRAMES       = END_SILENCE_MS // FRAME_MS
MIN_UTTERANCE_MS         = 400         # ignore <400 ms blips
MIN_UTTERANCE_FRAMES     = MIN_UTTERANCE_MS // FRAME_MS
CALIBRATE_SEC            = 1.5
ENERGY_MULT              = 4.0
ENERGY_OFFSET            = 0.003

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

# ── Helpers ────────────────────────────────────────────────────
def play_audio_safe(handler: AudioHandler, pcm: bytes) -> None:
    try:
        handler.play_audio(pcm)
    except Exception:
        pass

class Flag:
    """Tiny thread-safe boolean container."""
    def __init__(self) -> None: self._state = False
    def set(self, v: bool) -> None: self._state = v
    def get(self) -> bool: return self._state

# ── Main ───────────────────────────────────────────────────────
async def main() -> None:
    # CLI --------------------------------------------------------------------
    p = argparse.ArgumentParser()
    p.add_argument("--list-devices", action="store_true")
    p.add_argument("--mic", type=int, help="index returned by --list-devices")
    p.add_argument("--debug-level", choices=("none", "energy"), default="none")
    args = p.parse_args()

    # Devices ----------------------------------------------------------------
    devs = sd.query_devices()
    ins = [(i, d) for i, d in enumerate(devs) if d["max_input_channels"] > 0]
    if args.list_devices:
        for i, d in ins: print(f"[{i}] {d['name']}")
        sys.exit(0)

    mic_index = (
        args.mic
        if args.mic is not None
        else next(
            i
            for i, d in ins
            if "loopback" not in d["name"].lower()
            and "stereo mix" not in d["name"].lower()
        )
    )
    print(f"✔️  Using mic #{mic_index}: {devs[mic_index]['name']}")

    # API key -----------------------------------------------------------------
    api_key = os.getenv("OPENAI_API_KEY") or sys.exit("OPENAI_API_KEY not set")

    loop = asyncio.get_running_loop()
    speaker = AudioHandler()
    speaking = Flag()
    awaiting_response = Flag()

    # Assistant event handlers -----------------------------------------------
    def _on_audio_start(*_): speaking.set(True)
    def _on_audio_done(*_):
        speaking.set(False)
        awaiting_response.set(False)

    client = RealtimeClient(
        api_key=api_key,
        instructions=SYSTEM_PROMPT,
        voice="alloy",
        turn_detection_mode=TurnDetectionMode.MANUAL,
        on_text_delta=lambda t: print(f"\nAssistant: {t}", end="", flush=True),
        on_audio_delta=lambda p: play_audio_safe(speaker, p),
        extra_event_handlers={
            "response.audio.delta": _on_audio_start,
            "response.audio.done":  _on_audio_done,
        },
    )

    # Quit on “q”
    keyboard.Listener(
        on_press=lambda k: loop.stop() if getattr(k, "char", "") == "q" else None
    ).start()

    # Connect & greeting ------------------------------------------------------
    await client.connect()
    print("🎙️  Connected – playing greeting …")
    await client.create_response()
    asyncio.create_task(client.handle_messages())

    # VAD ---------------------------------------------------------------------
    if HAVE_WEBRTC:
        vad = webrtcvad.Vad(2)
    else:
        print("[INFO] webrtcvad not found – using energy VAD")
        print(f"🔇  Calibrating {CALIBRATE_SEC}s of ambience …")
        amb = sd.rec(
            int(CALIBRATE_SEC * SAMPLE_RATE),
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            device=mic_index,
        )
        sd.wait()
        rms = np.sqrt(np.mean(np.square(amb[:, 0])))
        energy_threshold = rms * ENERGY_MULT + ENERGY_OFFSET
        print(f"✅  Initial energy threshold: {energy_threshold:.6f}")

    # State -------------------------------------------------------------------
    in_speech = False
    voice_frames = silence_frames = 0
    buffer = bytearray()
    last_voice: Optional[float] = None
    session_start = time.time()

    # Audio callback ----------------------------------------------------------
    def cb(indata, *_):
        nonlocal in_speech, voice_frames, silence_frames, buffer, energy_threshold, last_voice

        # Ignore while assistant is speaking or response is pending
        if speaking.get() or awaiting_response.get():
            return

        pcm16 = (indata[:, 0] * 32767).astype(np.int16).tobytes()
        if HAVE_WEBRTC:
            is_voice = vad.is_speech(pcm16, SAMPLE_RATE)
        else:
            energy = np.sqrt(np.mean(indata[:, 0] ** 2))
            is_voice = energy > energy_threshold
            if args.debug_level == "energy":
                print(f"\rEnergy {energy:.4f} thr {energy_threshold:.4f}", end="")

        # Track speech / silence ---------------------------------
        if is_voice:
            voice_frames += 1
            silence_frames = 0
            last_voice = time.time()
        else:
            if in_speech:
                silence_frames += 1
            voice_frames = 0

        # Start utterance ----------------------------------------
        if not in_speech and voice_frames >= START_SPEECH_FRAMES:
            in_speech = True
            buffer.clear()
            print("\n[🎤  Recording …]")

        # Buffer audio -------------------------------------------
        if in_speech:
            buffer.extend(pcm16)

        # End utterance ------------------------------------------
        if in_speech and silence_frames >= END_SILENCE_FRAMES:
            in_speech = False
            silence_frames = 0
            frames_in_utt = len(buffer) // 2 // FRAME_SAMPLES
            if frames_in_utt >= MIN_UTTERANCE_FRAMES:
                print("[📤  Sending utterance]")
                asyncio.run_coroutine_threadsafe(
                    client.stream_audio(bytes(buffer)), loop
                )
                awaiting_response.set(True)
                asyncio.run_coroutine_threadsafe(client.create_response(), loop)
            buffer.clear()

        # Auto-adjust threshold (energy VAD only) ----------------
        if (
            not HAVE_WEBRTC
            and last_voice is None
            and time.time() - session_start > 8
        ):
            energy_threshold *= 0.7
            last_voice = time.time()
            print(f"\n[INFO] Lowering threshold → {energy_threshold:.4f}")

    # Mic stream --------------------------------------------------------------
    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        blocksize=FRAME_SAMPLES,
        device=mic_index,
        callback=cb,
    ):
        print("🔴  Speak when ready (press q to quit) …")
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            pass

    # Cleanup -----------------------------------------------------------------
    speaker.cleanup()
    await client.close()
    print("\n👋  Bye")


# Entrypoint ------------------------------------------------------------------
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
