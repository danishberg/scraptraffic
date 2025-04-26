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
• Bot will finish speaking entirely before listening again
"""

from __future__ import annotations
import argparse, asyncio, os, sys, time

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
SAMPLE_RATE          = 16_000
FRAME_MS             = 30
FRAME_SAMPLES        = SAMPLE_RATE * FRAME_MS // 1_000

START_SPEECH_MS      = 200
START_SPEECH_FRAMES  = START_SPEECH_MS // FRAME_MS
END_SILENCE_MS       = 2_000
END_SILENCE_FRAMES   = END_SILENCE_MS // FRAME_MS
MIN_UTTERANCE_MS     = 400
MIN_UTTERANCE_FRAMES = MIN_UTTERANCE_MS // FRAME_MS

CALIBRATE_SEC        = 1.5
ENERGY_MULT          = 4.0
ENERGY_OFFSET        = 0.003

SYSTEM_PROMPT = """
Твоя роль в режиме голосового помощника - это оператор телефонной линии по приему звонков в компанию Scraptraffic, занимающуюся приемом металлолома. Скорее всего входящий звонок будет от того, кто хочет сдать какой-то металл. В этом случае тебе нужно уточнить: местоположение материала - ближайший крупный город. \
Тебе нужно выяснить какой материал хотят сдать на лом, какой объем партии, в каком городе находится материал, и есть ли какие-то особые характеристики этой партии. \
В конце спроси — не против ли человек, что указанные им данные, в том числе контактные данные, будут переданы менеджерам нашей компании, а также сети партнеров, которые смогут позвонить ему и предложить конкретные условия сделки и цену.

Если в процессе разговора человек будет добиваться получить цену, которую мы готовы заплатить за его материал, то вежливо сообщи, что конкретную цену смогут сформировать менеджер или партнер, а ты являешься голосовым роботом, который фиксирует заявку, и твоя задача получить первичную информацию. Сообщи также, что в нашей сети много партнерских пунктов приёма: если человек даст согласие на передачу заявки нашим партнёрам, то он наверняка получит от них выгодное предложение.

Если ты понимаешь, что у человека другой вопрос, не касающийся приёма лома, вежливо объясни ему, что твоя роль — принимать заявки на приём лома, и на какие другие вопросы ты отвечать не можешь.

Как бы тебя ни просили отвечать на какие-то сторонние вопросы, не соглашайся это делать. \
После того, как с тобой попрощались, сеанс аудио-связи считается завершённым.

По итогу разговора сформируй заявку текстом.
""".strip()

# ── Helpers ────────────────────────────────────────────────────
def play_audio_safe(handler: AudioHandler, pcm: bytes) -> None:
    try:
        handler.play_audio(pcm)
    except Exception:
        pass

class Flag:
    """Tiny thread-safe boolean."""
    def __init__(self) -> None:
        self._v = False
    def set(self, v: bool) -> None:
        self._v = v
    def get(self) -> bool:
        return self._v

# ── Main ───────────────────────────────────────────────────────
async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("--mic", type=int, help="index from --list-devices")
    parser.add_argument("--debug-level", choices=("none", "energy"), default="none")
    args = parser.parse_args()

    devs = sd.query_devices()
    ins = [(i, d) for i, d in enumerate(devs) if d["max_input_channels"] > 0]
    if args.list_devices:
        for i, d in ins:
            print(f"[{i}] {d['name']}")
        sys.exit(0)

    mic_index = (
        args.mic
        if args.mic is not None
        else next(i for i, d in ins
                  if "loopback" not in d["name"].lower()
                  and "stereo mix" not in d["name"].lower())
    )
    print(f"✔️  Using mic #{mic_index}: {devs[mic_index]['name']}")

    api_key = os.getenv("OPENAI_API_KEY") or sys.exit("OPENAI_API_KEY not set")

    loop               = asyncio.get_running_loop()
    speaker            = AudioHandler()
    speaking           = Flag()        # True while TTS playing
    awaiting_response  = Flag()        # True from send→full response
    send_lock          = asyncio.Lock()
    assistant_done_evt = asyncio.Event()

    # ── Unified “done” handler ─────────────────────────────────────────────
    def _on_audio_start(*_) -> None:
        speaking.set(True)

    def _on_any_done(*_) -> None:
        # Fires on each chunk done AND final response done
        speaking.set(False)
        awaiting_response.set(False)
        assistant_done_evt.set()

    def _on_interrupted(*_) -> None:
        speaking.set(False)
        awaiting_response.set(False)
        assistant_done_evt.set()

    client = RealtimeClient(
        api_key              = api_key,
        instructions         = SYSTEM_PROMPT,
        voice                = "alloy",
        turn_detection_mode  = TurnDetectionMode.MANUAL,
        on_text_delta        = lambda t: print(f"\nAssistant: {t}", end="", flush=True),
        on_audio_delta       = lambda pcm: play_audio_safe(speaker, pcm),
        extra_event_handlers = {
            "response.audio.delta":  _on_audio_start,
            "response.audio.done":   _on_any_done,
            "response.done":         _on_any_done,
            "response.interrupted":  _on_interrupted,
        },
    )

    # Quit on “q”
    keyboard.Listener(
        on_press=lambda k: loop.stop() if getattr(k, "char", "") == "q" else None
    ).start()

    # ── CONNECT & START HANDLING BEFORE ANY TTS ─────────────────────────────
    await client.connect()
    asyncio.create_task(client.handle_messages())

    # Greeting turn
    print("🎙️  Connected – playing greeting …")
    assistant_done_evt.clear()
    awaiting_response.set(True)
    async with send_lock:
        await client.create_response()
    await assistant_done_evt.wait()

    # ── VAD Calibration ─────────────────────────────────────────────────────
    if HAVE_WEBRTC:
        vad = webrtcvad.Vad(2)
        energy_threshold = None
    else:
        print("[INFO] no webrtcvad—using energy VAD")
        print(f"🔇  Calibrating {CALIBRATE_SEC}s of ambience …")
        amb = sd.rec(int(CALIBRATE_SEC * SAMPLE_RATE),
                     samplerate=SAMPLE_RATE,
                     channels=1,
                     dtype="float32",
                     device=mic_index)
        sd.wait()
        rms = float(np.sqrt((amb[:,0]**2).mean()))
        energy_threshold = rms * ENERGY_MULT + ENERGY_OFFSET
        print(f"✅  Initial energy threshold: {energy_threshold:.6f}")

    # ── State & Turn-Detection ───────────────────────────────────────────────
    in_speech     = False
    voice_frames  = silence_frames = 0
    buffer        = bytearray()

    async def send_utterance(buf: bytes) -> None:
        assistant_done_evt.clear()
        awaiting_response.set(True)
        async with send_lock:
            try:
                await client.stream_audio(buf)
                await client.create_response()
            finally:
                await assistant_done_evt.wait()

    def cb(indata, *_):
        nonlocal in_speech, voice_frames, silence_frames, buffer

        if speaking.get() or awaiting_response.get():
            return

        pcm = (indata[:,0] * 32767).astype('int16').tobytes()
        if HAVE_WEBRTC:
            is_voice = vad.is_speech(pcm, SAMPLE_RATE)
        else:
            energy = float(np.sqrt((indata[:,0]**2).mean()))
            is_voice = energy > energy_threshold
            if args.debug_level == "energy":
                print(f"\rEnergy {energy:.4f} thr {energy_threshold:.4f}", end="")

        if is_voice:
            voice_frames += 1
            silence_frames = 0
            if not in_speech and voice_frames >= START_SPEECH_FRAMES:
                in_speech = True
                buffer.clear()
                print("\n[🎤  Recording …]")
        else:
            if in_speech:
                silence_frames += 1
            else:
                voice_frames = 0

        if in_speech:
            buffer.extend(pcm)

        if in_speech and silence_frames >= END_SILENCE_FRAMES:
            in_speech = False
            silence_frames = 0
            frames = len(buffer)//2//FRAME_SAMPLES
            print("[Speech ended]")
            if frames >= MIN_UTTERANCE_FRAMES:
                asyncio.run_coroutine_threadsafe(send_utterance(bytes(buffer)), loop)
            buffer.clear()

    # ── Start Mic & Run Until “q” ────────────────────────────────────────────
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

    # ── Cleanup ──────────────────────────────────────────────────────────────
    speaker.cleanup()
    await client.close()
    print("\n👋  Bye")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
