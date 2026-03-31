from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import evaluate
import librosa
import numpy as np
import torch
from datasets import Audio, DatasetDict, load_dataset
from PIL import Image
from transformers import (
    Seq2SeqTrainer,
    Seq2SeqTrainingArguments,
    WhisperForConditionalGeneration,
    WhisperProcessor,
)


DEFAULT_MODEL = "openai/whisper-small"
DEFAULT_DATASET = "kresnik/zeroth_korean"


@dataclass
class DataCollatorSpeechSeq2SeqWithPadding:
    processor: WhisperProcessor

    def __call__(self, features: list[dict[str, Any]]) -> dict[str, torch.Tensor]:
        input_features = [{"input_features": feature["input_features"]} for feature in features]
        batch = self.processor.feature_extractor.pad(input_features, return_tensors="pt")

        label_features = [{"input_ids": feature["labels"]} for feature in features]
        labels_batch = self.processor.tokenizer.pad(label_features, return_tensors="pt")
        labels = labels_batch["input_ids"].masked_fill(labels_batch.attention_mask.ne(1), -100)

        if (labels[:, 0] == self.processor.tokenizer.bos_token_id).all().item():
            labels = labels[:, 1:]

        batch["labels"] = labels
        return batch


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-id", default=DEFAULT_MODEL)
    parser.add_argument("--dataset-id", default=DEFAULT_DATASET)
    parser.add_argument("--output-dir", default="training_runs/korean_asr_small")
    parser.add_argument("--max-train-samples", type=int, default=512)
    parser.add_argument("--max-eval-samples", type=int, default=64)
    parser.add_argument("--max-steps", type=int, default=80)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--grad-accum", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=5e-6)
    parser.add_argument("--num-workers", type=int, default=1)
    parser.add_argument("--audio-diagnostics", action="store_true")
    parser.add_argument("--diagnostic-samples-per-split", type=int, default=4)
    parser.add_argument("--freeze-encoder", action="store_true", default=True)
    parser.add_argument("--no-freeze-encoder", dest="freeze_encoder", action="store_false")
    return parser.parse_args()


def get_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def prepare_datasets(
    dataset: DatasetDict,
    processor: WhisperProcessor,
    num_workers: int,
) -> tuple[DatasetDict, int]:
    def prepare_batch(batch: dict[str, Any]) -> dict[str, Any]:
        audio = batch["audio"]
        batch["input_features"] = processor.feature_extractor(
            audio["array"], sampling_rate=audio["sampling_rate"]
        ).input_features[0]
        batch["labels"] = processor.tokenizer(batch["text"]).input_ids
        return batch

    vectorized = dataset.map(
        prepare_batch,
        remove_columns=dataset["train"].column_names,
        num_proc=None if num_workers <= 1 else num_workers,
    )
    return vectorized, len(dataset["train"])


def spectrogram_to_image(audio: np.ndarray, sample_rate: int, mel: bool) -> Image.Image:
    n_fft = 512
    hop = 160
    if mel:
        spec = librosa.feature.melspectrogram(
            y=audio,
            sr=sample_rate,
            n_fft=n_fft,
            hop_length=hop,
            n_mels=80,
            power=2.0,
        )
        db = librosa.power_to_db(spec, ref=np.max)
    else:
        stft = librosa.stft(y=audio, n_fft=n_fft, hop_length=hop)
        db = librosa.amplitude_to_db(np.abs(stft), ref=np.max)

    db = np.nan_to_num(db, nan=-80.0, neginf=-80.0, posinf=0.0)
    db = np.clip(db, -80.0, 0.0)
    normalized = ((db + 80.0) / 80.0 * 255.0).astype(np.uint8)[::-1, :]
    return Image.fromarray(normalized, mode="L")


def export_audio_diagnostics(
    raw_dataset: DatasetDict,
    output_dir: Path,
    samples_per_split: int,
) -> None:
    diagnostics_dir = output_dir / "diagnostics"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {}

    for split_name in ("train", "validation"):
        if split_name not in raw_dataset:
            continue
        split = raw_dataset[split_name]
        total = len(split)
        inspect_count = min(max(samples_per_split, 1), total)
        if inspect_count < 1:
            continue

        split_dir = diagnostics_dir / split_name
        split_dir.mkdir(parents=True, exist_ok=True)

        durations = []
        rms_values = []
        peak_values = []
        sample_rate_hist: dict[str, int] = {}
        sample_rows: list[dict[str, Any]] = []

        for index in range(inspect_count):
            row = split[index]
            audio_data = row["audio"]["array"].astype(np.float32)
            sample_rate = int(row["audio"]["sampling_rate"])
            duration = float(len(audio_data) / max(sample_rate, 1))
            peak = float(np.max(np.abs(audio_data)) + 1e-8)
            rms = float(np.sqrt(np.mean(np.square(audio_data))) + 1e-8)

            durations.append(duration)
            peak_values.append(peak)
            rms_values.append(rms)
            sample_rate_hist[str(sample_rate)] = sample_rate_hist.get(str(sample_rate), 0) + 1

            wav_img = spectrogram_to_image(audio_data, sample_rate, mel=False)
            mel_img = spectrogram_to_image(audio_data, sample_rate, mel=True)
            wav_img.save(split_dir / f"sample_{index:02d}_spectrogram.png")
            mel_img.save(split_dir / f"sample_{index:02d}_mel.png")

            sample_rows.append(
                {
                    "index": index,
                    "duration_sec": round(duration, 4),
                    "sample_rate": sample_rate,
                    "peak": round(peak, 6),
                    "rms": round(rms, 6),
                    "text_preview": str(row.get("text", ""))[:120],
                }
            )

        report[split_name] = {
            "total_rows": total,
            "inspected_rows": inspect_count,
            "duration_sec": {
                "mean": float(np.mean(durations)),
                "min": float(np.min(durations)),
                "max": float(np.max(durations)),
            },
            "peak": {
                "mean": float(np.mean(peak_values)),
                "max": float(np.max(peak_values)),
            },
            "rms": {
                "mean": float(np.mean(rms_values)),
                "min": float(np.min(rms_values)),
                "max": float(np.max(rms_values)),
            },
            "sample_rate_histogram": sample_rate_hist,
            "samples": sample_rows,
        }

    with (diagnostics_dir / "summary.json").open("w", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)


def main() -> None:
    args = parse_args()
    device = get_device()

    processor = WhisperProcessor.from_pretrained(args.model_id)
    model = WhisperForConditionalGeneration.from_pretrained(args.model_id)
    model.generation_config.language = "ko"
    model.generation_config.task = "transcribe"
    model.config.use_cache = False
    if args.freeze_encoder:
        model.freeze_encoder()
    model.to(device)

    raw_dataset = load_dataset(args.dataset_id)
    raw_dataset = raw_dataset.cast_column("audio", Audio(sampling_rate=16000))
    if "validation" not in raw_dataset and "test" in raw_dataset:
        raw_dataset["validation"] = raw_dataset["test"]
    if args.max_train_samples:
        raw_dataset["train"] = raw_dataset["train"].select(
            range(min(args.max_train_samples, len(raw_dataset["train"])))
        )
    if args.max_eval_samples and "validation" in raw_dataset:
        raw_dataset["validation"] = raw_dataset["validation"].select(
            range(min(args.max_eval_samples, len(raw_dataset["validation"])))
        )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.audio_diagnostics:
        export_audio_diagnostics(
            raw_dataset=raw_dataset,
            output_dir=output_dir,
            samples_per_split=args.diagnostic_samples_per_split,
        )

    dataset, train_count = prepare_datasets(
        dataset=raw_dataset,
        processor=processor,
        num_workers=args.num_workers,
    )

    wer_metric = evaluate.load("wer")
    data_collator = DataCollatorSpeechSeq2SeqWithPadding(processor=processor)

    def compute_metrics(pred):
        pred_ids = pred.predictions
        label_ids = pred.label_ids
        label_ids[label_ids == -100] = processor.tokenizer.pad_token_id

        pred_str = processor.batch_decode(pred_ids, skip_special_tokens=True)
        label_str = processor.batch_decode(label_ids, skip_special_tokens=True)
        wer = 100 * wer_metric.compute(predictions=pred_str, references=label_str)
        return {"wer": wer}

    training_args = Seq2SeqTrainingArguments(
        output_dir=str(output_dir),
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.learning_rate,
        warmup_steps=2,
        max_steps=args.max_steps,
        evaluation_strategy="steps",
        eval_steps=max(args.max_steps // 2, 1),
        save_steps=args.max_steps,
        logging_steps=1,
        predict_with_generate=True,
        generation_max_length=128,
        fp16=device == "cuda",
        bf16=False,
        gradient_checkpointing=device != "cpu",
        dataloader_num_workers=0,
        report_to=[],
        remove_unused_columns=False,
        push_to_hub=False,
        save_total_limit=1,
        load_best_model_at_end=False,
    )

    trainer = Seq2SeqTrainer(
        args=training_args,
        model=model,
        train_dataset=dataset["train"],
        eval_dataset=dataset["validation"],
        data_collator=data_collator,
        tokenizer=processor.feature_extractor,
        compute_metrics=compute_metrics,
    )

    print(f"device={device}")
    print(f"model={args.model_id}")
    print(f"dataset={args.dataset_id}")
    print(f"train_samples={train_count}")
    print(f"freeze_encoder={args.freeze_encoder}")
    trainer.train()
    trainer.save_model()
    processor.save_pretrained(output_dir)


if __name__ == "__main__":
    main()
