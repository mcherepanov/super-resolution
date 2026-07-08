#!/usr/bin/env python3
"""
Пакетная супер-резолюция аудио через FlashSR.
Обработка на 48 kHz, выход 44.1 kHz (CD), симметричный overlap-add.
"""

import os
os.environ["TQDM_DISABLE"] = "1"

import argparse
import contextlib
import math
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from scipy.signal import resample_poly

import warnings
warnings.filterwarnings("ignore")

from FlashSR.FlashSR import FlashSR

# ---- constants ----------------------------------------------------------------

TARGET_SR = 48_000
OUTPUT_SR = 44_100
WINDOW_LEN = 245_760
OVERLAP = 24_000
HOP = WINDOW_LEN - OVERLAP

AUDIO_EXTENSIONS = {".wav", ".flac", ".mp3", ".ogg", ".opus"}


# ---- helpers ------------------------------------------------------------------

def _load_audio(path: str | Path) -> tuple[np.ndarray, int]:
    data, sr = sf.read(str(path), dtype="float32")
    return data, sr

def _resample_if_needed(audio: np.ndarray, orig_sr: int) -> np.ndarray:
    if orig_sr == TARGET_SR:
        return audio
    return resample_poly(audio, TARGET_SR, orig_sr).astype(np.float32)

def _build_crossfade(length: int) -> tuple[torch.Tensor, torch.Tensor]:
    """sin² fade-in (0→1) и cos² fade-out (1→0), в overlap сумма = 1."""
    t = torch.linspace(0.0, math.pi / 2, length)
    return torch.sin(t) ** 2, torch.cos(t) ** 2

def _pad_to(tensor: torch.Tensor, n: int) -> torch.Tensor:
    deficit = n - tensor.shape[-1]
    if deficit <= 0:
        return tensor
    return torch.nn.functional.pad(tensor, (0, deficit))

@contextlib.contextmanager
def _suppress_stdout():
    with open(os.devnull, "w") as devnull:
        with contextlib.redirect_stdout(devnull):
            yield


# ---- core ---------------------------------------------------------------------

def build_model(weights_dir: str | Path, device: torch.device) -> FlashSR:
    w = Path(weights_dir)
    model = FlashSR(
        student_ldm_ckpt_path=str(w / "student_ldm.pth"),
        sr_vocoder_ckpt_path=str(w / "sr_vocoder.pth"),
        autoencoder_ckpt_path=str(w / "vae.pth"),
    )
    return model.to(device).eval()


@torch.inference_mode()
def enhance(
    model: FlashSR,
    waveform: np.ndarray,
    *,
    device: torch.device,
    lowpass: bool = False,
) -> np.ndarray:
    if waveform.ndim == 2 and waveform.shape[1] == 2:
        print("  Processing L channel ...")
        left = waveform[:, 0]
        right = waveform[:, 1]
        left_enh = enhance(model, left, device=device, lowpass=lowpass)
        print("  Processing R channel ...")
        right_enh = enhance(model, right, device=device, lowpass=lowpass)
        return np.stack([left_enh, right_enh], axis=1)

    signal = torch.from_numpy(waveform).unsqueeze(0)
    n_samples = signal.shape[-1]

    if n_samples <= WINDOW_LEN:
        chunk = _pad_to(signal, WINDOW_LEN).to(device)
        with _suppress_stdout():
            out = model(chunk, lowpass_input=lowpass)
        return out[0, :n_samples].cpu().numpy()

    total_chunks = math.ceil(n_samples / HOP)
    bar_len = 40

    fade_in, fade_out = _build_crossfade(OVERLAP)
    accumulator = torch.zeros(n_samples)
    norm = torch.zeros(n_samples)

    offset = 0
    chunk_count = 0
    start_time = time.monotonic()

    while offset < n_samples:
        end = min(offset + WINDOW_LEN, n_samples)
        segment = signal[:, offset:end]
        segment = _pad_to(segment, WINDOW_LEN).to(device)

        with _suppress_stdout():
            enhanced_seg = model(segment, lowpass_input=lowpass).cpu().squeeze(0)

        seg_len = min(WINDOW_LEN, n_samples - offset)
        enhanced_seg = enhanced_seg[:seg_len]

        is_first = offset == 0
        is_last = offset + HOP >= n_samples

        w = torch.ones(seg_len)
        if not is_first:
            n = min(OVERLAP, seg_len)
            w[:n] = fade_in[:n]
        if not is_last:
            n = min(OVERLAP, seg_len)
            w[-n:] = fade_out[:n]

        accumulator[offset : offset + seg_len] += enhanced_seg * w
        norm[offset : offset + seg_len] += w
        offset += HOP
        chunk_count += 1

        elapsed = time.monotonic() - start_time
        percent = chunk_count / total_chunks * 100
        filled = int(bar_len * chunk_count / total_chunks)
        bar = "█" * filled + "░" * (bar_len - filled)

        if chunk_count > 1:
            rate = (chunk_count - 1) / elapsed
            eta = (total_chunks - chunk_count) / rate if rate > 0 else 0
            eta_str = f" ETA: {eta:.0f}s"
        else:
            eta_str = " ETA: --"

        print(f"\r  Progress: [{bar}] {percent:.1f}% ({chunk_count}/{total_chunks}) | Time: {elapsed:.0f}s{eta_str}", end="", flush=True)

    print("  Done!          ")
    norm.clamp_(min=1e-8)
    return (accumulator / norm).numpy()


# ---- file-level ---------------------------------------------------------------

def enhance_file(
    model: FlashSR,
    src: str | Path,
    dst: str | Path,
    *,
    device: torch.device,
    lowpass: bool = False,
) -> tuple[float, int, int, float]:
    """Returns (audio_duration, original_sample_rate, output_sample_rate, speed)."""
    raw, sr = _load_audio(src)
    audio = _resample_if_needed(raw, sr)
    audio_duration = len(audio) / TARGET_SR

    start_time = time.monotonic()
    result = enhance(model, audio, device=device, lowpass=lowpass)
    elapsed = time.monotonic() - start_time

    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    temp_dst = dst.with_name(dst.stem + "_48k.wav")
    sf.write(str(temp_dst), result, TARGET_SR)

    print(f"  Resampling: 48 kHz -> 44.1 kHz ...", end="", flush=True)
    subprocess.run(
        ["ffmpeg", "-i", str(temp_dst), "-ar", str(OUTPUT_SR), str(dst), "-y"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )
    temp_dst.unlink()
    print(" Done")

    speed = audio_duration / elapsed if elapsed > 0 else 0
    return audio_duration, sr, OUTPUT_SR, speed


def collect_audio_files(root: str | Path) -> list[Path]:
    root = Path(root)
    return sorted(p for p in root.rglob("*") if p.suffix.lower() in AUDIO_EXTENSIONS)


# ---- CLI ----------------------------------------------------------------------

def cli() -> None:
    ap = argparse.ArgumentParser(
        description="FlashSR: пакетная супер-резолюция аудио (выход 44.1 kHz)")
    ap.add_argument("--input", "-i", required=True,
                    help="Входной файл или каталог")
    ap.add_argument("--output", "-o", required=True,
                    help="Выходной файл или каталог")
    ap.add_argument("--weights", "-w", default="/app/weights",
                    help="Каталог с весами .pth (default: /app/weights)")
    ap.add_argument("--lowpass", action="store_true",
                    help="Lowpass-фильтр перед enhance (для узкополосного входа)")
    ap.add_argument("--device", default="cuda",
                    help="Torch device (default: cuda)")
    args = ap.parse_args()

    dev = torch.device(args.device if torch.cuda.is_available() else "cpu")
    print(f"Device: {dev}")

    print("Loading model...")
    t0 = time.monotonic()
    model = build_model(args.weights, dev)
    print(f"Loaded in {time.monotonic() - t0:.1f}s")

    inp = Path(args.input)
    out = Path(args.output)

    if inp.is_dir():
        files = collect_audio_files(inp)
        if not files:
            sys.exit(f"No audio files found in {inp}")
        pairs = [(f, out / f.relative_to(inp).with_suffix(".wav")) for f in files]
    else:
        pairs = [(inp, out)]

    total_dur = 0.0
    t_start = time.monotonic()
    skipped_count = 0

    for idx, (src, dst) in enumerate(pairs, 1):
        temp_dst = dst.with_name(dst.stem + "_48k.wav")

        if temp_dst.exists() and not dst.exists():
            print(f"\n[{idx}/{len(pairs)}] Cleanup: removing incomplete temp file {temp_dst.name}")
            temp_dst.unlink()

        if dst.exists() and temp_dst.exists():
            print(f"\n[{idx}/{len(pairs)}] Cleanup: removing incomplete files for {dst.name}")
            dst.unlink()
            temp_dst.unlink()

        if dst.exists() and not temp_dst.exists():
            print(f"\n[{idx}/{len(pairs)}] Skip: {dst.name} (already exists)")
            skipped_count += 1
            continue

        print(f"\n[{idx}/{len(pairs)}] Processing: {src.name}")
        print("-" * 50)

        dur, orig_sr, out_sr, speed = enhance_file(model, src, dst, device=dev, lowpass=args.lowpass)
        total_dur += dur

        print(f"[{idx}/{len(pairs)}] Done: {dst.name} ({dur:.1f}s, {speed:.2f}x)")
        print(f"  Sample rate: {orig_sr} Hz -> {out_sr} Hz")

    elapsed = time.monotonic() - t_start
    avg_speed = total_dur / elapsed if elapsed > 0 else 0

    print(f"\nDone: {len(pairs)} file(s), {total_dur:.1f}s audio, "
          f"{elapsed:.1f}s wall-clock ({avg_speed:.2f}x realtime)")
    if skipped_count > 0:
        print(f"Skipped: {skipped_count} file(s) (already exist)")


if __name__ == "__main__":
    cli()
