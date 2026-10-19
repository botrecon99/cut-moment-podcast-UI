"""Interactive 2-lane operator console. No third-party Python dependencies.

Lane A workers: download/import and detect with Python 3.7 continuously.
Lane B: accept an AI CUT+CHAPTER response and render cached source video.
"""
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

from pipeline_common import (
    ROOT, JOBS, LOGS, DONE, CONFIG, config, jobs, mark_url_done, note,
    status, video_file
)
import tai_chi_cat as cutter

PROMPT = ROOT / 'PROMPT_V4.2_CSV_FIRST.txt'
WORKER_FILE = ROOT / 'pipeline_worker.py'
WORKERS = []


def get_detector_python(cfg):
    """Use ONLY the Python 3.7 runtime contained in THIS project.

    Never consult Windows Registry, PATH, a parent folder, or cfg paths
    copied from another workstation. Installer V5 installs _python37 here.
    """
    if os.name == 'nt':
        exe = ROOT / '_python37' / 'python.exe'
    else:
        raise RuntimeError('This deployment requires Windows 64-bit')
    if not exe.is_file():
        raise RuntimeError('Missing project-local _python37/python.exe: run INSTALL_NEW_PC.bat')
    print('  [INFO] Detector uses project-local Python: {}'.format(exe))
    return [str(exe)]


def launch_workers(cfg):
    for name, cmd in (
        ('ingest', [sys.executable, str(WORKER_FILE), 'ingest']),
        ('detect', get_detector_python(cfg) + [str(WORKER_FILE), 'detect']),
    ):
        log_path = LOGS / (name + '.log')
        log = log_path.open('ab', buffering=0)
        try:
            kwargs = {}
            if os.name == 'nt':
                kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
            proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=log,
                                    stderr=subprocess.STDOUT, **kwargs)
            WORKERS.append((name, proc, log))
            print('  [OK] {} worker PID {} -> {}'.format(name, proc.pid, log_path))
        except OSError as exc:
            log.close()
            print('  [ERROR] {} worker not launched: {} / CMD={}'.format(name, exc, cmd))


def close_workers():
    for name, proc, log in WORKERS:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        log.close()


def configure_render(cfg):
    def bin_path(x):
        x = str(x)
        local = ROOT / x
        return str(local) if local.is_file() else x
    cutter.FFMPEG = bin_path(cfg.get('ffmpeg') or 'ffmpeg')
    cutter.FFPROBE = bin_path(cfg.get('ffprobe') or 'ffprobe')
    if not shutil.which(str(cutter.FFMPEG)) and not Path(str(cutter.FFMPEG)).is_file():
        print('WARNING: ffmpeg not located. Configure ffmpeg path in config.json')
    if not shutil.which(str(cutter.FFPROBE)) and not Path(str(cutter.FFPROBE)).is_file():
        print('WARNING: ffprobe not located. Configure ffprobe path in config.json')


def short(text, length=65):
    text = re.sub(r'\s+', ' ', text.strip())
    return (text[:length - 3] + '...') if len(text) > length else text


def show_jobs():
    rows = jobs()
    print('\n' + '=' * 105)
    print('{:<4} {:<18} {:<20} {}'.format('NO', 'STATE', 'JOB ID', 'TITLE'))
    print('-' * 105)
    for i, (d, m) in enumerate(rows, 1):
        print('{:<4} {:<18} {:<20} {}'.format(i, status(d), d.name[:20], short(m.get('title', ''))))
    downloading = [p.name.replace('.downloading_', '') for p in JOBS.glob('.downloading_*') if p.is_dir()]
    if downloading:
        print('DOWNLOADING NOW:', ', '.join(downloading[:5]))
    if not rows and not downloading:
        print('No jobs yet. Add URLs in list.txt or MP4 files in input_videos/.')
    print('=' * 105)
    print('A: watch CSV + transcript + prompt manually | B: paste CUT+CHAPTER | C: open job folder')
    print('R: retry detector failures | L: show recent log | ENTER: refresh | Q: quit')
    return rows


def select_job(rows, allowed=None):
    if not rows:
        return None
    raw = input('Select job number (ENTER cancels): ').strip()
    if not raw.isdigit():
        return None
    index = int(raw) - 1
    if index < 0 or index >= len(rows):
        print('Invalid selection.')
        return None
    d, m = rows[index]
    if allowed and status(d) not in allowed:
        print('Job state {} not allowed for this action.'.format(status(d)))
        return None
    return d, m


def open_folder(path):
    if os.name == 'nt':
        os.startfile(str(path))
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', str(path)])
    else:
        subprocess.Popen(['xdg-open', str(path)])


def make_packet(d, m):
    transcript = d / 'transcript.txt'
    csv = d / 'laughs.csv'
    if not csv.exists():
        print('CSV not ready: {}'.format(csv))
        return
    print('\nFiles for AI:')
    print('  Prompt     : {}'.format(PROMPT))
    print('  CSV        : {}'.format(csv))
    print('  Transcript : {}'.format(transcript))
    if transcript.exists() and transcript.stat().st_size:
        data = (
            'VIDEO JOB: {}\nTITLE: {}\n\n'.format(m['id'], m['title'])
            + '===== MASTER PROMPT =====\n' + PROMPT.read_text(encoding='utf-8-sig')
            + '\n\n===== LAUGHTER CSV =====\n' + csv.read_text(encoding='utf-8-sig')
            + '\n\n===== FULL TRANSCRIPT =====\n'
            + transcript.read_text(encoding='utf-8-sig')
        )
        packet = d / 'AI_PACKET.txt'
        packet.write_text(data, encoding='utf-8')
        print('  [READY] One-file packet: {}'.format(packet))
    else:
        print('  Transcript not found yet. Put a timestamped transcript.txt into this job folder.')
        print('  You can upload three files to ChatGPT manually without making AI_PACKET.')


def receive_ai_text():
    print('\nPaste AI result below. Needs two DONE separators as in V4.2.')
    print('Example: [CUT lines] DONE [CHAPTER lines] DONE')
    print('Type CANCEL alone to stop. (Paste multiple lines into console.)\n')
    lines = []
    done_count = 0
    while True:
        try:
            line = input()
        except EOFError:
            print('stdin closed; did not render')
            return None
        if line.strip().upper() == 'CANCEL':
            return None
        lines.append(line)
        done_count += len(re.findall(r'\bDONE\b', line, flags=re.IGNORECASE))
        if done_count >= 2:
            return '\n'.join(lines) + '\n'
        if len(lines) > 5000:
            print('Unusually long input, cancelled.')
            return None


def parse_ai(text):
    cleaned = re.sub(r'(?m)^\s*```[^\n]*$', '', text)
    parts = re.split(r'(?im)^\s*DONE\s*$', cleaned)
    if len(parts) < 3:
        parts = re.split(r'\bDONE\b', cleaned, flags=re.IGNORECASE)
    if len(parts) < 3:
        raise ValueError('Need CUT section, DONE, CHAPTER section, DONE')
    a, b = parts[:2]
    if re.search(r'(?i)^\s*SKIP\s*$', a):
        return 'skip', [], False, []

    # Normalize cut ranges, including multiple ranges accidentally pasted on one line.
    time_token = r'\d{1,4}:\d{2}(?::\d{2})?'
    pat = re.compile(r'({0})\s*(?:-->|-|–|—|TO)\s*({0}|END)'.format(time_token), re.I)
    extracted = ['{} --> {}'.format(x.group(1), x.group(2)) for x in pat.finditer(a)]
    keep_all_requested = bool(re.search(r'(?i)\b(?:NONE|KEEP ALL)\b', a))
    if not extracted and not keep_all_requested:
        raise ValueError('No valid CUT ranges')
    ranges, keep_all, bad = cutter.parse_cut_ranges_from_lines(extracted or ['NONE'])
    if bad:
        raise ValueError('Invalid CUT range: ' + bad[0])
    chapters, original, bad_chaps = cutter.parse_chapters_from_lines(b.splitlines())
    if bad_chaps:
        raise ValueError('Invalid CHAPTER line: ' + bad_chaps[0])
    if not chapters:
        raise ValueError('No CHAPTER lines found; this pipeline requires at least one chapter title')
    return 'process', ranges, keep_all, chapters


def validate_plan(raw, cut_ranges, keep_all, chapters, title):
    length = cutter.get_video_duration(raw)
    if length is None or length <= 0:
        raise ValueError('Cannot ffprobe video duration')
    for s, e in cut_ranges:
        if s >= length:
            raise ValueError('CUT begins after video ends')
        if e != float('inf') and e > length + 3.0:
            raise ValueError('CUT END exceeds video duration by > 3 seconds')
    normalized = [] if keep_all else cutter.normalize_cut_ranges(cut_ranges, length)
    keeps = [(0.0, length)] if keep_all else cutter.build_keep_ranges(normalized, length)
    if not keeps:
        raise ValueError('No video left after CUT')
    normalized_chapters = cutter.normalize_chapters(chapters, length)
    if len(normalized_chapters) != len(chapters):
        raise ValueError('Duplicate/out-of-bounds CHAPTER timestamp')
    for start, name in normalized_chapters:
        if not any(a <= start < b for a, b in keeps):
            raise ValueError('CHAPTER at {} lies inside a CUT region'.format(cutter.seconds_to_timestamp(start)))
    if abs(normalized_chapters[0][0] - keeps[0][0]) > 0.15:
        raise ValueError('First CHAPTER must start exactly at the first kept timestamp')
    for start, end in keeps:
        if not any(abs(t - start) < 0.15 for t, _ in normalized_chapters):
            raise ValueError('Missing CHAPTER at keep start {}'.format(cutter.seconds_to_timestamp(start)))
    specs = cutter.build_clip_specs(keeps, normalized_chapters, length, title)
    if not specs:
        raise ValueError('Zero output clips')
    return length, specs


def render_job(d, m, cfg, text):
    if (d / 'cut.done').exists():
        print('Already rendered. Raw file and result still accessible; no automatic overwrite.')
        return
    raw = video_file(d, m)
    if not raw:
        print('SOURCE VIDEO MISSING: {}'.format(d))
        return
    try:
        action, ranges, keep_all, chapters = parse_ai(text)
        if action == 'skip':
            print('AI selected SKIP. No deletion performed.')
            (d / 'ai_result.txt').write_text(text, encoding='utf-8')
            note(d / 'ai.skip', 'AI skip')
            return
        video_seconds, specs = validate_plan(raw, ranges, keep_all, chapters, m['title'])
    except (ValueError, OSError) as e:
        print('INVALID AI RESULT: {}'.format(e))
        print('No changes to video. Paste corrected CUT+CHAPTER again.')
        return

    print('\nCUT PREVIEW: {} | source {} | {} clips'.format(m['title'],
          cutter.seconds_to_timestamp(video_seconds), len(specs)))
    for i, spec in enumerate(specs, 1):
        times = ', '.join('{} -> {}'.format(cutter.seconds_to_timestamp(a),
                                        cutter.seconds_to_timestamp(b))
                          for a, b in spec['source_ranges'])
        print('  {:02d} {} | {}'.format(i, short(spec['title'], 70), times))

    # Save pasted text before attempting to render: safe to retry even after a crash.
    (d / 'ai_result.txt').write_text(text, encoding='utf-8')
    cutter.WORK_DIR = d / '_cut_work'
    cutter.DONE_DIR = DONE / d.name
    cutter.DONE_DIR.mkdir(parents=True, exist_ok=True)
    (d / 'cut.error').unlink(missing_ok=True)
    note(d / 'cut.running', 'processing')
    try:
        results = cutter.process_video(raw, m['title'], ranges, chapters,
                                       keep_all=keep_all, use_original_title=False)
        if not results:
            raise RuntimeError('FFmpeg returned no valid clips')
        note(d / 'cut.done', 'DONE\n' + '\n'.join(str(p) for p in results))
        if m.get('url'):
            mark_url_done(m['url'])
        if not cfg.get('keep_raw_after_cut', True):
            # Removal only AFTER successful outputs, and explicit config=false.
            raw.unlink()
        print('\n[COMPLETE] {} clips -> {}'.format(len(results), results[0].parent))
    except Exception:
        detail = traceback.format_exc()
        note(d / 'cut.error', detail)
        print('CUT ERROR: {}'.format(detail[-1500:]))
    finally:
        (d / 'cut.running').unlink(missing_ok=True)


def retry_detect(d):
    for name in ('detect.error', 'detect.done', 'detect.running'):
        (d / name).unlink(missing_ok=True)
    print('Detector job requeued:', d.name)


def recent_log():
    print('Choose log: 1 ingest, 2 detect')
    choice = input('Log: ').strip()
    which = LOGS / ('detect.log' if choice == '2' else 'ingest.log')
    if not which.exists():
        print('No log yet')
        return
    lines = which.read_text(encoding='utf-8', errors='replace').splitlines()
    for l in lines[-25:]:
        print(l)


def main():
    os.chdir(str(ROOT))
    cfg = config()
    configure_render(cfg)
    print('\n' + '=' * 84)
    print(' PARALLEL VIDEO -> LAUGH CSV -> MANUAL AI -> CACHED VIDEO CUTTER')
    print(' [A] Auto ingest + detector | [B] Paste AI CUT + CHAPTER and render')
    print('=' * 84)
    print('Root:', ROOT)
    print('Detector project:', cfg.get('detector_project_dir') or '(same folder)')
    print('Detector Python:', get_detector_python(cfg))
    launch_workers(cfg)
    try:
        while True:
            rows = show_jobs()
            choice = input('\nChoice: ').strip().upper()
            if choice == 'Q':
                break
            if choice == 'L':
                recent_log()
            elif choice == 'A':
                chosen = select_job(rows)
                if chosen:
                    make_packet(*chosen)
            elif choice == 'C':
                chosen = select_job(rows)
                if chosen:
                    open_folder(chosen[0])
            elif choice == 'B':
                chosen = select_job(rows, allowed=('WAITING_AI', 'CUT_ERROR'))
                if chosen:
                    raw_text = receive_ai_text()
                    if raw_text:
                        render_job(chosen[0], chosen[1], cfg, raw_text)
            elif choice == 'R':
                chosen = select_job(rows, allowed=('DETECT_ERROR', 'WAITING_DETECT'))
                if chosen:
                    retry_detect(chosen[0])
            elif choice == '':
                pass
            else:
                print('Unknown command: ' + choice)
    except (KeyboardInterrupt, EOFError):
        print('\nConsole stopping...')
    finally:
        close_workers()
        print('Worker processes closed. Jobs and cached videos are preserved.')


if __name__ == '__main__':
    main()
