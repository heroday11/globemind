#!/usr/bin/env python3
import argparse
import json
import subprocess
import wave
from pathlib import Path


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as wav:
        return wav.getnframes() / float(wav.getframerate())


def run(args: list[str]) -> None:
    print(" ".join(args), flush=True)
    subprocess.run(args, check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--video", required=True)
    parser.add_argument("--bgm", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--voice-dir", required=True)
    parser.add_argument("--voice-timeline", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--duration", type=float, default=137.0)
    args = parser.parse_args()

    ffmpeg = args.ffmpeg
    duration = args.duration
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    voice_dir = Path(args.voice_dir)
    voice_timeline = Path(args.voice_timeline)
    output = Path(args.output)
    voice_timeline.parent.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)

    voice_items = []
    for item in manifest:
        wav_path = voice_dir / f"{item['id']}.wav"
        if not wav_path.exists():
            raise FileNotFoundError(wav_path)
        item = dict(item)
        item["file"] = str(wav_path)
        item["duration"] = wav_duration(wav_path)
        item["end"] = item["start"] + item["duration"]
        if item["end"] <= duration:
            voice_items.append(item)
        else:
            print(f"Skipping {item['id']} because it exceeds target duration: {item['end']:.2f}s")

    timing_path = voice_timeline.with_suffix(".json")
    timing_path.write_text(json.dumps(voice_items, ensure_ascii=False, indent=2), encoding="utf-8")

    voice_inputs: list[str] = []
    voice_filters: list[str] = []
    mix_labels: list[str] = []
    for index, item in enumerate(voice_items):
        voice_inputs.extend(["-i", item["file"]])
        delay_ms = int(round(item["start"] * 1000))
        fade_out_start = max(0.0, item["duration"] - 0.08)
        label = f"v{index}"
        voice_filters.append(
            f"[{index}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=mono,"
            f"volume=1.45,afade=t=in:st=0:d=0.03,"
            f"afade=t=out:st={fade_out_start:.3f}:d=0.08,"
            f"adelay={delay_ms}:all=1[{label}]"
        )
        mix_labels.append(f"[{label}]")

    voice_filter = ";".join(voice_filters)
    voice_filter += (
        f";{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=longest:normalize=0,"
        f"apad=whole_dur={duration:.3f},atrim=0:{duration:.3f},"
        "aformat=sample_fmts=s16:channel_layouts=stereo[aout]"
    )

    run(
        [
            ffmpeg,
            "-y",
            *voice_inputs,
            "-filter_complex",
            voice_filter,
            "-map",
            "[aout]",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(voice_timeline),
        ]
    )

    final_filter = (
        f"[1:a]aresample=48000,atrim=0:{duration:.3f},"
        "afade=t=in:st=0:d=1.2,"
        f"afade=t=out:st={max(0, duration - 2.5):.3f}:d=2.5,"
        "volume=0.16[bgm];"
        "[2:a]aresample=48000,volume=1.0,asplit=2[vo_sc][vo_mix];"
        "[bgm][vo_sc]sidechaincompress=threshold=0.035:ratio=8:attack=40:release=650[ducked];"
        "[ducked][vo_mix]amix=inputs=2:duration=first:normalize=0,"
        "alimiter=limit=0.95[aout]"
    )
    run(
        [
            ffmpeg,
            "-y",
            "-i",
            args.video,
            "-stream_loop",
            "-1",
            "-i",
            args.bgm,
            "-i",
            str(voice_timeline),
            "-filter_complex",
            final_filter,
            "-map",
            "0:v:0",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            "-t",
            f"{duration:.3f}",
            str(output),
        ]
    )


if __name__ == "__main__":
    main()
