"""Online license gate for LaughDetectionV2 (Python stdlib, Windows-friendly).

License keys stay on each device under .license/, never in Git.
Offline allowance: at most 24 hours since a confirmed online validation and
never past server-reported expiry. This is operational access control, not DRM.
"""
import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / 'license_config.json'
DATA_DIR = ROOT / '.license'
STATE_FILE = DATA_DIR / 'activation.json'
OFFLINE_GRACE = 24 * 3600
CHECK_INTERVAL = 120
NETWORK_TIMEOUT = 6


def _json_file(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _write_state(value):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_name('activation.json.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(str(temp), str(STATE_FILE))


def machine_hash():
    """Pseudonymous fingerprint from Windows machine identity (not user files)."""
    parts = []
    if os.name == 'nt':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r'SOFTWARE\Microsoft\Cryptography') as key:
                parts.append(str(winreg.QueryValueEx(key, 'MachineGuid')[0]))
        except OSError:
            pass
    parts.append(str(uuid.getnode()))
    parts.append(os.environ.get('COMPUTERNAME', '') if os.name == 'nt' else os.uname().nodename)
    return hashlib.sha256(('LaughDetectionV2/device/v1|' + '|'.join(parts)).encode('utf-8')).hexdigest()


class LicenseClient:
    def __init__(self):
        self.lock = threading.RLock()
        self.last_checked = 0.0
        self.memo = None

    def _endpoint(self):
        url = str(_json_file(CONFIG).get('license_server_url', '')).strip().rstrip('/')
        if not url.startswith('https://'):
            return None
        if any(c in url for c in (' ', '\\', '?', '#')) or 'YOUR-' in url.upper() or 'REPLACE' in url.upper():
            return None
        return url

    @staticmethod
    def _request(url, route, payload):
        req = Request(url + route,
                      data=json.dumps(payload).encode('utf-8'),
                      headers={'Content-Type': 'application/json', 'User-Agent': 'LaughDetectionV2-License/1'},
                      method='POST')
        try:
            with urlopen(req, timeout=NETWORK_TIMEOUT) as res:
                return json.loads(res.read(8192).decode('utf-8')), False
        except HTTPError as ex:
            try:
                error = json.loads(ex.read(8192).decode('utf-8'))
                if 400 <= ex.code < 500:
                    return error, False  # explicit rejection: do NOT grant offline grace
            except (ValueError, UnicodeError):
                pass
            raise URLError('Máy chủ license lỗi HTTP %s' % ex.code)

    def _report(self, valid, message, **details):
        return {'ok': True, 'valid': bool(valid), 'message': str(message), **details}

    def status(self, force=False):
        with self.lock:
            now = time.time()
            tick = time.monotonic()
            if not force and self.memo is not None and tick - self.last_checked < CHECK_INTERVAL:
                return dict(self.memo)
            url = self._endpoint()
            state = _json_file(STATE_FILE)
            if not url:
                answer = self._report(False, 'Chưa cấu hình máy chủ license (license_config.json).', configured=False)
            elif not state.get('key'):
                answer = self._report(False, 'Chưa kích hoạt. Hãy nhập License Key.', configured=True)
            else:
                try:
                    data, _ = self._request(url, '/api/validate', {
                        'key': state['key'], 'device': machine_hash()
                    })
                    if data.get('valid'):
                        expiry = int(data.get('expires_at', 0))
                        state.update({'expires_at': expiry, 'last_online_at': now,
                                      'label': data.get('label', ''), 'online_denied': False})
                        _write_state(state)
                        answer = self._report(expiry > now, 'License hợp lệ.' if expiry > now else 'License đã hết hạn.',
                                              configured=True, expires_at=expiry,
                                              offline=False, label=state.get('label', ''))
                    else:
                        state['online_denied'] = True
                        _write_state(state)
                        answer = self._report(False, data.get('error', 'License bị từ chối.'), configured=True,
                                              expires_at=state.get('expires_at'), offline=False)
                except (URLError, OSError, ValueError, KeyError, TimeoutError) as ex:
                    last = float(state.get('last_online_at') or 0)
                    expiry = float(state.get('expires_at') or 0)
                    # Prevent obvious time rollback from extending local grace.
                    grace = (not state.get('online_denied') and 0 <= now - last <= OFFLINE_GRACE
                             and now < expiry and last > 0)
                    msg = 'Mất kết nối: được dùng offline tối đa 24 giờ kể từ xác thực cuối.' if grace else 'Không kết nối được máy chủ license. Cần Internet để xác thực.'
                    answer = self._report(grace, msg, configured=True, expires_at=int(expiry),
                                          offline=grace, network_error=str(ex)[:130])
            self.memo = answer
            self.last_checked = tick
            return dict(answer)

    def activate(self, key):
        with self.lock:
            url = self._endpoint()
            if not url:
                raise ValueError('Chưa cấu hình license_server_url trong license_config.json')
            key = str(key or '').strip().upper()
            if len(key) < 20 or len(key) > 120:
                raise ValueError('Mã key không hợp lệ.')
            try:
                reply, _ = self._request(url, '/api/activate', {'key': key, 'device': machine_hash()})
            except (URLError, OSError, ValueError, TimeoutError) as ex:
                raise ValueError('Không kết nối được máy chủ license: %s' % ex)
            if not reply.get('valid'):
                raise ValueError(reply.get('error', 'Kích hoạt thất bại.'))
            _write_state({'key': key, 'expires_at': int(reply['expires_at']),
                          'last_online_at': time.time(), 'label': reply.get('label', ''), 'online_denied': False})
            self.memo = None
            self.last_checked = 0
            return self.status(force=True)
