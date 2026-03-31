from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import numpy as np
import soundfile as sf
from faster_whisper import WhisperModel

from . import settings  # noqa: F401

ASR_BACKEND = os.getenv("ASR_BACKEND", "transformers-whisper")
MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "distil-large-v3")
MODEL_DEVICE = os.getenv("WHISPER_DEVICE", "auto")
COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "auto")
TRANSFORMERS_WHISPER_MODEL_ID = os.getenv(
    "TRANSFORMERS_WHISPER_MODEL_ID",
    "ghost613/whisper-large-v3-turbo-korean",
)


@lru_cache
def get_model() -> WhisperModel:
    device = MODEL_DEVICE
    compute_type = COMPUTE_TYPE

    if device == "auto":
        try:
            return WhisperModel(MODEL_SIZE, device="cuda", compute_type="float16")
        except Exception:
            device = "cpu"
            compute_type = "int8"

    return WhisperModel(MODEL_SIZE, device=device, compute_type=compute_type)


def get_runtime_device() -> str:
    if ASR_BACKEND == "transformers-whisper":
        return get_transformers_device()
    return str(get_model().model.device)


def get_runtime_model_name() -> str:
    if ASR_BACKEND == "transformers-whisper":
        return TRANSFORMERS_WHISPER_MODEL_ID
    return MODEL_SIZE


@lru_cache
def get_transformers_components():
    import torch
    from transformers import WhisperForConditionalGeneration, WhisperProcessor

    model_id = TRANSFORMERS_WHISPER_MODEL_ID
    processor = WhisperProcessor.from_pretrained(model_id)

    device = get_transformers_device()
    dtype = torch.float16 if device == "cuda" else torch.float32
    model = WhisperForConditionalGeneration.from_pretrained(model_id, torch_dtype=dtype)
    model.to(device)
    model.eval()
    return processor, model


def get_transformers_device() -> str:
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def find_best_match(script: str, recognized_text: str) -> tuple[int, str, float]:
    normalized_script = " ".join(script.split())
    normalized_recognized = " ".join(recognized_text.lower().split())
    script_lower = normalized_script.lower()

    if not normalized_recognized or not normalized_script:
        return 0, normalized_script[:120], 0.0

    best_index = 0
    best_score = 0.0
    tokens = normalized_recognized.split()
    window = max(len(normalized_recognized), 30)

    for start in range(0, max(len(script_lower) - window + 1, 1), 4):
        chunk = script_lower[start : start + window]
        overlap = sum(1 for token in tokens if token in chunk)
        score = overlap / max(len(tokens), 1)
        if score > best_score:
            best_score = score
            best_index = start

    preview = normalized_script[best_index : best_index + 160]
    return best_index, preview, round(best_score, 3)


def find_progressive_match(
    script: str,
    recognized_text: str,
    current_index: int,
    backtrack_chars: int = 220,
) -> tuple[int, str, float]:
    search_start = max(0, current_index - backtrack_chars)
    relative_index, preview, confidence = find_best_match(
        script[search_start:], recognized_text
    )
    return search_start + relative_index, preview, confidence


def transcribe_audio_file(path: str | Path) -> dict[str, object]:
    return transcribe_audio_file_with_options(path)


def transcribe_audio_file_with_options(
    path: str | Path,
    *,
    language: str | None = None,
    beam_size: int = 3,
    best_of: int = 3,
    vad_filter: bool = True,
    condition_on_previous_text: bool = True,
    initial_prompt: str | None = None,
) -> dict[str, object]:
    if ASR_BACKEND == "transformers-whisper":
        return transcribe_with_transformers_whisper(
            path=path,
            language=language,
            initial_prompt=initial_prompt,
        )

    segments, info = get_model().transcribe(
        str(path),
        beam_size=beam_size,
        best_of=best_of,
        language=language,
        vad_filter=vad_filter,
        condition_on_previous_text=condition_on_previous_text,
        initial_prompt=initial_prompt,
        temperature=0.0,
        word_timestamps=False,
    )
    text = " ".join(segment.text.strip() for segment in segments).strip()
    return {
        "text": text,
        "language": info.language,
        "duration": info.duration,
    }


def transcribe_with_transformers_whisper(
    path: str | Path,
    *,
    language: str | None = None,
    initial_prompt: str | None = None,
) -> dict[str, object]:
    import torch

    processor, model = get_transformers_components()
    audio, sample_rate = sf.read(str(path))
    audio = prepare_audio_array(audio, sample_rate, processor.feature_extractor.sampling_rate)

    inputs = processor(
        audio,
        sampling_rate=processor.feature_extractor.sampling_rate,
        return_tensors="pt",
    )

    input_features = inputs.input_features.to(model.device)
    generate_kwargs: dict[str, object] = {
        "task": "transcribe",
    }
    if language:
        generate_kwargs["language"] = language

    if initial_prompt:
        tokenizer = processor.tokenizer
        get_prompt_ids = getattr(tokenizer, "get_prompt_ids", None)
        if callable(get_prompt_ids):
            try:
                prompt_ids = get_prompt_ids(initial_prompt, return_tensors="pt")
                if prompt_ids is not None:
                    generate_kwargs["prompt_ids"] = prompt_ids.to(model.device)
            except Exception:
                pass

    with torch.no_grad():
        predicted_ids = model.generate(input_features, **generate_kwargs)

    text = processor.batch_decode(predicted_ids, skip_special_tokens=True)[0].strip()
    duration = round(len(audio) / processor.feature_extractor.sampling_rate, 3)
    return {
        "text": text,
        "language": language or "ko",
        "duration": duration,
    }


def prepare_audio_array(
    audio: np.ndarray,
    source_rate: int,
    target_rate: int,
) -> np.ndarray:
    if audio.ndim > 1:
        audio = audio.mean(axis=1)

    audio = audio.astype(np.float32)
    if source_rate == target_rate:
        return audio

    duration = len(audio) / max(source_rate, 1)
    target_length = max(int(duration * target_rate), 1)
    source_positions = np.linspace(0.0, 1.0, num=len(audio), endpoint=False)
    target_positions = np.linspace(0.0, 1.0, num=target_length, endpoint=False)
    return np.interp(target_positions, source_positions, audio).astype(np.float32)
