---
language:
  - en
tags:
  - audio
  - super-resolution
  - speech-enhancement
  - diffusion
  - one-step
pipeline_tag: audio-to-audio
---

# FlashSR: One-step Versatile Audio Super-Resolution

> **This is a convenience redistribution, not the original repository.** All credit for the model architecture, research, training, and weights belongs to the original authors. This repository is not affiliated with or endorsed by them.

| | |
|---|---|
| **Authors** | Jaekwon Im and Juhan Nam (KAIST) |
| **Paper** | [FlashSR: One-step Versatile Audio Super-resolution via Diffusion Distillation](https://arxiv.org/abs/2501.10807) (arXiv:2501.10807) |
| **Demo** | [jakeoneijk.github.io/flashsr-demo](https://jakeoneijk.github.io/flashsr-demo/) |
| **Original code** | [jakeoneijk/FlashSR_Inference](https://github.com/jakeoneijk/FlashSR_Inference) |
| **Original weights** | [jakeoneijk/FlashSR_weights](https://huggingface.co/datasets/jakeoneijk/FlashSR_weights) |

> **Note:** There are other unrelated projects also named "FlashSR" (for other super-resolution).

## About this repository

The original code and weights are split across GitHub and Hugging Face and have dependencies (torchcodec, FFmpeg) that can be difficult to set up. This repository bundles everything into one place with a standalone inference script that only needs PyTorch, soundfile, and scipy.

**What is from the original authors:** The model code (`FlashSR/`, `TorchJaekwon/`) and the pretrained weights (`weights/`) are from the original repositories linked above.

**What is new in this redistribution:** The inference script (`enhance.py`), `setup.py`, and this README were written independently. The code in this repository (excluding model weights) is released under the **Apache License 2.0**.

## What FlashSR does

FlashSR restores high-frequency audio components in a single forward pass. It takes audio at any sample rate, resamples to 48 kHz, and reconstructs missing high-frequency detail. This is useful for:

- Upscaling low-sample-rate recordings to full bandwidth
- Enhancing audio that has been through lossy processing (codecs, vocoders, etc.)
- Post-processing TTS or voice conversion outputs

The model handles speech, music, and sound effects.

## Repository structure

```
weights/
  student_ldm.pth     (986 MB)  - Distilled latent diffusion model
  sr_vocoder.pth      (599 MB)  - Super-resolution vocoder
  vae.pth             (1.6 GB)  - Variational autoencoder
FlashSR/                        - Model code (from original repo)
TorchJaekwon/                   - Utility library (from original repo)
Assets/ExampleInput/            - Example audio files (speech, music, sound effects)
enhance.py                      - Standalone inference script
setup.py                        - Package installer
```

## Installation

**Requirements:** Python 3.10+, PyTorch 2.0+ with CUDA, ~6 GB GPU memory.

```bash
# Clone this repository
git clone https://huggingface.co/laion/FlashSR_One-step_Versatile_Audio_Super-resolution
cd FlashSR_One-step_Versatile_Audio_Super-resolution

# Install
pip install -e .
pip install einops librosa soundfile tqdm scipy
```

### Verify

```bash
python enhance.py --input Assets/ExampleInput/speech.wav --output output.wav
```

> **Tip:** If you have a conda environment with conflicting cudnn libraries, clear `LD_LIBRARY_PATH` before running: `LD_LIBRARY_PATH="" python enhance.py ...`

## Usage

### Command line

```bash
# Single file
python enhance.py --input my_audio.wav --output enhanced.wav

# Entire directory
python enhance.py --input ./audio_folder/ --output ./enhanced_folder/

# With lowpass filter (can help when input was not originally bandwidth-limited)
python enhance.py --input my_audio.wav --output enhanced.wav --lowpass

# Specify GPU
CUDA_VISIBLE_DEVICES=0 python enhance.py --input my_audio.wav --output enhanced.wav
```

### Python API

```python
import torch
import soundfile as sf
import numpy as np
from pathlib import Path
from FlashSR.FlashSR import FlashSR

WEIGHTS_DIR = Path("./weights")
WINDOW_SIZE = 245760  # 5.12 seconds at 48 kHz

# Initialize
model = FlashSR(
    student_ldm_ckpt_path=str(WEIGHTS_DIR / "student_ldm.pth"),
    sr_vocoder_ckpt_path=str(WEIGHTS_DIR / "sr_vocoder.pth"),
    autoencoder_ckpt_path=str(WEIGHTS_DIR / "vae.pth"),
)
model = model.to("cuda").eval()

# Load and prepare audio (must be mono, 48 kHz)
samples, rate = sf.read("input.wav", dtype="float32")
if samples.ndim > 1:
    samples = samples.mean(axis=1)

# The model accepts exactly 245760 samples per call.
# Pad short audio; for longer audio, see enhance.py for chunk-based processing.
waveform = torch.from_numpy(samples).unsqueeze(0)  # shape: (1, num_samples)
n = waveform.shape[-1]
if n < WINDOW_SIZE:
    waveform = torch.nn.functional.pad(waveform, (0, WINDOW_SIZE - n))

waveform = waveform.to("cuda")

with torch.no_grad():
    result = model(waveform, lowpass_input=False)

# Trim padding and save
result = result[:, :n].squeeze(0).cpu().numpy()
sf.write("output.wav", result, 48000)
```

## Notes

- **Fixed input length:** The model processes exactly 245,760 samples (5.12 seconds at 48 kHz). The `enhance.py` script handles longer audio automatically using overlapping chunks with crossfading.
- **Sample rate:** Input audio at any sample rate is resampled to 48 kHz. Output is always 48 kHz.
- **Channels:** Mono and stereo are both supported. Stereo files are processed channel-by-channel.
- **`lowpass_input` flag:** Set to `True` if your input was not originally bandwidth-limited. This applies a lowpass filter before enhancement to better match the model's training distribution.

## License

The inference script (`enhance.py`), `setup.py`, and this README are released under the [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0).

The model weights and original model code (`FlashSR/`, `TorchJaekwon/`) are from the original authors' repositories linked above. Please refer to those repositories for their licensing terms.

## Citation

If you use FlashSR in your work, please cite the original paper:

```bibtex
@article{im2025flashsr,
  title={FlashSR: One-step Versatile Audio Super-resolution via Diffusion Distillation},
  author={Im, Jaekwon and Nam, Juhan},
  journal={arXiv preprint arXiv:2501.10807},
  year={2025}
}
```

## References

- [AudioSR](https://github.com/haoheliu/versatile_audio_super_resolution)
- [NVSR](https://github.com/haoheliu/ssr_eval)
- [BigVGAN](https://github.com/NVIDIA/BigVGAN)
- [Diffusers](https://github.com/huggingface/diffusers)
