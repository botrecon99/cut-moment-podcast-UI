"""Parallel Laugh Studio V3: local browser UI, stdlib HTTP API.

Uses the original background ingest/detect workers and the ORIGINAL FFmpeg
renderer in tai_chi_cat.py. The web UI edits approved kept ranges, then
renders chapter clips from the cached per-job source without downloading.
"""
import csv
import hashlib
import array
from datetime import datetime
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
import traceback
import webbrowser
import time
from license_client import LicenseClient
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from pipeline_common import ROOT, JOBS, DONE, INBOX, config, jobs, status, video_file, mark_url_done, note, write_json_atomic
from pipeline_manager import launch_workers, close_workers, configure_render, open_folder, WORKERS
import tai_chi_cat as cutter
from subtitle_youtube import parse_youtube_id, source_for_job, list_languages, fetch_caption, cookie_status

HOST = '127.0.0.1'
PORT = 8765
HTML = ROOT / 'ui' / 'index.html'
LICENSE_HTML = ROOT / 'ui' / 'license.html'
LICENSE = LicenseClient()
WORKERS_LOCK = threading.RLock()
WORKERS_ENABLED = False
ACTIVE_RENDERS = set()
RENDER_LOCK = threading.RLock()
RENDER_EXEC_LOCK = threading.Lock()  # cutter.py uses global working paths; render one job at a time
MAX_REQUEST = 12 * 1024 * 1024
THUMB_LIMIT = threading.BoundedSemaphore(2)


# Cached audio peak envelope. UI can render a waveform at any zoom without re-decoding.
WAVE_LOCKS = {}
WAVE_LOCK_GUARD = threading.Lock()

def get_waveform(folder, meta):
    movie = video_file(folder, meta)
    if movie is None:
        raise ValueError('Không tìm thấy video để trích sóng âm')
    movie_stat = movie.stat()
    key = {'size': movie_stat.st_size, 'mtime_ns': movie_stat.st_mtime_ns}
    output = folder / '.waveform.v1.json'
    with WAVE_LOCK_GUARD:
        lock = WAVE_LOCKS.setdefault(str(folder), threading.Lock())
    with lock:
        if output.exists():
            try:
                data = json.loads(output.read_text(encoding='utf-8'))
                if data.get('source') == key and isinstance(data.get('peaks'), list):
                    return data
            except (OSError, ValueError, TypeError):
                pass
        duration = probe_length(folder, meta)
        if duration > 8 * 3600:
            raise ValueError('Video dài hơn 8 giờ: tạm bỏ qua waveform')
        ffmpeg = str(cutter.FFMPEG)
        if not shutil.which(ffmpeg) and not Path(ffmpeg).is_file():
            raise ValueError('Không tìm thấy FFmpeg để lấy sóng âm')
        # 1000 mono samples/sec. Limit envelope to 120K bins, reasonably sharp when zooming.
        sample_rate = 1000
        bins = max(1, min(120000, round(duration * 45)))
        command = [ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error',
                   '-i', str(movie), '-vn', '-ac', '1', '-ar', str(sample_rate),
                   '-f', 's16le', '-c:a', 'pcm_s16le', 'pipe:1']
        try:
            proc = subprocess.run(command, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, timeout=180)
        except subprocess.TimeoutExpired:
            raise ValueError('Tạo waveform quá lâu, hãy thử video ngắn hơn')
        if proc.returncode != 0:
            msg = proc.stderr.decode('utf-8', errors='replace')[-280:]
            raise ValueError('FFmpeg không trích được audio: ' + msg)
        raw = proc.stdout
        values = array.array('h')
        values.frombytes(raw[:len(raw) - len(raw) % 2])
        count = len(values)
        if not count:
            peaks = []
        else:
            peaks = [0] * bins
            for i, v in enumerate(values):
                idx = min(bins - 1, i * bins // count)
                level = abs(v)
                if level > peaks[idx]:
                    peaks[idx] = level
            peaks = [round(p / 32768, 3) for p in peaks]
        data = {'ok': True, 'source': key, 'duration': duration,
                'interval': duration / bins, 'peaks': peaks}
        temp = output.with_name(output.name + '.tmp')
        temp.write_text(json.dumps(data, separators=(',', ':')), encoding='utf-8')
        os.replace(str(temp), str(output))
        return data


def generate_thumbnail(folder, meta, time_value):
    movie = video_file(folder, meta)
    if not movie or not movie.is_file():
        raise ValueError('Không tìm thấy video cache')
    length = probe_length(folder, meta)
    seconds = max(0, min(float(time_value), max(0,length-0.12)))
    # Round to 3-second intervals so pans/zooms reuse the same cached images.
    seconds = int(seconds // 3)*3
    cache = folder / '.thumb_cache'
    cache.mkdir(exist_ok=True)
    target = cache / ('thumb_%07d.jpg' % seconds)
    if target.is_file() and target.stat().st_size > 200:
        return target
    ffmpeg = str(cutter.FFMPEG)
    temp = cache / ('thumb_%07d_%s.jpg' % (seconds, threading.get_ident()))
    args = [ffmpeg,'-nostdin','-hide_banner','-loglevel','error',
            '-ss',str(seconds),'-i',str(movie),'-an','-frames:v','1',
            '-vf','scale=180:102:force_original_aspect_ratio=decrease,pad=180:102:(ow-iw)/2:(oh-ih)/2',
            '-q:v','6','-y',str(temp)]
    with THUMB_LIMIT:
        if target.is_file() and target.stat().st_size > 200:
            return target
        try:
            proc = subprocess.run(args, stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=22)
            if proc.returncode or not temp.is_file() or temp.stat().st_size < 100:
                raise RuntimeError('Không tạo được thumbnail; kiểm tra FFmpeg: '+proc.stderr.decode('utf-8',errors='replace')[-300:])
            os.replace(str(temp),str(target))
        finally:
            if temp.is_file():temp.unlink()
    # Cache constrained to roughly 250 * 3s images; keep current thumbnails usable.
    pictures=list(cache.glob('thumb_*.jpg'))
    if len(pictures)>260:
        for old in sorted(pictures,key=lambda p:p.stat().st_mtime)[:len(pictures)-240]:
            try:old.unlink()
            except OSError:pass
    return target


def find_job(job_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]{4,100}', str(job_id)):
        raise ValueError('Invalid job ID')
    for folder, meta in jobs():
        if folder.name == job_id:
            return folder, meta
    raise ValueError('Không tìm thấy job')


def probe_length(folder, meta):
    movie = video_file(folder, meta)
    if movie is None:
        raise ValueError('Không tìm thấy video gốc của job')
    length = cutter.get_video_duration(movie)
    if length is None or length <= 0:
        raise ValueError('Không đọc được thời lượng video. Kiểm tra ffprobe.')
    return round(length, 3)


def get_laughs(folder):
    p = folder / 'laughs.csv'
    if not p.is_file():
        return []
    result = []
    try:
        with p.open('r', encoding='utf-8-sig', newline='') as f:
            for row in csv.DictReader(f):
                try:
                    result.append({'start':float(row['start_seconds']), 'end':float(row['end_seconds']),
                                   'score':float(row.get('peak_score') or 0)})
                except (ValueError, KeyError):
                    pass
                if len(result) >= 20000:
                    break
    except OSError:
        return []
    return result


def parse_video_time(value, duration, *, is_end=False):
    """HH:MM:SS(.mmm), MM:SS(.mmm), numeric seconds or END for clip end."""
    if isinstance(value, bool):
        raise ValueError('Mốc thời gian không được là true/false')
    if isinstance(value, (int, float)):
        seconds = float(value)
    elif isinstance(value, str):
        token = value.strip()
        if is_end and token.upper() == 'END':
            return duration
        parts = token.split(':')
        if len(parts) not in (2, 3):
            raise ValueError('Mốc %r cần dạng HH:MM:SS, ví dụ 00:10:16' % value)
        try:
            nums = [float(v) for v in parts]
        except ValueError:
            raise ValueError('Thời gian không hợp lệ: %r' % value)
        if len(parts) == 3:
            if not nums[0].is_integer() or not nums[1].is_integer() or not 0<=nums[1]<60:
                raise ValueError('Giờ/phút không hợp lệ: %r' % value)
        if nums[-1] >= 60 or nums[-1] < 0:
            raise ValueError('Giây không hợp lệ: %r' % value)
        seconds = nums[-1] + nums[-2] * 60 + (nums[0]*3600 if len(nums) == 3 else 0)
    else:
        raise ValueError('Mốc thời gian phải là chuỗi hoặc số')
    if not (float('-inf') < seconds < float('inf')):
        raise ValueError('Mốc thời gian không hữu hạn')
    if seconds < 0 or seconds > duration + 0.01:
        raise ValueError('Mốc %.3fs vượt ngoài video (%.3fs)' % (seconds, duration))
    return round(min(seconds, duration), 3)


def clips_from_json(text, length):
    """Simpler AI output: KEEP-only clips, each with explicit source segments."""
    raw = text.strip()
    if raw.startswith('```'):
        raw = re.sub(r'^```(?:json)?\s*\n?', '', raw, flags=re.I)
        raw = re.sub(r'\n?```\s*$', '', raw).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError('JSON không hợp lệ ở dòng %s, cột %s: %s' % (exc.lineno, exc.colno, exc.msg))
    if not isinstance(data, dict):
        raise ValueError('JSON phải là object chứa key "clips"')
    if data.get('format') not in (None, 'parallel-laugh-v1'):
        raise ValueError('JSON format phải là "parallel-laugh-v1"')
    raw_clips = data.get('clips')
    if not isinstance(raw_clips, list):
        raise ValueError('JSON cần key clips là một danh sách')
    if len(raw_clips) > 1000:
        raise ValueError('Tối đa 1000 clips')
    clips = []
    for i, item in enumerate(raw_clips, 1):
        if not isinstance(item, dict):
            raise ValueError('Clip %s không phải JSON object' % i)
        title = item.get('title')
        if not isinstance(title, str) or not title.strip():
            raise ValueError('Clip %s thiếu title' % i)
        title = title.strip()
        if len(title) > 250:
            raise ValueError('Clip %s: title dài hơn 250 ký tự' % i)
        segments = item.get('segments')
        if not isinstance(segments, list) or not segments:
            raise ValueError('Clip %s thiếu segments [[start, end]]' % i)
        if len(segments) > 200:
            raise ValueError('Clip %s có quá nhiều segments' % i)
        parsed=[]
        for j, pair in enumerate(segments,1):
            if not isinstance(pair,list) or len(pair)!=2:
                raise ValueError('Clip %s segment %s phải là ["HH:MM:SS", "HH:MM:SS"]' % (i,j))
            a=parse_video_time(pair[0],length)
            b=parse_video_time(pair[1],length,is_end=True)
            if b-a < cutter.MIN_KEEP_SECONDS - 0.0001:
                raise ValueError('Clip %s segment %s: END phải sau START ít nhất 0.2 giây' % (i,j))
            parsed.append([a,b])
        clips.append({'id':'clip_%03d'%i,'title':title,'approved':False,'segments':parsed})
    return {'version':3,'duration':length,'clips':clips,'ai_original':text,'source_format':'json'}


def normalize_legacy_chapter_layout(text):
    """Accept old CUT/DONE/CHAPTER/DONE even pasted without newlines."""
    # Split before each timestamp + pipe + [CATEGORY] chapter marker.
    # In the old one-line format those markers are the only reliable boundaries.
    return re.sub(r'(?<![\w:])(?=\d{1,4}:\d{2}(?::\d{2})?\s*\|\s*\[[A-Za-z][A-Za-z_0-9]*\])',
                  '\n', text)


def clips_from_ai(ai_text, folder, meta, length):
    from pipeline_manager import parse_ai
    # Primary format is KEEP-only JSON; preserve old CUT + DONE + CHAPTER + DONE.
    source = ai_text.lstrip()
    if source.startswith('{') or source.startswith('```json') or source.startswith('```JSON'):
        return clips_from_json(ai_text, length)
    action, cuts, keep_all, chapters = parse_ai(normalize_legacy_chapter_layout(ai_text))
    if action == 'skip':
        raise ValueError('AI trả về SKIP: không có clip để cắt')
    # Parse original CUT + CHAPTER but allow multiple source ranges inside one
    # chapter. Console validation requires a CHAPTER at every kept-range start;
    # that prevents legitimate setup->punchline edits with an internal trim.
    for start, end in cuts:
        if start >= length or (end != float('inf') and end > length + 3):
            raise ValueError('CUT vượt quá thời lượng video')
    removed = [] if keep_all else cutter.normalize_cut_ranges(cuts,length)
    kept = [(0.0,length)] if keep_all else cutter.build_keep_ranges(removed,length)
    if not kept:
        raise ValueError('CUT đã xóa toàn bộ video')
    normchapters = cutter.normalize_chapters(chapters,length)
    if len(normchapters)!=len(chapters):
        raise ValueError('CHAPTER trùng mốc hoặc nằm ngoài thời lượng video')
    if abs(normchapters[0][0]-kept[0][0])>.15:
        raise ValueError('CHAPTER đầu phải bắt đầu tại đoạn KEEP đầu tiên')
    for t, name in normchapters:
        if not any(a<=t<b for a,b in kept):
            raise ValueError('CHAPTER ở trong một đoạn CUT: '+cutter.seconds_to_timestamp(t))
    specs=cutter.build_clip_specs(kept,normchapters,length,meta['title'])
    if not specs:
        raise ValueError('Không có clip phù hợp để xuất')
    found = []
    for i, spec in enumerate(specs, 1):
        found.append({'id':'clip_%03d'%i, 'title':spec['title'],
                      'approved':False, 'segments':[[round(float(s),3),round(float(e),3)]
                                                    for s,e in spec['source_ranges']]})
    return {'version':2, 'duration':length, 'clips':found, 'ai_original':ai_text}


def validated_plan(obj, length, require_approved=False):
    """Strictly validate user-edited ranges before persisting AND before render.

    Unlike raw CUT/CHAPTER strings, arbitrary edited clips have their own ranges.
    A single chapter can retain multiple source segments separated by removed gaps.
    """
    input_clips = obj.get('clips')
    if not isinstance(input_clips, list) or len(input_clips)>1000:
        raise ValueError('Danh sách clip không hợp lệ')
    out = []
    all_intervals = []
    seen_ids = set()
    for idx, item in enumerate(input_clips):
        if not isinstance(item, dict):
            raise ValueError('Clip không hợp lệ')
        cid = str(item.get('id') or ('clip_%03d'%(idx+1)))[:80]
        if cid in seen_ids:
            raise ValueError('Clip ID trùng nhau')
        seen_ids.add(cid)
        title = str(item.get('title') or '').strip()
        # Atomic clips still retain the old 250-character limit. A merged clip
        # concatenates the full original titles and may be much longer.
        title_limit = 52000 if 'merge_sources' in item else 250
        if not title or len(title)>title_limit:
            raise ValueError('Clip %s: tiêu đề trống/quá dài (tối đa %s ký tự)'%(cid,title_limit))
        segments = item.get('segments')
        if not isinstance(segments,list) or len(segments)<1 or len(segments)>200:
            raise ValueError('Clip %s: không có đoạn hợp lệ'%cid)
        pairs=[]
        for pair in segments:
            if not isinstance(pair,list) or len(pair)!=2:
                raise ValueError('Sai cặp mốc của clip %s'%cid)
            try:
                s,e = float(pair[0]), float(pair[1])
            except (ValueError, TypeError):
                raise ValueError('Mốc không phải số')
            if not (0<=s<e<=length+0.01) or (e-s)<cutter.MIN_KEEP_SECONDS-0.0001:
                raise ValueError('Clip %s: mốc không hợp lệ (%s - %s)'%(cid,s,e))
            s,e=round(s,3),min(round(e,3), length)
            pairs.append([s,e]); all_intervals.append((s,e,cid))
        if any(pairs[i][1] > pairs[i+1][0] + 0.005 for i in range(len(pairs)-1)):
            raise ValueError('Các đoạn trong clip phải theo thứ tự, không chồng nhau')
        normalized = {'id':cid, 'title':title, 'approved':item.get('approved') is True, 'segments':pairs}
        # V3.7.3: preserve atomic originals in review_plan.json for Alt+G unmerge.
        # Render reads only normalized['segments']; merge_sources is history data.
        if 'merge_sources' in item:
            sources = item['merge_sources']
            if not isinstance(sources, list) or not (2 <= len(sources) <= 200):
                raise ValueError('Clip %s: dữ liệu ghép không hợp lệ' % cid)
            if any(not isinstance(src, dict) or 'merge_sources' in src for src in sources):
                raise ValueError('Clip %s: chỉ lưu các clip gốc khi ghép' % cid)
            restored = validated_plan({'clips':sources}, length)
            for original, clean in zip(sources, restored):
                number = original.get('merge_order')
                if not isinstance(number, int) or isinstance(number, bool) or number < 1 or number > 1000000:
                    raise ValueError('Clip %s: số thứ tự clip gốc không hợp lệ' % cid)
                clean['merge_order'] = number
            normalized['merge_sources'] = restored
        out.append(normalized)
    # Prohibit accidental overlap of any ranges in the editor, approved or not.
    all_intervals.sort()
    for a,b in zip(all_intervals,all_intervals[1:]):
        if a[1] > b[0] + 0.005:
            raise ValueError('Hai clip chồng mốc: %s và %s'%(a[2],b[2]))
    if require_approved and not any(c['approved'] for c in out):
        raise ValueError('Hãy tick duyệt ít nhất một clip trước khi cắt')
    return out


def plan_file(folder):
    return folder / 'review_plan.json'


def read_plan(folder):
    p = plan_file(folder)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def make_specs(clips):
    specs=[]
    for clip in clips:
        if not clip['approved']:
            continue
        ranges=[(float(s),float(e)) for s,e in clip['segments']]
        specs.append({'global_index':len(specs)+1, 'title':clip['title'],
                      'source_ranges':ranges, 'kept_duration':sum(e-s for s,e in ranges),
                      'start':ranges[0][0], 'end':ranges[-1][1]})
    return specs


# Studio-specific safe file publishing: do not modify the legacy cutter module.
# Windows legacy MAX_PATH is 260 UTF-16 code units, including the trailing NUL.
# Reserve ample margin for directory moves, filesystem differences and suffixes.
EXPORT_PATH_LIMIT = 240


def _windows_units(value):
    return len(str(value).encode('utf-16-le')) // 2


def _short_utf16(value, max_units):
    out = []
    units = 0
    for char in value:
        need = 2 if ord(char) > 0xffff else 1
        if units + need > max_units:
            break
        out.append(char)
        units += need
    return ''.join(out)


def _safe_path_component(value, max_units):
    """Keep a readable prefix and hash when truncated, avoiding collisions."""
    safe = cutter.clean_filename(value)
    if _windows_units(safe) <= max_units:
        return safe
    stamp = '-' + hashlib.sha1(str(value).encode('utf-8')).hexdigest()[:8]
    if max_units <= _windows_units(stamp) + 5:
        raise OSError('Đường dẫn quá sâu. Hãy chuyển LaughDetectionV2 về thư mục ngắn hơn, ví dụ D:\\LaughDetectionV2')
    head = _short_utf16(safe, max_units - _windows_units(stamp)).rstrip(' .-_')
    return head + stamp


def move_segments_to_done_safe(rendered_clip_files, clip_specs, original_title):
    """Export short Windows-safe MP4 filenames, preserving full titles in manifest.

    Called only by Studio so existing CLI cutter behavior remains unchanged.
    """
    if not rendered_clip_files or len(rendered_clip_files) != len(clip_specs):
        raise RuntimeError('Không đủ clip đã render để lưu.')

    base = cutter.DONE_DIR
    # Reserve 70 UTF-16 units for each filename even for very long video titles.
    reserve_name = 70
    suffix = ' - clips'
    budget_dir = EXPORT_PATH_LIMIT - _windows_units(base) - 1 - _windows_units(suffix) - 1 - reserve_name
    folder_title = _safe_path_component(original_title, min(180, budget_dir))
    out_dir = base / (folder_title + suffix)
    out_dir.mkdir(parents=True, exist_ok=True)

    output_files = []
    for rendered_file, spec in zip(rendered_clip_files, clip_specs):
        number = f'{int(spec["global_index"]):02d} - '
        extension = '.mp4'
        budget_file = (EXPORT_PATH_LIMIT - _windows_units(out_dir) - 1
                       - _windows_units(number) - _windows_units(extension))
        short_title = _safe_path_component(spec['title'], min(180, budget_file))
        filename = number + short_title + extension
        target = out_dir / filename
        if _windows_units(target) > EXPORT_PATH_LIMIT:
            raise OSError('Đường dẫn file MP4 vượt quá giới hạn an toàn Windows.')
        if target.exists():
            raise FileExistsError('Tên file xuất bị trùng: ' + filename)
        shutil.move(str(rendered_file), str(target))
        if not target.exists() or target.stat().st_size <= 0:
            raise RuntimeError('Clip xuất không hợp lệ: ' + filename)
        output_files.append(target)

    with (out_dir / 'clips_timestamps.txt').open('w', encoding='utf-8') as info:
        for spec, path in zip(clip_specs, output_files):
            info.write(path.name + '\n')
            info.write('  Title: ' + spec['title'] + '\n')
            info.write('  Source ranges:\n')
            for start_seconds, end_seconds in spec['source_ranges']:
                info.write('    %s --> %s\n' % (
                    cutter.seconds_to_timestamp(start_seconds),
                    cutter.seconds_to_timestamp(end_seconds)))
            info.write('  Kept duration: %s\n\n' % cutter.seconds_to_timestamp(spec['kept_duration']))
    return output_files


def studio_status(folder):
    """Display a fresh render error even if an older successful export exists."""
    if (folder/'cut.running').exists():
        return 'CUTTING'
    marker=folder/'cut.done'
    err=folder/'cut.error'
    if err.exists() and (not marker.exists() or err.stat().st_mtime > marker.stat().st_mtime):
        return 'CUT_ERROR'
    return status(folder)


def has_previous_export(folder):
    """A former run counts even when the old cut.done marker is missing."""
    out = DONE / folder.name
    return (folder / 'cut.done').exists() or (out.is_dir() and any(out.iterdir()))


def render_approved(folder, meta, cfg, clips_snapshot, replace_existing):
    # FFmpeg module uses process-wide paths; render jobs one at a time.
    with RENDER_EXEC_LOCK:
        _render_approved_locked(folder, meta, cfg, clips_snapshot, replace_existing)


def _render_approved_locked(folder, meta, cfg, clips_snapshot, replace_existing):
    """Render into staging FIRST, publish only after all clips are complete.

    Previous clips are moved to _archive only when replacement is successful.
    If rendering fails, the original outputs and cut.done are untouched.
    """
    stage = None
    try:
        movie = video_file(folder, meta)
        if not movie:
            raise ValueError('Không còn video gốc. Hãy khôi phục MP4 trước khi xuất.')
        specs = make_specs(clips_snapshot)
        if not specs:
            raise ValueError('Chưa chọn clip để xuất.')
        note(folder/'cut.running', 'rendering %s approved clips' % len(specs))
        (folder/'cut.error').unlink(missing_ok=True)
        stage_root = DONE / '_pending'
        stage_root.mkdir(parents=True, exist_ok=True)
        stage = stage_root / (folder.name + '_' + uuid.uuid4().hex[:12])
        stage.mkdir()
        cutter.WORK_DIR = folder/'_cut_work'
        cutter.DONE_DIR = stage
        cutter.clear_work_dir()
        results = cutter.render_all_chapter_clips(movie, specs)
        if not results or len(results) != len(specs):
            raise RuntimeError('FFmpeg chưa xuất đủ clip. Bản cũ vẫn được giữ nguyên.')
        final = move_segments_to_done_safe(results, specs, meta['title'])
        if not final or len(final) != len(specs):
            raise RuntimeError('Không hoàn tất được file xuất. Bản cũ vẫn được giữ nguyên.')
        for file in final:
            if not file.exists() or file.stat().st_size < 1:
                raise RuntimeError('Có clip xuất bị trống. Bản cũ vẫn được giữ nguyên.')
        target = DONE / folder.name
        archive = None
        old_moved = False
        marker = folder/'cut.done'
        with RENDER_LOCK:
            if (target.exists() and any(target.iterdir())) or marker.exists():
                if not replace_existing:
                    raise ValueError('Video này đã được xuất trước đó. Hãy xác nhận thay bản cũ trước khi xuất lại.')
                archive_root = DONE / '_archive'
                archive_root.mkdir(parents=True, exist_ok=True)
                archive = archive_root / (folder.name + '_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
                archive.mkdir()
                if target.exists():
                    # Preserve the whole original folder even if an old run failed.
                    # Remove the newly created empty archive dir before rename.
                    archive.rmdir()
                    os.replace(str(target),str(archive))
                    old_moved = True
                if marker.exists():
                    shutil.copy2(str(marker),str(archive/'cut.done.previous.txt'))
            try:
                os.replace(str(stage),str(target))
                stage = None
            except Exception:
                if old_moved and archive and not target.exists():
                    os.replace(str(archive),str(target))
                raise
            # Write a clean manifest, using the new directory paths.
            relative_files = [f.relative_to(final[0].parents[1]) for f in final]
            note(marker,'DONE\n' + '\n'.join(str(target / rel) for rel in relative_files))
        if meta.get('url'):
            mark_url_done(meta['url'])
        # The user explicitly edits/re-exports old jobs; preserve their source video.
        # Deleting raw MP4 after export would break this key workflow.
        cutter.clear_work_dir()
    except Exception:
        note(folder/'cut.error',traceback.format_exc())
    finally:
        if stage and stage.exists():
            shutil.rmtree(str(stage), ignore_errors=True)
        (folder/'cut.running').unlink(missing_ok=True)
        with RENDER_LOCK:
            ACTIVE_RENDERS.discard(folder.name)




def reconcile_license_workers(valid):
    """Start ingest/detect only while the Studio license is currently allowed."""
    global WORKERS_ENABLED
    with WORKERS_LOCK:
        if valid and not WORKERS_ENABLED:
            launch_workers(config())
            WORKERS_ENABLED = True
        elif not valid and WORKERS_ENABLED:
            close_workers()
            WORKERS.clear()
            WORKERS_ENABLED = False


def license_status(force=False):
    result = LICENSE.status(force=force)
    reconcile_license_workers(result.get('valid', False))
    return result


def license_watch():
    # Also stop workers when the browser is closed or unattended.
    while True:
        try:
            license_status()
        except Exception as exc:
            print('[License] Error:', exc)
        time.sleep(35)

class Handler(BaseHTTPRequestHandler):
    server_version = 'ParallelLaughStudio/3.6'
    protocol_version = 'HTTP/1.1'

    def handle(self):
        try:
            return super().handle()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return

    def log_message(self, fmt, *args):
        if not self.path.startswith('/api/jobs'):
            super().log_message(fmt,*args)

    def send_bytes(self,data,mime='application/json',code=200):
        if not isinstance(data,bytes):
            data=json.dumps(data,ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type',mime+'; charset=utf-8')
        if mime == 'text/html':
            self.send_header('Cache-Control','no-store, no-cache, must-revalidate')
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.end_headers()
        self.wfile.write(data)

    def error_json(self,ex,code=400):
        self.send_bytes({'ok':False,'error':str(ex)},code=code)

    def get_param(self,p,key):
        return parse_qs(p.query).get(key,[''])[0]

    def do_GET(self):
        p=urlparse(self.path)
        try:
            if p.path == '/api/license-status':
                self.send_bytes(license_status())
                return
            current = license_status()
            if p.path in ('/', '/index.html'):
                page = HTML if current['valid'] else LICENSE_HTML
                self.send_bytes(page.read_bytes(), 'text/html')
                return
            if not current['valid']:
                self.send_bytes({'ok':False,'error':current['message'],'license_required':True},code=403)
                return
            if p.path in ('/api/license-activate', '/api/license-refresh'):
                self.send_bytes({'ok':False,'error':'Already licensed'},code=400)
                return
            if p.path == '/api/jobs':
                entries=[]
                for folder,meta in jobs():
                    entries.append({'id':folder.name,'title':meta.get('title',folder.name),
                        'status':studio_status(folder), 'has_previous_export':has_previous_export(folder), 'ai':(folder/'review_plan.json').exists(),
                        'laugh_count':len(get_laughs(folder)) if (folder/'laughs.csv').exists() else 0})
                self.send_bytes({'ok':True,'jobs':entries,
                                 'workers':{n:('running' if p.poll() is None else 'stopped') for n,p,_ in WORKERS}})
            elif p.path == '/api/job':
                folder,meta=find_job(self.get_param(p,'id'))
                length=probe_length(folder,meta)
                ai=(folder/'ai_result.txt')
                self.send_bytes({'ok':True,'job':{'id':folder.name,'title':meta.get('title'),
                     'status':studio_status(folder),'duration':length,'has_previous_export':has_previous_export(folder),'has_video':True,
                     'has_csv':(folder/'laughs.csv').exists(),
                     'ai_text':ai.read_text(encoding='utf-8') if ai.exists() else '',
                     'error':(folder/'cut.error').read_text(encoding='utf-8')[-1600:] if (folder/'cut.error').exists() else '',
                     'plan':read_plan(folder), 'laughs':get_laughs(folder)}})
            elif p.path == '/api/waveform':
                folder,meta=find_job(self.get_param(p,'id'))
                self.send_bytes(get_waveform(folder, meta))
            elif p.path == '/thumb':
                folder,meta=find_job(self.get_param(p,'id'))
                raw=self.get_param(p,'t')
                if not re.fullmatch(r'\d+(?:\.\d+)?',raw):
                    raise ValueError('Invalid thumbnail time')
                path=generate_thumbnail(folder,meta,float(raw))
                self.send_bytes(path.read_bytes(),mime='image/jpeg')
            elif p.path == '/api/subtitle-vtt':
                folder,meta=find_job(self.get_param(p,'id'))
                video_id=source_for_job(folder,meta)
                if not video_id:
                    raise ValueError('Chưa biết video YouTube gốc. Hãy dán link video để lấy phụ đề.')
                lang=self.get_param(p,'lang')
                data=fetch_caption(video_id,lang,folder,ROOT)
                self.send_bytes(data,mime='text/vtt')
            elif p.path == '/api/log':
                which=self.get_param(p,'which')
                if which not in ('ingest','detect'):
                    raise ValueError('Invalid log')
                logfile=ROOT/'logs'/(which+'.log')
                raw=logfile.read_bytes()[-16000:] if logfile.exists() else b''
                self.send_bytes({'ok':True,'log':raw.decode('utf-8',errors='replace')})
            elif p.path == '/media':
                folder,meta=find_job(self.get_param(p,'id'))
                path=video_file(folder,meta)
                if path is None:
                    raise ValueError('Cached video not found')
                self.stream_file(path)
            else:
                self.send_bytes({'ok':False,'error':'Not found'},code=404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # Browser closed a media request during seek/reload; not a detector error.
            return
        except (ValueError,OSError) as exc:
            try:
                self.error_json(exc)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return
        except Exception as exc:
            traceback.print_exc()
            try:
                self.error_json(exc,500)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return

    def stream_file(self,path):
        """HTTP Range is essential for responsive browser seeking/scrubbing."""
        size=path.stat().st_size
        mime=mimetypes.guess_type(str(path))[0] or 'video/mp4'
        start,end=0,size-1
        rng=self.headers.get('Range','')
        if rng:
            match=re.fullmatch(r'bytes=(\d*)-(\d*)',rng.strip())
            if not match:
                self.send_response(416); self.send_header('Content-Range','bytes */%d'%size)
                self.send_header('Content-Length','0');self.end_headers();return
            a,b=match.groups()
            if a:
                start=int(a)
                end=int(b) if b else size-1
            elif b:
                start=max(0,size-int(b))
            else:
                start=size
            end=min(end,size-1)
            if start> end or start>=size:
                self.send_response(416); self.send_header('Content-Range','bytes */%d'%size)
                self.send_header('Content-Length','0');self.end_headers();return
        length=end-start+1
        self.send_response(206 if rng else 200)
        self.send_header('Content-Type',mime)
        self.send_header('Content-Length',str(length))
        self.send_header('Accept-Ranges','bytes')
        if rng:
            self.send_header('Content-Range','bytes %d-%d/%d'%(start,end,size))
        self.send_header('Cache-Control','private, max-age=300')
        self.end_headers()
        try:
            with path.open('rb') as f:
                f.seek(start)
                while length>0:
                    piece=f.read(min(length,1024*1024))
                    if not piece: break
                    self.wfile.write(piece)
                    length-=len(piece)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):
            pass

    def do_POST(self):
        try:
            origin=self.headers.get('Origin','')
            if origin and origin not in ('http://127.0.0.1:%d'%PORT,'http://localhost:%d'%PORT):
                raise ValueError('Access denied (origin)')
            n=int(self.headers.get('Content-Length','0'))
            if n<=0 or n>MAX_REQUEST:
                raise ValueError('Request size invalid')
            obj=json.loads(self.rfile.read(n).decode('utf-8'))
            p=urlparse(self.path)
            if p.path == '/api/license-activate':
                self.send_bytes(LICENSE.activate(obj.get('key', '')))
                return
            if p.path == '/api/license-refresh':
                self.send_bytes(license_status(force=True))
                return
            current = license_status()
            if not current['valid']:
                self.send_bytes({'ok':False,'error':current['message'],'license_required':True},code=403)
                return
            if p.path == '/api/subtitle-languages':
                folder,meta=find_job(obj.get('id',''))
                raw=str(obj.get('source') or '').strip()
                video_id=parse_youtube_id(raw) if raw else source_for_job(folder,meta)
                if not video_id:
                    raise ValueError('Chưa có link YouTube gốc. Dán link hoặc ID video (chỉ lấy sub, không tải MP4).')
                languages=list_languages(video_id,ROOT)
                if not languages:
                    raise ValueError('YouTube không trả về track phụ đề nào cho video này.')
                if raw:
                    write_json_atomic(folder/'subtitle_source.json',{'video_id':video_id})
                self.send_bytes({'ok':True,'languages':languages,'video_id':video_id, 'cookie_status':cookie_status(ROOT)})
            elif p.path == '/api/import-ai':
                folder,meta=find_job(obj.get('id',''))
                value=str(obj.get('text') or '').strip()
                if not value:
                    raise ValueError('Dán JSON có key clips, hoặc CUT → DONE → CHAPTER → DONE cũ')
                plan=clips_from_ai(value,folder,meta,probe_length(folder,meta))
                # Do not overwrite current good draft unless the entire import validates.
                validated_plan(plan,plan['duration'])
                write_json_atomic(plan_file(folder),plan)
                (folder/'ai_result.txt').write_text(value+'\n',encoding='utf-8')
                self.send_bytes({'ok':True,'plan':plan})
            elif p.path == '/api/reopen':
                raise ValueError('Không cần mở lại lượt cắt. Bạn có thể chỉnh mốc bất kỳ lúc nào; Studio sẽ hỏi xác nhận khi xuất video.')
            elif p.path == '/api/save-plan':
                folder,meta=find_job(obj.get('id',''))
                old=read_plan(folder)
                if not old:
                    raise ValueError('Chưa có bản AI để chỉnh')
                length=probe_length(folder,meta)
                clips=validated_plan(obj,length)
                old['clips']=clips
                old['duration']=length
                write_json_atomic(plan_file(folder),old)
                self.send_bytes({'ok':True,'clips':clips})
            elif p.path == '/api/render':
                folder,meta=find_job(obj.get('id',''))
                replace_existing = obj.get('replace_existing') is True
                with RENDER_LOCK:
                    if folder.name in ACTIVE_RENDERS or (folder/'cut.running').exists():
                        raise ValueError('Studio đang xuất video này. Đợi xong rồi xuất lần nữa nhé.')
                    if has_previous_export(folder) and not replace_existing:
                        raise ValueError('Video đã được xuất trước đó. Cần chọn “Thay bản đã xuất” để xuất lại.')
                    plan=read_plan(folder)
                    if not plan:
                        raise ValueError('Bạn chưa dán mốc AI để chọn clip.')
                    snapshot=validated_plan(plan, probe_length(folder,meta), True)
                    # Pin source ranges now. Later edits must not change an in-flight render.
                    ACTIVE_RENDERS.add(folder.name)
                    note(folder/'cut.running','queued')
                    cfg=config()
                    th=threading.Thread(target=render_approved,args=(folder,meta,cfg,snapshot,replace_existing),daemon=True)
                    try:
                        th.start()
                    except Exception:
                        ACTIVE_RENDERS.discard(folder.name)
                        (folder/'cut.running').unlink(missing_ok=True)
                        raise
                self.send_bytes({'ok':True,'message':'Đang xuất %s clip. Bạn có thể tiếp tục chỉnh mốc cho lượt xuất sau.'%sum(1 for c in snapshot if c['approved'])})
            elif p.path == '/api/open':
                folder,meta=find_job(obj.get('id',''))
                what=obj.get('what','job')
                if what not in ('job','done'):
                    raise ValueError('Invalid folder')
                path=folder if what=='job' else DONE/folder.name
                if not path.exists(): raise ValueError('Thư mục chưa tồn tại')
                open_folder(path)
                self.send_bytes({'ok':True})
            elif p.path == '/api/open-input':
                open_folder(INBOX);self.send_bytes({'ok':True})
            else:
                self.send_bytes({'ok':False,'error':'Not found'},code=404)
        except (ValueError,KeyError,OSError,json.JSONDecodeError) as exc:
            self.error_json(exc)
        except Exception as exc:
            traceback.print_exc();self.error_json(exc,500)


def main():
    global PORT
    os.chdir(str(ROOT))
    cfg=config()
    configure_render(cfg)
    for attempt in range(10):
        try:
            http=ThreadingHTTPServer((HOST,PORT),Handler)
            break
        except OSError:
            PORT+=1
    else:
        raise RuntimeError('Cannot open localhost port')
    print('\nPARALLEL LAUGH STUDIO V2 — http://%s:%d'%(HOST,PORT))
    print('Running original ingest + Python 3.7 detector; browser is manual review lane.')
    threading.Thread(target=license_watch, daemon=True, name='LicenseCheck').start()
    webbrowser.open('http://%s:%d'%(HOST,PORT))
    try:
        http.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print('\nStopping studio...')
    finally:
        http.server_close()
        close_workers()


if __name__=='__main__':
    main()
