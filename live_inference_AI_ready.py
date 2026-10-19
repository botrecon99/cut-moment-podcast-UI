"""Laugh detection with CSV outputs suitable for transcript + laughter analysis.

Uses the same VGGishEmbedder and trained Keras model as live_inference.py.
Exports ACTUAL DETECTED laughter intervals separately from OPTIONAL
context windows. Neither is a finalized funny-moment video cut.

Required: ffmpeg, numpy, scipy, tensorflow, keras, audioset, model .h5.
Run in the original project directory (where Models/ and audioset exist).
"""

import csv
import math
import os
import re
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import tensorflow as tf  # used by the model/back end
import keras
from scipy.io import wavfile
from audioset import vggish_embeddings

# --- Settings ---
BASE_DIR = Path(__file__).resolve().parent
INPUT_FOLDER = BASE_DIR / 'input_videos'
OUTPUT_FOLDER = BASE_DIR / 'output_data'
KERAS_MODEL_PATH = BASE_DIR / 'Models' / 'LSTM_SingleLayer_100Epochs.h5'
FFMPEG = 'ffmpeg'
SAMPLE_LEN = 3.0  # seconds; native time resolution is ~3 seconds
RATE = 16000
THRESHOLD = 0.90  # model score threshold, NOT laughter loudness
MAX_MERGE_GAP = 3.0  # bridge at most one non-detected 3s frame
CONTEXT_BEFORE = 30.0  # optional browsing windows only
CONTEXT_AFTER = 15.0
OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)


def safe_name(name):
    return re.sub(r'[^\w\-. ]', '_', name).strip(' .') or 'video'


def timecode(seconds):
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f'{h:02}:{m:02}:{s:02}'


def write_csv(path, headings, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=headings, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def group_laughter(predictions):
    """A group is based on detected 3-second windows, not an edited clip."""
    hits = [(start, end, score) for start, end, score in predictions
            if score >= THRESHOLD]
    events = []
    group = []

    def finish():
        if not group:
            return
        start = group[0][0]
        end = group[-1][1]
        scores = [s for _, _, s in group]
        events.append({
            'start_seconds': round(start, 3),
            'end_seconds': round(end, 3),
            'duration_seconds': round(end - start, 3),
            'peak_score': round(max(scores), 4),
            'mean_score': round(sum(scores) / len(scores), 4),
            'detected_window_count': len(scores),
            'detected_seconds': round(sum(e - s for s, e, _ in group), 3),
        })

    for start, end, score in hits:
        if group and start - group[-1][1] > MAX_MERGE_GAP:
            finish()
            group = []
        group.append((start, end, score))
    finish()
    return events


def process_one(video, model, embedder):
    stem = safe_name(video.stem)
    with tempfile.TemporaryDirectory(prefix='laugh_', dir=OUTPUT_FOLDER) as tmp:
        wav_path = Path(tmp) / 'audio.wav'
        process = subprocess.run(
            [FFMPEG, '-y', '-hide_banner', '-loglevel', 'error',
             '-i', str(video), '-vn', '-ac', '1', '-ar', str(RATE),
             '-c:a', 'pcm_s16le', str(wav_path)],
            capture_output=True, text=True
        )
        if process.returncode or not wav_path.exists():
            print(f'ERROR extracting {video.name}: {process.stderr[-500:]}')
            return
        sample_rate, samples = wavfile.read(wav_path)
        if sample_rate != RATE:
            raise ValueError(f'Expected {RATE} Hz, got {sample_rate} Hz')
        if samples.ndim > 1:
            samples = samples.mean(axis=1).astype(np.int16)

        duration = len(samples) / RATE
        chunk_size = int(SAMPLE_LEN * RATE)
        n = math.ceil(len(samples) / chunk_size)
        predictions = []
        print(f'Analyzing {video.name}: {timecode(duration)}, {n} windows')
        for i in range(n):
            start_sample = i * chunk_size
            chunk = samples[start_sample:start_sample + chunk_size]
            if len(chunk) < chunk_size:
                chunk = np.pad(chunk, (0, chunk_size - len(chunk)))
            embeddings = embedder.convert_waveform_to_embedding(chunk, RATE)
            prediction = model.predict(np.expand_dims(embeddings, axis=0), verbose=0)
            score = float(np.asarray(prediction).reshape(-1)[0])
            predictions.append((
                i * SAMPLE_LEN,
                min((i + 1) * SAMPLE_LEN, duration),
                score,
            ))

    events = group_laughter(predictions)
    event_rows = []
    candidate_rows = []
    for index, event in enumerate(events, 1):
        start = event['start_seconds']
        end = event['end_seconds']
        event_rows.append({
            'event_id': index,
            'start': timecode(start),
            'end': timecode(end),
            **event,
        })
        # Browsing suggestion only. AI should locate the real setup and payoff.
        candidate_start = max(0.0, start - CONTEXT_BEFORE)
        candidate_end = min(duration, end + CONTEXT_AFTER)
        candidate_rows.append({
            'event_id': index,
            'start': timecode(candidate_start),
            'end': timecode(candidate_end),
            'start_seconds': round(candidate_start, 3),
            'end_seconds': round(candidate_end, 3),
            'source_laugh_start': timecode(start),
            'source_laugh_end': timecode(end),
            'peak_score': event['peak_score'],
            'note': 'CONTEXT_ONLY_NOT_FINAL_CUT',
        })

    # Main CSV for AI: TRUE DETECTED EVENT TIMES (rounded to 3-s windows).
    detailed_fields = [
        'event_id', 'start', 'end', 'start_seconds', 'end_seconds',
        'duration_seconds', 'peak_score', 'mean_score',
        'detected_window_count', 'detected_seconds',
    ]
    write_csv(OUTPUT_FOLDER / f'{stem}_laughs.csv', detailed_fields, event_rows)
    write_csv(OUTPUT_FOLDER / f'{stem}_context_candidates.csv', [
        'event_id', 'start', 'end', 'start_seconds', 'end_seconds',
        'source_laugh_start', 'source_laugh_end', 'peak_score', 'note',
    ], candidate_rows)
    print(f'Found {len(events)} laughter candidate events. CSVs saved to {OUTPUT_FOLDER}')
    if not events:
        print('No events passed the threshold. This does not prove the video is unfunny.')


def main():
    videos = sorted(p for p in INPUT_FOLDER.glob('*') if p.suffix.lower() == '.mp4')
    if not videos:
        print(f'No MP4 files in {INPUT_FOLDER}')
        return
    if not KERAS_MODEL_PATH.exists():
        print(f'No model found: {KERAS_MODEL_PATH}')
        return
    print('Loading model and VGGish audio embedder...')
    model = keras.models.load_model(str(KERAS_MODEL_PATH))
    embedder = vggish_embeddings.VGGishEmbedder()
    for video in videos:
        try:
            process_one(video, model, embedder)
        except Exception as exc:
            print(f'ERROR on {video.name}: {exc}')


if __name__ == '__main__':
    main()
