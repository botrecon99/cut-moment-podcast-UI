import os
import re
import subprocess
import numpy as np
from datetime import timedelta
from scipy.io import wavfile

import tensorflow as tf
import keras

from audioset import vggish_embeddings

# === CONFIG ===
INPUT_FOLDER = 'input_videos'
OUTPUT_FOLDER = 'output_data'
KERAS_MODEL_PATH = 'Models/LSTM_SingleLayer_100Epochs.h5'
THRESHOLD = 0.7
THRESHOLD_PEAK = 0.9
SAMPLE_LEN = 3.0
RATE = 16000
CHUNK = int(SAMPLE_LEN * RATE)

os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# === UTILITY FUNCTIONS ===
def sec_to_time(secs):
    td = timedelta(seconds=int(secs))
    total_seconds = int(td.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes}:{seconds:02}"

def score_to_title(score):
    if score >= 0.95:
        return "Audience loses it!"
    elif score >= 0.9:
        return "Epic Laugh!"
    elif score >= 0.85:
        return "Great Moment"
    else:
        return "Mildly Funny"

def sanitize_filename(name):
    return re.sub(r'[^\w\-_\. ]', '_', name)

# === MODEL LOADING ===
print("🚀 Đang tải mô hình...")
model = keras.models.load_model(KERAS_MODEL_PATH)
audio_embed = vggish_embeddings.VGGishEmbedder()

# === PROCESS EACH VIDEO ===
for filename in os.listdir(INPUT_FOLDER):
    if not filename.lower().endswith('.mp4'):
        continue

    input_path = os.path.join(INPUT_FOLDER, filename)
    name_base = os.path.splitext(filename)[0]
    sanitized_name = sanitize_filename(name_base)
    
    laugh_csv = os.path.join(OUTPUT_FOLDER, f'{sanitized_name}_laughs.csv')
    temp_wav = os.path.join(OUTPUT_FOLDER, 'temp_audio.wav')

    # === AUDIO EXTRACTION ===
    print(f"\n🎧 Đang trích xuất âm thanh từ: {filename}")
    subprocess.run([
        'ffmpeg', '-y', '-hwaccel', 'cuda', '-i', input_path,
        '-ac', '1', '-ar', str(RATE), '-vn',
        '-c:a', 'pcm_s16le', temp_wav
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if not os.path.exists(temp_wav):
        print(f"❌ Lỗi: Không thể trích xuất âm thanh từ {filename}. Vui lòng kiểm tra lại FFmpeg.")
        continue

    rate, data = wavfile.read(temp_wav)
    if data.ndim == 2:
        data = data.mean(axis=1).astype(np.int16)

    assert rate == RATE, f"Sample rate mismatch: expected {RATE}, got {rate}"

    # === LAUGHTER DETECTION ===
    num_chunks = len(data) // CHUNK
    print("🔍 Đang phân tích tìm tiếng cười...")
    results = []
    for i in range(num_chunks):
        chunk = data[i * CHUNK:(i + 1) * CHUNK]
        embeddings = audio_embed.convert_waveform_to_embedding(chunk, RATE)
        score = float(model.predict(np.expand_dims(embeddings, axis=0), verbose=0)[0, 0])
        start = round(i * SAMPLE_LEN, 2)
        end = round(start + SAMPLE_LEN, 2)
        results.append((start, end, score))

    os.remove(temp_wav)

    laugh_segments = [r for r in results if r[2] >= THRESHOLD]
    if not laugh_segments:
        print("⚠️ Không tìm thấy đoạn cười nào đạt tiêu chuẩn trong video này.")
        continue

    # === MERGE LAUGH SEGMENTS ===
    merged_segments, buffer = [], []
    for seg in laugh_segments:
        if not buffer or seg[0] - buffer[-1][1] <= 10:
            buffer.append(seg)
        else:
            merged_segments.append((buffer[0][0], buffer[-1][1], max([s[2] for s in buffer])))
            buffer = [seg]
    if buffer:
        merged_segments.append((buffer[0][0], buffer[-1][1], max([s[2] for s in buffer])))

    # === IN KẾT QUẢ VÀ LƯU CSV ===
    print(f"\n📊 KẾT QUẢ CHO VIDEO: {filename}")
    print(f"{'Bắt đầu':<10} | {'Kết thúc':<10} | {'Thời lượng':<12} | {'Điểm':<6} | {'Đánh giá'}")
    print("-" * 65)

    with open(laugh_csv, 'w', encoding='utf-8') as f:
        f.write("start,end,duration,score,title\n")
        
        for idx, (start, end, score) in enumerate(merged_segments):
            mid = (start + end) / 2
            clip_start = max(0, mid - 20)
            clip_end = mid + 5

            for s, e, sc in results:
                if clip_end <= s <= clip_end + 30 and sc >= THRESHOLD_PEAK:
                    clip_end = e
                    break

            duration = clip_end - clip_start
            title = score_to_title(score)
            
            str_start = sec_to_time(clip_start)
            str_end = sec_to_time(clip_end)
            
            print(f"{str_start:<10} | {str_end:<10} | {round(duration,1):<10}s | {round(score,3):<6} | {title}")
            f.write(f"{str_start},{str_end},{round(duration,1)},{round(score,3)},\"{title}\"\n")

    print(f"\n✅ Đã lưu danh sách vào: {laugh_csv}\n")