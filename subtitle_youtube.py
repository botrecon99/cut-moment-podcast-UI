"""Optional YouTube subtitle reader for locally cached video jobs.

Only accesses caption metadata and caption files.  It does not download video,
transcribe audio, translate using unrelated services, or affect the detector.
Requires `yt-dlp` in the Python that starts studio_server.py.
"""
import html
from http.cookiejar import MozillaCookieJar, LoadError
import json
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs

_ID = re.compile(r'^[A-Za-z0-9_-]{11}$')
_LANG = re.compile(r'^[A-Za-z0-9_.-]{1,36}$')
_META = {}
_LOCK = threading.RLock()


def parse_youtube_id(source):
    """Accept only standard YouTube links or a raw 11-character video ID."""
    text = str(source or '').strip()
    if _ID.fullmatch(text):
        return text
    try:
        parsed = urlparse(text)
        host = (parsed.hostname or '').lower()
        if parsed.scheme not in ('https', 'http'):
            return None
        if host in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtube-nocookie.com', 'www.youtube-nocookie.com'):
            vid = parse_qs(parsed.query).get('v', [''])[0] if parsed.path.rstrip('/') in ('/watch', '/watch/') else ''
            if not vid:
                parts = parsed.path.strip('/').split('/')
                if len(parts) == 2 and parts[0] in ('shorts','live','embed'):
                    vid = parts[1]
        elif host == 'youtu.be' or host == 'www.youtu.be':
            vid = parsed.path.strip('/').split('/')[0]
        else:
            return None
        return vid if _ID.fullmatch(vid or '') else None
    except (ValueError, TypeError):
        return None


def source_for_job(folder, meta):
    state = folder / 'subtitle_source.json'
    if state.is_file():
        try:
            raw = json.loads(state.read_text(encoding='utf-8'))
            vid = parse_youtube_id(raw.get('video_id'))
            if vid:
                return vid
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    for candidate in (meta.get('url'), meta.get('id')):
        vid = parse_youtube_id(candidate)
        if vid:
            return vid
    for candidate in (meta.get('id'), folder.name):
        if str(candidate).startswith('YT_') and _ID.fullmatch(str(candidate)[3:]):
            return str(candidate)[3:]
    # Safe extraction ONLY from explicit [VIDEO_ID] in the filename/title.
    for candidate in (meta.get('video_file'), meta.get('title')):
        match = re.search(r'\[([A-Za-z0-9_-]{11})\]', str(candidate or ''))
        if match:
            return match.group(1)
    return None


def _import_ytdlp():
    try:
        import yt_dlp
        return yt_dlp
    except ImportError as exc:
        raise ValueError('Chưa có yt-dlp. Mở CMD chạy: python -m pip install -U yt-dlp (Python đang chạy Studio).') from exc


def _cookies_state(root):
    """Inspect only cookies.txt next to START_STUDIO.bat; never expose cookie values."""
    path = Path(root) / 'cookies.txt'
    if not path.is_file():
        return {'available': False, 'message': 'Chưa có cookies.txt trong LaughDetectionV2.'}
    try:
        if path.stat().st_size < 40:
            raise ValueError('File cookies.txt rỗng hoặc quá ngắn')
        # Real yt-dlp expects a Netscape-format cookie file, not a JSON export.
        with path.open('r', encoding='utf-8-sig', errors='replace') as f:
            first = f.readline().strip()
        if first not in ('# Netscape HTTP Cookie File', '# HTTP Cookie File'):
            raise ValueError('cookies.txt phải là định dạng Netscape (dòng đầu: # Netscape HTTP Cookie File)')
        jar = MozillaCookieJar(str(path))
        jar.load(ignore_discard=True, ignore_expires=True)
        n = sum(1 for c in jar if (c.domain or '').lower().endswith(('youtube.com', '.youtube.com', 'google.com', '.google.com')))
        if n == 0:
            raise ValueError('cookies.txt không có cookie YouTube/Google; hãy xuất lại từ youtube.com')
        stat = path.stat()
        return {'available': True, 'message': 'Đã nạp cookies.txt (%d mục liên quan YouTube/Google). Chưa xác minh còn đăng nhập.' % n,
                'fingerprint': (stat.st_mtime_ns, stat.st_size)}
    except (OSError, ValueError, LoadError) as ex:
        return {'available': False, 'invalid': True, 'message': 'cookies.txt không hợp lệ: %s' % str(ex)[:150]}


def cookie_status(root):
    """Non-sensitive cookie status for the Studio user interface."""
    result = _cookies_state(root)
    return {'available': result['available'], 'message': result['message']}


def _opts(root):
    opts = {'quiet': True, 'no_warnings': True, 'skip_download': True,
            'ignore_no_formats_error': True, 'format': 'none', 'socket_timeout': 16,
            'retries': 0, 'extractor_retries': 0, 'playlist_items': '1',
            'noplaylist': True}
    cookies = _cookies_state(root)
    if cookies.get('invalid'):
        raise ValueError(cookies['message'])
    if cookies['available']:
        # yt-dlp uses this cookie jar for BOTH video metadata and the
        # caption download. Never read cookies in the browser/JavaScript.
        opts['cookiefile'] = str(Path(root) / 'cookies.txt')
    return opts


def _is_youtube_429(exc):
    message = str(exc).lower()
    if '429' in message or 'too many requests' in message:
        return True
    original = getattr(exc, '__cause__', None)
    return original is not None and original is not exc and _is_youtube_429(original)


def _friendly_429(stage, root=None):
    cookies = _cookies_state(root) if root is not None else {'available': False}
    desc = ('Đã sử dụng cookies.txt nhưng YouTube vẫn trả 429. '
            if cookies['available'] else 'Chưa có cookies.txt hợp lệ cạnh START_STUDIO.bat. ')
    return ("YouTube đang giới hạn truy cập (HTTP 429) khi " + stage + ". "
            + desc + "Không bấm thử liên tục; đợi rồi thử lại. Cookies không đảm bảo hết 429.")


def _extract_info(video_id, root, refresh=False):
    # A short-lived cached extract means selecting a language doesn't repeat
    # another large YouTube metadata request. Caption URLs expire, so TTL short.
    with _LOCK:
        pair = _META.get(video_id)
        # Re-extract metadata if the user has just added/replaced cookies.txt.
        state = _cookies_state(root)
        fingerprint = state.get('fingerprint', None)
        if not refresh and pair and pair[1] == fingerprint and time.monotonic() - pair[0] < 360:
            return pair[2]
    yt_dlp = _import_ytdlp()
    try:
        with yt_dlp.YoutubeDL(_opts(root)) as ydl:
            info = ydl.extract_info('https://www.youtube.com/watch?v='+video_id, download=False)
        if not isinstance(info, dict):
            raise ValueError('YouTube không trả về thông tin video')
    except ValueError:
        raise
    except Exception as exc:
        if _is_youtube_429(exc):
            raise ValueError(_friendly_429('quét danh sách ngôn ngữ', root)) from exc
        raise ValueError('Không quét được phụ đề YouTube: '+str(exc)[:280]) from exc
    with _LOCK:
        # yt-dlp may update cookies.txt on close; cache against the final file.
        _META[video_id] = (time.monotonic(), _cookies_state(root).get('fingerprint'), info)
    return info


def list_languages(video_id, root):
    info = _extract_info(video_id, root)
    found = {}
    for source_key,source_label in [('subtitles','Phụ đề do kênh cung cấp'),
                                    ('automatic_captions','Phụ đề tự động / dịch YouTube')]:
        for code, formats in (info.get(source_key) or {}).items():
            if not _LANG.fullmatch(str(code)) or code in found or not formats:
                continue
            usable = [f for f in formats if f.get('ext') in ('json3','vtt') and f.get('url')]
            if not usable:
                continue
            name = next((str(f.get('name')) for f in formats if f.get('name')), code)
            found[code] = {'code':code,'name':name,'kind':source_label}
    return sorted(found.values(),key=lambda e:(e['code'] != 'vi',e['code'] != 'en',e['name'].lower()))


def _parse_json3(raw):
    cues=[]
    for event in raw.get('events',[]):
        parts=event.get('segs') or []
        if not parts:
            continue
        content=''.join(str(x.get('utf8','')) for x in parts)
        content=html.unescape(re.sub(r'\s+',' ',content)).strip()
        if not content:
            continue
        start=max(0,int(event.get('tStartMs') or 0))
        length=max(0,int(event.get('dDurationMs') or 0))
        if length < 80:
            continue
        end=start+length
        if cues and cues[-1][0]==start and cues[-1][2]==content:
            cues[-1]=(start,max(end,cues[-1][1]),content)
        else:
            cues.append((start,end,content))
    if not cues:
        raise ValueError('Track được chọn không có lời thoại để hiển thị')
    return cues


def _format_t(ms):
    t,s=divmod(ms,1000)
    h,s=divmod(t,3600)
    m,s=divmod(s,60)
    return '%02d:%02d:%02d.%03d' % (h,m,s,ms%1000)


def _json3_to_vtt(raw):
    cues = _parse_json3(raw)
    result=['WEBVTT','']
    for i,(start,end,content) in enumerate(cues,1):
        # Escape angle brackets so captions can't inject VTT styling directives.
        content=content.replace('<','&lt;').replace('>','&gt;')
        result.extend([str(i), '%s --> %s'%(_format_t(start),_format_t(end)),content,''])
    return '\n'.join(result)+'\n'


def fetch_caption(video_id, lang, folder, root):
    if not _LANG.fullmatch(str(lang)):
        raise ValueError('Mã ngôn ngữ không hợp lệ')
    savedir=folder/'subtitles'
    target=savedir/('%s.%s.vtt' % (video_id,lang))
    if target.is_file() and target.stat().st_size > 12:
        return target.read_bytes()
    for attempt in range(2):
        info=_extract_info(video_id,root,refresh=(attempt>0))
        track=(info.get('subtitles') or {}).get(lang) or (info.get('automatic_captions') or {}).get(lang)
        if not track:
            raise ValueError('YouTube không có phụ đề ngôn ngữ %s cho video này' % lang)
        fmt=next((f for f in track if f.get('ext')=='json3' and f.get('url')),None)
        if fmt is None:
            fmt=next((f for f in track if f.get('ext')=='vtt' and f.get('url')),None)
        if fmt is None:
            raise ValueError('Track %s không có định dạng phụ đề VTT/JSON3' % lang)
        yt_dlp=_import_ytdlp()
        try:
            with yt_dlp.YoutubeDL(_opts(root)) as ydl:
                response=ydl.urlopen(fmt['url'])
                with response:
                    data=response.read(8 * 1024 * 1024 + 1)
            if len(data)>8*1024*1024:
                raise ValueError('Phụ đề quá lớn (giới hạn 8 MB)')
            if fmt['ext']=='json3':
                data=_json3_to_vtt(json.loads(data.decode('utf-8-sig'))).encode('utf-8')
            elif not data.lstrip(b'\xef\xbb\xbf').startswith(b'WEBVTT'):
                raise ValueError('YouTube trả về dữ liệu không phải VTT')
            savedir.mkdir(exist_ok=True)
            temporary=target.with_suffix('.%d.tmp' % threading.get_ident())
            temporary.write_bytes(data)
            temporary.replace(target)
            return data
        except ValueError:
            raise
        except Exception as exc:
            # HTTP 429 = rate limiting. Retrying immediately only makes it worse.
            if _is_youtube_429(exc):
                raise ValueError(_friendly_429('tải phụ đề '+lang, root)) from exc
            if attempt==0:
                continue
            raise ValueError('Không tải được track phụ đề từ YouTube: '+str(exc)[:250]) from exc
    raise ValueError('Không tải được phụ đề')
