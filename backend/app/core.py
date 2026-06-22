from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import numpy as np
import soundfile as sf

from . import settings  # noqa: F401

TRANSFORMERS_WHISPER_MODEL_ID = os.getenv(
    "TRANSFORMERS_WHISPER_MODEL_ID",
    # 속도 우선(기본, MPS에서 ~0.2~0.3초/발화). 정확도↑: openai/whisper-medium
    # 한국어 최고 정확(but 느림 ~6초): ghost613/whisper-large-v3-turbo-korean
    "openai/whisper-small",
)


def get_runtime_device() -> str:
    return get_transformers_device()


def get_runtime_model_name() -> str:
    return TRANSFORMERS_WHISPER_MODEL_ID


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


def transcribe_audio_file(path: str | Path, *, initial_prompt: str | None = None) -> dict[str, object]:
    return transcribe_audio_file_with_options(path, initial_prompt=initial_prompt)


def transcribe_audio_file_with_options(
    path: str | Path,
    *,
    language: str | None = "ko",
    initial_prompt: str | None = None,
) -> dict[str, object]:
    return transcribe_with_transformers_whisper(
        path=path,
        language=language or "ko",
        initial_prompt=initial_prompt,
    )


def transcribe_with_transformers_whisper(
    path: str | Path,
    *,
    language: str | None = "ko",
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
    # max_new_tokens: 일부 모델의 generation_config.max_length 기본값이 작아 긴 발화가
    # 잘리므로 명시한다. Whisper 디코더 한계(448) 근처로 잡는다.
    # num_beams=1(greedy): 실시간 응답을 위해 빔서치 대신 그리디 디코딩(약 2배 빠름).
    generate_kwargs: dict[str, object] = {"max_new_tokens": 440, "num_beams": 1}

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
        try:
            # 다국어 모델(openai/whisper-*)은 한국어를 강제하면 더 안정적이다.
            predicted_ids = model.generate(
                input_features, language="ko", task="transcribe", **generate_kwargs
            )
        except Exception:
            # 일부 파인튜닝 모델은 generation_config가 language 인자와 호환되지 않음 → 생략
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

    import librosa
    return librosa.resample(audio, orig_sr=source_rate, target_sr=target_rate)
