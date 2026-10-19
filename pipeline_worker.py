"""Background ingest and detector workers. Python 3.7-compatible.

Run separate processes:
  python pipeline_worker.py ingest
  python3.7 pipeline_worker.py detect
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

from pipeline_common import (
    INBOX, JOBS, ROOT, VIDEO_EXTS, config,
    initial_meta, job_id_for_local, jobs, log, note,
    video_file, write_json_atomic,
)


def which_bin(value):
    """Resolve FFmpeg solely inside this LaughDetectionV2 directory."""
    value = str(value or '').strip()
    local = (ROOT / value).resolve()
    try:
        local.relative_to(ROOT.resolve())
    except ValueError:
        raise RuntimeError('External executable path is not allowed: ' + value)
    if not local.is_file():
        raise RuntimeError('Missing project-local executable: ' + str(local))
    return str(local)


def stable_file(path, min_age=6):
    try:
        return time.time() - path.stat().st_mtime >= min_age and path.stat().st_size > 0
    except OSError:
        return False


def import_local():
    for item in sorted(INBOX.iterdir()):
        if not item.is_file() or item.suffix.lower() not in VIDEO_EXTS:
            continue
        if not stable_file(item):
            continue
        jid = job_id_for_local(item)
        dest_dir = JOBS / jid
        if dest_dir.exists():
            # Already imported. Remove only if user already gave explicit permission?
            # No: keep source file untouched. Avoid recopy on each scan.
            continue
        work = JOBS / ('.importing_' + jid)
        if work.exists():
            shutil.rmtree(str(work))
        work.mkdir(parents=True)
        try:
            dst = work / ('video' + item.suffix.lower())
            shutil.copy2(str(item), str(dst))
            meta = initial_meta(jid, 'local', item.stem, dst.name)
            write_json_atomic(work / 'job.json', meta)
            os.rename(str(work), str(dest_dir))
            log('Imported local: {} -> {}'.format(item.name, jid))
        except Exception as e:
            log('ERROR import {}: {}'.format(item.name, e))
            shutil.rmtree(str(work), ignore_errors=True)


def ingest():
    cfg = config()
    log('Ingest worker started (LOCAL MP4 only; no YouTube download)')
    while True:
        try:
            if cfg.get('auto_import_mp4', True):
                import_local()
        except Exception:
            log(traceback.format_exc())
        time.sleep(max(2, cfg.get('poll_seconds', 3)))


def load_detector(cfg):
    # Portable mode: model files must live inside this LaughDetectionV2 folder.
    # Never read Models/audioset from its parent or from an old machine path.
    project = ROOT
    # VGGish reads relative resources such as 'audioset/vggish_pca_params.npz'.
    # Detector subprocess uses this project's root as its working directory.
    # Since this is a dedicated detect subprocess, changing cwd is safe and
    # leaves the separate ingest/UI processes unaffected.
    project = project.resolve()
    if not (project / 'audioset').is_dir():
        raise RuntimeError('Missing audioset folder in detector_project_dir: ' + str(project))
    pca_file = project / 'audioset' / 'vggish_pca_params.npz'
    if not pca_file.is_file():
        raise RuntimeError('Missing VGGish PCA model file: {}. Check the audioset folder under LaughDetectionV2; do not duplicate models into V2.'.format(pca_file))
    os.chdir(str(project))
    log('Detector working directory: {}'.format(os.getcwd()))
    if str(project) not in sys.path:
        sys.path.insert(0, str(project))
    # Use provided model inference implementation; redirect its output for each job.
    module_file = ROOT / 'live_inference_AI_ready.py'
    spec = importlib.util.spec_from_file_location('integrated_laugh_detector', str(module_file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.KERAS_MODEL_PATH = project / 'Models' / 'LSTM_SingleLayer_100Epochs.h5'
    if not mod.KERAS_MODEL_PATH.is_file():
        raise RuntimeError('Missing Keras model: ' + str(mod.KERAS_MODEL_PATH))
    mod.FFMPEG = which_bin(cfg['ffmpeg'])
    log('Loading local laughter model (threshold {})'.format(mod.THRESHOLD))
    model = mod.keras.models.load_model(str(mod.KERAS_MODEL_PATH))
    embedder = mod.vggish_embeddings.VGGishEmbedder()
    log('Model loaded once. Detector watching job queue.')
    return mod, model, embedder


def detect():
    cfg = config()
    log('Detection worker started (Python {})'.format(sys.version.split()[0]))
    # Recover interrupted jobs after a previous worker was killed.
    for d, m in jobs():
        (d / 'detect.running').unlink(missing_ok=True) if sys.version_info >= (3, 8) else remove_file(d / 'detect.running')
    engine = None
    last_error = 0
    while True:
        try:
            todo = [(d, m) for d, m in jobs()
                    if video_file(d, m) and not (d / 'detect.done').exists()
                    and not (d / 'detect.error').exists() and not (d / 'detect.running').exists()]
            if todo and engine is None:
                try:
                    engine = load_detector(cfg)
                except Exception as e:
                    if time.time() - last_error > 45:
                        log('DETECTOR SETUP ERROR: {} (check config.json)'.format(e))
                        last_error = time.time()
                    time.sleep(8)
                    continue
            if todo:
                d, meta = todo[0]
                mod, model, embedder = engine
                (d / 'detect.running').write_text('running', encoding='utf-8')
                log('ANALYZING: {}'.format(meta.get('title', d.name)))
                try:
                    vid = video_file(d, meta)
                    mod.OUTPUT_FOLDER = d
                    mod.process_one(vid, model, embedder)
                    src = d / (mod.safe_name(vid.stem) + '_laughs.csv')
                    context = d / (mod.safe_name(vid.stem) + '_context_candidates.csv')
                    if not src.is_file():
                        raise RuntimeError('Detector returned without a laughs.csv')
                    os.replace(str(src), str(d / 'laughs.csv'))
                    if context.exists():
                        os.replace(str(context), str(d / 'context_candidates.csv'))
                    note(d / 'detect.done', 'OK')
                    log('CSV READY: {} (job {})'.format(meta.get('title', ''), d.name))
                except Exception:
                    detail = traceback.format_exc()
                    note(d / 'detect.error', detail)
                    log('DETECT ERROR job {}: {}'.format(d.name, detail[-1200:]))
                finally:
                    remove_file(d / 'detect.running')
        except Exception:
            log('WORKER ERROR: ' + traceback.format_exc()[-1200:])
        time.sleep(max(2, cfg.get('poll_seconds', 3)))


def remove_file(p):
    try:
        p.unlink()
    except OSError:
        pass


if __name__ == '__main__':
    if len(sys.argv) != 2 or sys.argv[1] not in ('ingest', 'detect'):
        print('Usage: pipeline_worker.py ingest|detect')
        sys.exit(2)
    try:
        if sys.argv[1] == 'ingest':
            ingest()
        else:
            detect()
    except KeyboardInterrupt:
        pass
