#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

import soundfile as sf
from voxcpm import VoxCPM


CONTROL = "年轻女性声音，柔和自然，亲切清透，中文普通话，语速适中，像真人产品讲解，不要播音腔，不要机械感"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--reference-audio", required=True)
    parser.add_argument("--cache-dir", required=True)
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    items = json.loads(manifest_path.read_text(encoding="utf-8"))
    model = VoxCPM.from_pretrained(
        hf_model_id="openbmb/VoxCPM2",
        load_denoiser=False,
        cache_dir=args.cache_dir,
        optimize=False,
        device="cuda",
    )

    summary = []
    for item in items:
        target_text = f"({CONTROL}){item['text']}"
        audio = model.generate(
            text=target_text,
            reference_wav_path=args.reference_audio,
            cfg_value=2.0,
            inference_timesteps=10,
            normalize=False,
        )
        out_path = out_dir / f"{item['id']}.wav"
        sample_rate = model.tts_model.sample_rate
        sf.write(out_path, audio, sample_rate)
        duration = len(audio) / sample_rate
        summary.append(
            {
                "id": item["id"],
                "start": item["start"],
                "duration": round(duration, 3),
                "end": round(item["start"] + duration, 3),
                "file": str(out_path),
                "text": item["text"],
            }
        )
        print(f"{item['id']}: {duration:.2f}s -> {out_path}", flush=True)

    (out_dir / "durations.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
