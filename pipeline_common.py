"""Common helpers; stdlib only, Python 3.7-compatible."""
import hashlib
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
JOBS = ROOT / 'jobs'
INBOX = ROOT / 'input_videos'
DONE = ROOT / 'done'
LOGS = ROOT / 'logs'
LIST = ROOT / 'list.txt'
DONE_LINK = ROOT / 'doneLink.txt'
CONFIG = ROOT / 'config.json'
VIDEO_EXTS = ('.mp4', '.mkv', '.webm', '.mov', '.m4v')
for p in (JOBS, INBOX, DONE, LOGS):
    p.mkdir(parents=True, exist_ok=True)


def config():
    data = {
        'detector_project_dir': '',
        'detector_python': '',
        'ffmpeg': 'ffmpeg',
        'ffprobe': 'ffprobe',
        'yt_dlp': 'yt-dlp',
        'keep_raw_after_cut': True,
        'auto_download_links': True,
        'auto_import_mp4': True,
        'poll_seconds': 3,
    }
    if CONFIG.is_file():
        try:
            obj = json.loads(CONFIG.read_text(encoding='utf-8-sig'))
            data.update(obj)
        except (ValueError, OSError) as e:
            print('Config warning:', e)
    return data


def write_json_atomic(path, obj):
    path = Path(path)
    temp = path.with_name(path.name + '.tmp')
    with temp.open('w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(str(temp), str(path))


def read_meta(jobdir):
    try:
        return json.loads((jobdir / 'job.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def jobs():
    result = []
    for d in JOBS.iterdir():
        if d.is_dir() and (d / 'job.json').exists():
            meta = read_meta(d)
            if meta:
                result.append((d, meta))
    result.sort(key=lambda pair: pair[1].get('created_at', ''))
    return result


def video_file(jobdir, meta):
    rel = meta.get('video_file') or ''
    if not rel or Path(rel).name != rel:
        return None
    candidate = jobdir / rel
    return candidate if candidate.is_file() and candidate.stat().st_size else None


def read_url_list(path):
    if not path.is_file():
        return []
    result, seen = [], set()
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        url = line.strip()
        if not url or url.startswith('#') or url in seen:
            continue
        result.append(url)
        seen.add(url)
    return result


def mark_url_done(url):
    if not url:
        return
    completed = read_url_list(DONE_LINK)
    if url not in completed:
        with DONE_LINK.open('a', encoding='utf-8') as f:
            f.write(url + '\n')
    if LIST.is_file():
        lines = LIST.read_text(encoding='utf-8-sig').splitlines()
        kept = [line for line in lines if line.strip() != url]
        tmp = LIST.with_name('list.txt.tmp')
        tmp.write_text('\n'.join(kept).rstrip() + '\n', encoding='utf-8')
        os.replace(str(tmp), str(LIST))


def job_id_for_url(url):
    match = re.search(r'[?&]v=([A-Za-z0-9_-]{6,})', url)
    id_part = match.group(1) if match else 'url'
    if match:
        # Stable job ID for the same YouTube video, even if URL includes playlist/timestamp params.
        return 'YT_' + id_part
    return 'YT_url_' + hashlib.sha1(url.encode('utf-8')).hexdigest()[:12]


def job_id_for_local(filename):
    # Stable identification by filename + size; no large file hashing.
    path = Path(filename)
    s = path.stat()
    key = (path.name + '|' + str(s.st_size)).encode('utf-8')
    return 'LOCAL_' + hashlib.sha1(key).hexdigest()[:13]


def initial_meta(jobid, source, title, video_name, url=''):
    return {
        'id': jobid,
        'source': source,
        'url': url,
        'title': title,
        'video_file': video_name,
        'created_at': datetime.now().isoformat(timespec='seconds')
    }


def status(jobdir):
    if (jobdir / 'cut.done').exists():
        return 'CUT_DONE'
    if (jobdir / 'cut.running').exists():
        return 'CUTTING'
    if (jobdir / 'cut.error').exists():
        return 'CUT_ERROR'
    if (jobdir / 'detect.error').exists():
        return 'DETECT_ERROR'
    if (jobdir / 'detect.running').exists():
        return 'DETECTING'
    if (jobdir / 'detect.done').exists():
        return 'WAITING_AI'
    return 'WAITING_DETECT'


def note(path, message):
    Path(path).write_text(str(message), encoding='utf-8')


def log(message):
    line = '[{}] {}'.format(datetime.now().strftime('%H:%M:%S'), message)
    print(line, flush=True)
