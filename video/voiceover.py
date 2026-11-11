"""Synthesise one WAV per scene from narration.json with Piper (en_US-ljspeech-high).

Writes video/build/vo/<id>.wav and video/build/vo/durations.json (seconds per scene),
which the recorder uses to pace each scene to its narration.
"""
import json
import wave
from pathlib import Path

from piper import PiperVoice, SynthesisConfig

HERE = Path(__file__).parent
OUT = HERE / "build" / "vo"
VOICE = HERE / "voices" / "en_US-ljspeech-high.onnx"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    voice = PiperVoice.load(str(VOICE))
    cfg = SynthesisConfig(length_scale=1.05)   # a touch slower than default, easier to follow over visuals
    durations = {}
    for scene in json.loads((HERE / "narration.json").read_text(encoding="utf-8")):
        path = OUT / f"{scene['id']}.wav"
        with wave.open(str(path), "wb") as w:
            voice.synthesize_wav(scene["text"], w, syn_config=cfg)
        with wave.open(str(path), "rb") as w:
            durations[scene["id"]] = round(w.getnframes() / w.getframerate(), 2)
    (OUT / "durations.json").write_text(json.dumps(durations, indent=2))
    print(json.dumps(durations, indent=2), "\ntotal", round(sum(durations.values()), 1), "s")


if __name__ == "__main__":
    main()
