# MIT, 88lin, da2bc7f. Reviewed declaration-only adapter; upstream entrypoints are not imported.
import base64
import json
import math
import ntpath
import os
import plistlib
import re
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import urllib.parse
import uuid
from contextlib import closing
from datetime import date, datetime
DEFAULT_ENDPOINT = 'https://copilot.tencent.com'
AUTH_BASENAME = os.path.join('CodeBuddyExtension', 'Data', 'Public', 'auth', 'workbuddy-desktop.info')
CLI_AUTH_BASENAME = os.path.join('CodeBuddyExtension', 'Data', 'Public', 'auth', 'Tencent-Cloud.coding-copilot.info')
CODE_NO_NETWORK = -1
CODE_BUDGET_OUT = -2
DEFAULT_BUDGET_SECONDS = 420.0
MAX_BUDGET_SECONDS = 540.0
POLL_BUDGET_SECONDS = 180.0
POLL_MAX_BUDGET_SECONDS = 240.0
MAKEUP_MAX_PER_RUN = 1
REQUEST_TIMEOUT = 30
NETWORK_RETRY_DELAYS = (5, 15, 30, 60, 90)
SERVER_RETRY_DELAYS = (3, 10)
_started_at = None
_budget_seconds = DEFAULT_BUDGET_SECONDS
_config_warning = None
POLL_ACTIONS = ('silent-poll', 'silent-growth')
AUTH_HELPER_TIMEOUT = 10.0
AUTH_INPUT_LIMIT = 65536
AUTH_OUTPUT_LIMIT = 65536
TOKEN_LIMIT = 32768
_sensitive_values = set()
AUTH_REASONS = {'INVALID_FORMAT': '登录凭据格式无效，请检查凭据来源和客户端版本', 'UNSUPPORTED_ENVELOPE': '尚不支持此加密凭据格式，请更新脚本；重新登录不会改变加密格式', 'RUNTIME_NOT_FOUND': '未找到 WorkBuddy 客户端，请用 WORKBUDDY_EXE 指定其可执行文件', 'INVALID_RUNTIME_PATH': 'WORKBUDDY_EXE 不是可用的可执行文件，请核对路径', 'RUNTIME_UNAVAILABLE': '客户端运行时不支持所需的原生存储接口，请检查客户端和脚本版本', 'KEY_MISMATCH': '凭据与所选客户端的密钥不匹配，请用 WORKBUDDY_EXE 指定对应客户端', 'DECRYPT_FAILED': '加密凭据认证失败，请检查客户端版本及凭据是否完整', 'HELPER_TIMEOUT': '凭据处理超时，已停止子进程，请稍后重试', 'HELPER_PROTOCOL': '客户端凭据助手返回无效结果，请检查客户端和脚本版本'}
AUTH_HELPER_JS = "\n'use strict';\nconst crypto = require('crypto');\nconst inputLimit = 65536;\nconst failure = reason => { throw {reason}; };\nconst object = x => x !== null && typeof x === 'object' && !Array.isArray(x);\nfunction base64(value, length) {\n  if (typeof value !== 'string' || value.length > inputLimit) failure('INVALID_FORMAT');\n  const bytes = Buffer.from(value, 'base64');\n  if (bytes.toString('base64') !== value || (length !== undefined && bytes.length !== length))\n    failure('INVALID_FORMAT');\n  return bytes;\n}\nfunction utf8(bytes) {\n  const text = bytes.toString('utf8');\n  if (!Buffer.from(text, 'utf8').equals(bytes)) failure('INVALID_FORMAT');\n  return text;\n}\nfunction decode(value) {\n  if (!object(value) || Object.keys(value).sort().join(',') !== '$wbEncrypted,envelope' || value.$wbEncrypted !== 1)\n    failure('UNSUPPORTED_ENVELOPE');\n  let envelope;\n  try { envelope = JSON.parse(utf8(base64(value.envelope))); }\n  catch (e) { failure(e.reason || 'INVALID_FORMAT'); }\n  if (!object(envelope) || !Number.isInteger(envelope.suite)) failure('INVALID_FORMAT');\n  if (envelope.suite !== 1) failure('UNSUPPORTED_ENVELOPE');\n  if (Object.keys(envelope).sort().join(',') !== 'authTag,ciphertext,keyId,nonce,suite' ||\n      typeof envelope.keyId !== 'string' || !/^[0-9a-f]{16}$/.test(envelope.keyId)) failure('INVALID_FORMAT');\n  return {keyId: envelope.keyId, nonce: base64(envelope.nonce, 12),\n    tag: base64(envelope.authTag, 16), ciphertext: base64(envelope.ciphertext)};\n}\nfunction nativeStorage() {\n  try {\n    const storage = process._linkedBinding('electron_browser_workbuddy_storage');\n    if (typeof storage.loggerGet !== 'function') failure('RUNTIME_UNAVAILABLE');\n    return storage;\n  } catch (_) { failure('RUNTIME_UNAVAILABLE'); }\n}\nfunction decrypt(envelope) {\n  let payload;\n  try { payload = JSON.parse(nativeStorage().loggerGet()); }\n  catch (_) { failure('RUNTIME_UNAVAILABLE'); }\n  let key;\n  let plaintext;\n  try {\n    if (!object(payload) || payload.version !== 1) failure('RUNTIME_UNAVAILABLE');\n    let secret;\n    try { secret = base64(payload.atRestSecretKey, 32); }\n    catch (_) { failure('RUNTIME_UNAVAILABLE'); }\n    const empty = secret.every(b => b === 0);\n    secret.fill(0);\n    if (empty) failure('RUNTIME_UNAVAILABLE');\n    key = crypto.createHash('sha256').update(payload.atRestSecretKey, 'utf8').digest();\n    payload = null;\n    if (crypto.createHash('sha256').update(key).digest('hex').slice(0, 16) !== envelope.keyId)\n      failure('KEY_MISMATCH');\n    const lp = s => {\n      const bytes = Buffer.from(s, 'utf8');\n      const length = Buffer.alloc(4); length.writeUInt32BE(bytes.length);\n      return Buffer.concat([length, bytes]);\n    };\n    const aad = Buffer.concat([Buffer.from('WB-AAD\\0', 'ascii'), Buffer.from([1]),\n      lp('WBEV1'), lp('sym-v1'), Buffer.from([0, 0, 0, 1]), lp(envelope.keyId), Buffer.from([2, 0, 0])]);\n    try {\n      const cipher = crypto.createDecipheriv('aes-256-gcm', key, envelope.nonce, {authTagLength: 16});\n      cipher.setAAD(aad); cipher.setAuthTag(envelope.tag);\n      plaintext = Buffer.concat([cipher.update(envelope.ciphertext), cipher.final()]);\n    } catch (_) { failure('DECRYPT_FAILED'); }\n    const token = utf8(plaintext);\n    if (!token.length || token.length > 32768 || !/^[A-Za-z0-9._~+\\/-]+=*$/.test(token))\n      failure('INVALID_FORMAT');\n    return token;\n  } finally {\n    if (key) key.fill(0);\n    if (plaintext) plaintext.fill(0);\n  }\n}\nlet chunks = [], size = 0;\nfunction reply(value) {\n  process.stdout.write(JSON.stringify({version: 1, ...value}), () => process.exit(value.ok ? 0 : 1));\n}\nprocess.stdin.on('data', chunk => {\n  size += chunk.length;\n  if (size > inputLimit) reply({ok: false, reason: 'INVALID_FORMAT'});\n  else chunks.push(chunk);\n});\nprocess.stdin.on('error', () => reply({ok: false, reason: 'HELPER_PROTOCOL'}));\nprocess.stdin.on('end', () => {\n  try {\n    const request = JSON.parse(utf8(Buffer.concat(chunks))); chunks = [];\n    if (!object(request) || request.version !== 1) failure('HELPER_PROTOCOL');\n    if (request.operation === 'probe') {\n      nativeStorage();\n      if (!crypto.getCiphers().includes('aes-256-gcm')) failure('RUNTIME_UNAVAILABLE');\n      reply({ok: true, electron: process.versions.electron || 'unknown'});\n    } else if (request.operation === 'decrypt') {\n      reply({ok: true, accessToken: decrypt(decode(request.value))});\n    } else failure('HELPER_PROTOCOL');\n  } catch (e) {\n    const reasons = ['INVALID_FORMAT','UNSUPPORTED_ENVELOPE','RUNTIME_UNAVAILABLE',\n      'KEY_MISMATCH','DECRYPT_FAILED','HELPER_PROTOCOL'];\n    reply({ok: false, reason: reasons.includes(e.reason) ? e.reason : 'HELPER_PROTOCOL'});\n  }\n});\n"

class AuthError(Exception):
    """Only fixed, non-sensitive messages may cross the credential boundary."""

    def __init__(self, reason, result='AUTH_ERROR'):
        self.reason = reason
        self.result = result
        super().__init__(AUTH_REASONS.get(reason, '本地未找到有效登录会话，请先登录客户端'))

    def output(self):
        return {'result': self.result, 'reason': self.reason, 'report': str(self), 'needs_attention': True}

def _valid_token(token):
    return isinstance(token, str) and 0 < len(token) <= TOKEN_LIMIT and (re.fullmatch('[A-Za-z0-9._~+/-]+=*', token) is not None)

def _validate_session(session):
    if not isinstance(session, dict):
        raise AuthError('INVALID_FORMAT')
    for field in ('auth', 'account'):
        if session.get(field) is None:
            raise AuthError('MISSING_SESSION', 'NO_SESSION')
        if not isinstance(session[field], dict):
            raise AuthError('INVALID_FORMAT')
    if session['auth'].get('accessToken') in (None, '') or session['account'].get('uid') in (None, ''):
        raise AuthError('MISSING_SESSION', 'NO_SESSION')
    for source, fields in ((session['account'], ('uid', 'enterpriseId')), (session['auth'], ('domain',))):
        for field in fields:
            value = source.get(field)
            if value not in (None, '') and (not isinstance(value, str) or re.fullmatch('[\\x21-\\x7e]{1,2048}', value) is None):
                raise AuthError('INVALID_FORMAT')
    endpoint = session['auth'].get('endpoint')
    if endpoint in (None, ''):
        endpoint = DEFAULT_ENDPOINT
    if not isinstance(endpoint, str) or re.search('[\\s\\x00-\\x1f\\x7f]', endpoint):
        raise AuthError('INVALID_FORMAT')
    try:
        parsed = urllib.parse.urlsplit(endpoint)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError()
        parsed.port
    except ValueError:
        raise AuthError('INVALID_FORMAT') from None

def _token_format(value):
    if isinstance(value, str):
        if not _valid_token(value):
            raise AuthError('INVALID_FORMAT')
        return 'plaintext'
    if not isinstance(value, dict):
        raise AuthError('INVALID_FORMAT')
    if set(value) != {'$wbEncrypted', 'envelope'} or type(value.get('$wbEncrypted')) is not int or value['$wbEncrypted'] != 1:
        raise AuthError('UNSUPPORTED_ENVELOPE')
    try:
        encoded = value['envelope']
        if not isinstance(encoded, str) or not 0 < len(encoded) <= AUTH_INPUT_LIMIT - 1024:
            raise ValueError()
        raw = base64.b64decode(encoded, validate=True)
        if base64.b64encode(raw).decode('ascii') != encoded:
            raise ValueError()
        envelope = json.loads(raw.decode('utf-8'))
        if not isinstance(envelope, dict) or type(envelope.get('suite')) is not int:
            raise ValueError()
        if envelope['suite'] != 1:
            raise AuthError('UNSUPPORTED_ENVELOPE')
        if set(envelope) != {'suite', 'keyId', 'nonce', 'authTag', 'ciphertext'}:
            raise ValueError()
        if not isinstance(envelope['keyId'], str) or not re.fullmatch('[0-9a-f]{16}', envelope['keyId']):
            raise ValueError()
        for field, length in (('nonce', 12), ('authTag', 16), ('ciphertext', None)):
            data = envelope[field]
            if not isinstance(data, str):
                raise ValueError()
            decoded = base64.b64decode(data, validate=True)
            if base64.b64encode(decoded).decode('ascii') != data or (length is not None and len(decoded) != length):
                raise ValueError()
        return 'sym-v1'
    except (ValueError, TypeError, KeyError):
        raise AuthError('INVALID_FORMAT') from None

def _mac_runtime(bundle):
    try:
        with open(os.path.join(bundle, 'Contents', 'Info.plist'), 'rb') as f:
            name = plistlib.load(f).get('CFBundleExecutable')
        if not isinstance(name, str) or not name or name in ('.', '..') or ('/' in name) or ('\\' in name):
            return None
        return os.path.join(bundle, 'Contents', 'MacOS', name)
    except (OSError, ValueError, TypeError, AttributeError):
        return None

def _registry_text(registry, key, field):
    """Read only string registry values; expand REG_EXPAND_SZ using Windows."""
    try:
        value, kind = registry.QueryValueEx(key, field)
        if not isinstance(value, str) or kind not in (registry.REG_SZ, registry.REG_EXPAND_SZ):
            return None
        if kind == registry.REG_EXPAND_SZ:
            value = registry.ExpandEnvironmentStrings(value)
        return value
    except OSError:
        return None

def _registry_runtime_path(value, icon=False):
    """Parse a directory or DisplayIcon; never interpret an uninstall command."""
    if not isinstance(value, str) or re.search('[\\x00-\\x1f\\x7f]', value):
        return None
    value = value.strip()
    if value.startswith('"'):
        suffix = '(?:\\s*,\\s*-?\\d+)?' if icon else ''
        match = re.fullmatch('"([^"]+)"' + suffix, value)
        if not match:
            return None
        value = match[1]
    elif '"' in value:
        return None
    elif icon:
        value = re.sub(',\\s*-?\\d+$', '', value).rstrip()
    if not ntpath.splitdrive(value)[0] or not ntpath.isabs(value):
        return None
    if not icon:
        value = ntpath.join(value, 'WorkBuddy.exe')
    value = ntpath.normpath(value)
    if ntpath.basename(value).casefold() != 'workbuddy.exe':
        return None
    return value

def _windows_registry_runtimes():
    """Yield existing clients from user/machine uninstall entries, lazily.

    Keep the import and all platform-specific constants inside this Windows-only
    fallback so importing the script on macOS/Linux needs no winreg module.
    """
    if sys.platform != 'win32':
        return
    try:
        import winreg
    except ImportError:
        return
    subkey = 'SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall'
    seen = set()
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ | view) as root:
                    count = winreg.QueryInfoKey(root)[0]
                    for index in range(count):
                        try:
                            with winreg.OpenKey(root, winreg.EnumKey(root, index), 0, winreg.KEY_READ | view) as entry:
                                name = _registry_text(winreg, entry, 'DisplayName')
                                if not name or not re.fullmatch('WorkBuddy(?:\\s+\\d+(?:\\.\\d+)*(?:[-+][0-9A-Za-z.-]+)?)?', name.strip(), re.IGNORECASE):
                                    continue
                                for field in ('InstallLocation', 'DisplayIcon'):
                                    path = _registry_runtime_path(_registry_text(winreg, entry, field), icon=field == 'DisplayIcon')
                                    if path and ntpath.normcase(path) not in seen:
                                        seen.add(ntpath.normcase(path))
                                        if os.path.isfile(path):
                                            yield path
                        except OSError:
                            continue
            except OSError:
                continue

def find_workbuddy_runtime():
    override = os.environ.get('WORKBUDDY_EXE')
    if override:
        path = os.path.abspath(os.path.expanduser(override))
        if not os.path.isfile(path) or (os.name != 'nt' and (not os.access(path, os.X_OK))):
            raise AuthError('INVALID_RUNTIME_PATH')
        return path
    home = os.path.expanduser('~')
    candidates = []
    if sys.platform == 'win32':
        local = os.environ.get('LOCALAPPDATA') or os.path.join(home, 'AppData', 'Local')
        candidates.append(os.path.join(local, 'Programs', 'WorkBuddy', 'WorkBuddy.exe'))
        for name in ('ProgramFiles', 'ProgramFiles(x86)'):
            if os.environ.get(name):
                candidates.append(os.path.join(os.environ[name], 'WorkBuddy', 'WorkBuddy.exe'))
    elif sys.platform == 'darwin':
        candidates = [_mac_runtime(os.path.join(root, 'WorkBuddy.app')) for root in ('/Applications', os.path.join(home, 'Applications'))]
    for path in candidates:
        if path and os.path.isfile(path) and (os.name == 'nt' or os.access(path, os.X_OK)):
            return os.path.abspath(path)
    if sys.platform == 'win32':
        with closing(_windows_registry_runtimes()) as entries:
            registered = next(entries, None)
        if registered:
            return registered
    raise AuthError('RUNTIME_NOT_FOUND')

def _run_auth_helper(exe, request):
    """Bound every pipe and deadline; never include captured data in exceptions."""
    data = json.dumps(dict(request, version=1), ensure_ascii=True).encode('ascii')
    if len(data) > AUTH_INPUT_LIMIT:
        raise AuthError('INVALID_FORMAT')
    timeout = min(AUTH_HELPER_TIMEOUT, _budget_left())
    if timeout <= 0:
        raise AuthError('HELPER_TIMEOUT')
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith(('NODE_', 'ELECTRON_', 'WORKBUDDY_'))}
    env['ELECTRON_RUN_AS_NODE'] = '1'
    try:
        process = subprocess.Popen([exe, '-e', AUTH_HELPER_JS], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, shell=False, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except OSError:
        raise AuthError('RUNTIME_UNAVAILABLE') from None
    output = {}
    failed = threading.Event()

    def read_pipe(name, pipe, limit):
        try:
            captured = pipe.read(limit + 1)
            if len(captured) > limit:
                failed.set()
                process.kill()
            else:
                output[name] = captured
        except OSError:
            failed.set()

    def write_pipe():
        try:
            process.stdin.write(data)
            process.stdin.close()
        except OSError:
            failed.set()
    workers = [threading.Thread(target=read_pipe, args=('stdout', process.stdout, AUTH_OUTPUT_LIMIT), daemon=True), threading.Thread(target=read_pipe, args=('stderr', process.stderr, 8192), daemon=True), threading.Thread(target=write_pipe, daemon=True)]
    deadline = time.monotonic() + timeout
    try:
        for worker in workers:
            worker.start()
        try:
            process.wait(timeout=max(0.001, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            raise AuthError('HELPER_TIMEOUT') from None
        for worker in workers:
            worker.join(max(0, deadline - time.monotonic()))
        if any((worker.is_alive() for worker in workers)):
            raise AuthError('HELPER_TIMEOUT')
        if failed.is_set():
            raise AuthError('HELPER_PROTOCOL')
        try:
            reply = json.loads(output.get('stdout', b'').decode('utf-8'))
        except (ValueError, UnicodeError):
            raise AuthError('HELPER_PROTOCOL') from None
        if not isinstance(reply, dict) or type(reply.get('version')) is not int or reply['version'] != 1:
            raise AuthError('HELPER_PROTOCOL')
        if reply.get('ok') is False and process.returncode == 1:
            reason = reply.get('reason')
            raise AuthError(reason if isinstance(reason, str) and reason in AUTH_REASONS else 'HELPER_PROTOCOL')
        if reply.get('ok') is not True or process.returncode != 0:
            raise AuthError('HELPER_PROTOCOL')
        if request['operation'] == 'decrypt':
            if set(reply) != {'version', 'ok', 'accessToken'} or not _valid_token(reply.get('accessToken')):
                raise AuthError('HELPER_PROTOCOL')
        elif set(reply) != {'version', 'ok', 'electron'} or not isinstance(reply.get('electron'), str) or re.fullmatch('[0-9A-Za-z.+-]{1,64}', reply['electron']) is None:
            raise AuthError('HELPER_PROTOCOL')
        return reply
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        for worker in workers:
            if worker.ident is not None:
                worker.join(1)
        if not any((worker.is_alive() for worker in workers)):
            for pipe in (process.stdin, process.stdout, process.stderr):
                pipe.close()

def resolve_session(session):
    _validate_session(session)
    token = session['auth']['accessToken']
    if _token_format(token) != 'plaintext':
        token = _run_auth_helper(find_workbuddy_runtime(), {'operation': 'decrypt', 'value': token})['accessToken']
    _sensitive_values.add(token)
    return dict(session, auth=dict(session['auth'], accessToken=token))

def run_doctor(session):
    _validate_session(session)
    kind = _token_format(session['auth']['accessToken'])
    out = {'result': 'AUTH_READY', 'credential_format': kind, 'needs_attention': False, 'report': '本地凭据格式有效；未验证服务端登录状态', 'online_checked': False}
    if kind == 'sym-v1':
        runtime = find_workbuddy_runtime()
        probe = _run_auth_helper(runtime, {'operation': 'probe'})
        out.update(runtime=runtime, electron_version=probe['electron'], report='已识别加密凭据，运行时具备解密能力；未提取密钥、未验证解密或服务端登录状态')
    return out

def _auth_failure(code):
    if code == 401:
        return {'result': 'AUTH_REJECTED', 'http': code, 'needs_attention': True, 'report': '服务端拒绝认证（HTTP 401），请检查客户端登录状态、凭据对应的服务地址及脚本版本'}
    return {'result': 'FORBIDDEN', 'http': code, 'needs_attention': True, 'report': '服务端拒绝此操作（HTTP 403），请检查账号权限或活动条件；不能据此判断登录过期'}

def _parse_budget(default=DEFAULT_BUDGET_SECONDS, maximum=MAX_BUDGET_SECONDS):
    """解析预算环境变量。非法值/越界值一律夹到安全区间——绝不能在这里抛异常。

    这段逻辑曾写在模块顶层，WORKBUDDY_BUDGET_SECONDS=abc 会让进程在 main() 的
    try/except 生效之前就崩掉，silent 模式下当天日志整条为空。

    上限按调用方传入的 maximum 算：轮询任务的 ExecutionTimeLimit 比签到任务短，
    共用一个 540s 的上限等于对轮询任务没有上限，跑穿一样会被杀在写日志之前。
    """
    raw = os.environ.get('WORKBUDDY_BUDGET_SECONDS')
    if not raw:
        return (default, None)
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return (default, 'WORKBUDDY_BUDGET_SECONDS=%r 不是数字，已回落 %s 秒' % (raw, int(default)))
    if val <= 0:
        return (default, 'WORKBUDDY_BUDGET_SECONDS=%s 必须为正数，已回落 %s 秒' % (raw, int(default)))
    if val > maximum:
        return (maximum, 'WORKBUDDY_BUDGET_SECONDS=%s 超过本命令上限，已夹到 %s 秒（须小于该定时任务的 ExecutionTimeLimit）' % (raw, int(maximum)))
    return (val, None)

def _start_budget(action=None):
    """启动预算时钟；配置非法时记下告警，由 emit 带进输出而不是静默回落。

    轮询类命令（silent-poll / silent-growth）用更短的默认值和更低的上限，用户显式设置
    环境变量时仍以环境变量为准，但同样夹在该命令自己的上限内。
    """
    global _started_at, _budget_seconds, _config_warning
    if action in POLL_ACTIONS:
        default, maximum = (POLL_BUDGET_SECONDS, POLL_MAX_BUDGET_SECONDS)
    else:
        default, maximum = (DEFAULT_BUDGET_SECONDS, MAX_BUDGET_SECONDS)
    _budget_seconds, _config_warning = _parse_budget(default, maximum)
    _started_at = time.monotonic()

def _budget_left():
    """本次运行还剩多少秒可用于网络请求。"""
    if _started_at is None:
        return _budget_seconds
    return _budget_seconds - (time.monotonic() - _started_at)

def find_auth_file():
    """按平台探测 WorkBuddy 桌面端写出的登录凭据文件，支持环境变量覆盖。

    返回 (path_or_None, looked_in) —— looked_in 始终是脚本实际检查过的路径列表，
    供 NO_AUTH 报错时显示，避免与代码实现漂移。
    """
    override = os.environ.get('WORKBUDDY_AUTH_FILE')
    if override:
        return (override if os.path.exists(override) else None, [override])
    home = os.path.expanduser('~')
    local = os.environ.get('LOCALAPPDATA') or os.path.join(home, 'AppData', 'Local')
    xdg_data = os.environ.get('XDG_DATA_HOME') or os.path.join(home, '.local', 'share')
    candidates = [os.path.join(local, AUTH_BASENAME), os.path.join(home, 'Library', 'Application Support', AUTH_BASENAME), os.path.join(xdg_data, CLI_AUTH_BASENAME), os.path.join(home, '.config', AUTH_BASENAME), os.path.join(home, '.workbuddy', 'auth', 'workbuddy-desktop.info')]
    for c in candidates:
        if os.path.exists(c):
            return (c, candidates)
    return (None, candidates)

def load_session(auth_file):
    with open(auth_file, 'r', encoding='utf-8') as f:
        return json.load(f)

def load_session_retry(auth_file, attempts=3, delay=2.0):
    """带重试的凭据读取。

    WorkBuddy 客户端刷新 token 时会短暂独占凭据文件，恰好在那一刻读取就是
    PermissionError（实测：定时任务两次撞锁、整轮直接放弃）。锁是瞬时的，
    等两秒再试即可；重试仍失败才真正报错。
    """
    last = None
    for i in range(attempts):
        try:
            return load_session(auth_file)
        except PermissionError as e:
            last = e
            if i < attempts - 1:
                time.sleep(delay)
    raise last

def build_headers(session):
    _validate_session(session)
    auth = session.get('auth') or {}
    account = session.get('account') or {}
    token = auth.get('accessToken')
    uid = account.get('uid')
    if not _valid_token(token):
        raise AuthError('INVALID_FORMAT')
    headers = {'Accept': 'application/json', 'Authorization': 'Bearer %s' % token, 'Content-Type': 'application/json', 'X-User-Id': uid, 'User-Agent': 'WorkBuddy'}
    if account.get('enterpriseId'):
        headers['X-Enterprise-Id'] = account['enterpriseId']
        headers['X-Tenant-Id'] = account['enterpriseId']
    if auth.get('domain'):
        headers['X-Domain'] = auth['domain']
    return headers

def _request(url, headers, method='GET', payload=None, timeout=REQUEST_TIMEOUT):
    body = None
    if payload is not None:
        body = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read().decode('utf-8')
            try:
                return (resp.status, json.loads(raw))
            except Exception:
                return (resp.status, {'raw': raw[:500]})
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', 'replace')
        try:
            return (e.code, json.loads(raw))
        except Exception:
            return (e.code, {'raw': raw[:500]})
    except urllib.error.URLError as e:
        return (CODE_NO_NETWORK, {'error': str(e.reason)})
    except Exception as e:
        return (CODE_NO_NETWORK, {'error': str(e)})

def post(url, headers, payload=None, retry=False):
    """POST 默认不重试：抽奖/领奖等写操作若在服务端处理完成后才超时，重试会重复提交。"""
    return _request_with_retry(url, headers, method='POST', payload=payload, retry=retry)

def get(url, headers):
    return _request_with_retry(url, headers, method='GET', retry=True)

def _retry_delays(code):
    """该失败码对应的退避节奏；空元组表示"重试也没用"，立刻如实返回。

    只有这两类值得再试：本机网络没就绪（-1）、服务端抖动（5xx）。
    4xx 是业务规则或参数问题，重试一百次也是同一个答案；CODE_BUDGET_OUT 更是
    连请求都没发出去，再试只会离被强杀更近一步。
    """
    if code == CODE_NO_NETWORK:
        return NETWORK_RETRY_DELAYS
    if code >= 500:
        return SERVER_RETRY_DELAYS
    return ()

def _request_with_retry(url, headers, method='GET', payload=None, retry=True):
    """带时间预算的请求：失败按退避节奏重试，超时上限随剩余预算收缩，预算不足则直接放弃。

    返回 CODE_BUDGET_OUT 表示"没发出去，因为再发就要超出任务时限了"——调用方据此提前
    收尾，保证 emit 一定能执行到。

    退避节奏见 NETWORK_RETRY_DELAYS：早期版本固定"重试 1 次、隔 5 秒"，对定时任务
    最常见的失败场景（刚开机/唤醒，网络还要几十秒才就绪）几乎是无效重试。
    """
    if _budget_left() <= 1:
        return (CODE_BUDGET_OUT, {'error': '已达本次运行时间预算，跳过剩余请求'})
    code, body = _request(url, headers, method=method, payload=payload, timeout=max(1, min(REQUEST_TIMEOUT, _budget_left())))
    if not retry:
        return (code, body)
    attempts = {}
    while True:
        delays = _retry_delays(code)
        if not delays:
            return (code, body)
        used = attempts.get(delays, 0)
        if used >= len(delays):
            return (code, body)
        delay = delays[used]
        attempts[delays] = used + 1
        if _budget_left() <= delay + REQUEST_TIMEOUT:
            return (code, body)
        time.sleep(delay)
        code, body = _request(url, headers, method=method, payload=payload, timeout=max(1, min(REQUEST_TIMEOUT, _budget_left())))

def _is_hard_failure(code):
    """是否属于"需要人关注"的失败。

    5xx 与"没拿到响应"算硬失败；4xx 绝大多数是业务规则（如派 Buddy 已达每日上限、
    活动已结束），属于每天的正常状态，若计入退出码会让计划任务天天报红。
    """
    return code >= 500 or code in (CODE_NO_NETWORK, CODE_BUDGET_OUT)

def _http_label(code):
    """把伪 HTTP 码翻译成人话；-1/-2 是脚本自定义的"没拿到响应"标记。"""
    if code == CODE_NO_NETWORK:
        return '网络不可达'
    if code == CODE_BUDGET_OUT:
        return '时间预算耗尽'
    return 'HTTP %s' % code

def _is_no_chance(msg):
    """抽奖失败是否只是"没有次数"——这是常态，不是故障。

    服务端对"次数为 0"返回 400 + `insufficient lottery chance balance`，和真正的
    参数错误（`invalid request`）同为 400，只看状态码会把两者混为一谈：把常态记成
    失败，轮询就会每轮强制落盘、还会把失败数算进汇报。
    """
    m = str(msg or '').lower()
    if not m:
        return False
    if 'insufficient' in m or 'not enough' in m:
        return 'chance' in m or 'balance' in m
    return 'no chance' in m

def _is_unknown_tier(code, body):
    """连登兑换是否因为"tier 这个值本身不认识"被拒——用于判断要不要换一种写法重试。

    /redeem 的 tier 是**档位标识字符串**（"7d"/"14d"/"28d"），权威来源是
    GET /streak 的 redemption_status.tiers[].tier。接口哪天改回收天数，脚本就会
    三档全废且看不出原因，所以保留这条兜底：档位标识被判 unknown tier 时退回天数
    再试一次。这类 400 发生在参数校验阶段，服务端没兑换任何东西，重试不会重复领取；
    `invalid request` 这类业务拒绝不在此列，不能重试。

    历史教训：2026-09-11 曾据"传天数返回 invalid request"误判 tier 要收数字，
    导致兑换模块连坏三个版本——"业务拒绝"的错误文案不能反推参数格式正确，
    必须拿到一次成功响应才算验证过。
    """
    if code != 400:
        return False
    m = str(dig(body, 'msg') or '').lower()
    return 'tier' in m and ('unknown' in m or 'unsupported' in m or 'invalid' in m)

def _is_tier_locked(code, body):
    """未解锁档位：403 + 「连续登录天数不足」——这是常态，不是故障。

    不加这条的话，未解锁档位会被计进 failures，轮询每轮都判定"有失败"从而强制
    落盘，真正有价值的记录会被这类常态信息淹没。
    """
    if code != 403:
        return False
    m = str(dig(body, 'msg') or '')
    return '天数不足' in m or '不足' in m

def dig(obj, key):
    """在可能被 data/result 包裹的响应里找字段，兼容信封结构。"""
    if isinstance(obj, dict):
        if key in obj and obj[key] is not None:
            return obj[key]
        for k in ('data', 'result', 'resp', 'response'):
            if k in obj and isinstance(obj[k], dict):
                r = dig(obj[k], key)
                if r is not None:
                    return r
    return None

def _api_success(code, body):
    """同时检查 HTTP 状态和 JSON 业务码，避免把业务失败当作状态转换成功。"""
    business_code = body.get('code') if isinstance(body, dict) else None
    return 200 <= code < 300 and (business_code is None or (type(business_code) is int and business_code == 0))

def _makeup_candidates(streak_body, heatmap_body):
    """从日历选断登日期；makeup_dates 是已补登历史，不能作为待办清单。

    对齐官方成长中心：当月、今天之前、活动上线之后、score == 0。
    使用 heatmap.today 的服务端日期，避免本机时区把月初/午夜算错。
    日历缺项不推测为零；结构异常时整段停止，不能靠猜测消耗补登卡。
    """

    def parse_date(value):
        if not isinstance(value, str) or re.fullmatch('\\d{4}-\\d{2}-\\d{2}', value) is None:
            raise ValueError('补登日期格式无效')
        return date.fromisoformat(value)
    today_obj = dig(heatmap_body, 'today')
    if not isinstance(today_obj, dict):
        raise ValueError('活跃日历缺少服务端日期')
    today = parse_date(today_obj.get('date'))
    launch = parse_date(dig(streak_body, 'launch_date'))
    start = max(today.replace(day=1), launch, date(2026, 6, 17))
    streak = dig(streak_body, 'streak')
    if not isinstance(streak, dict):
        raise ValueError('连登状态格式无效')
    made_up = streak.get('makeup_dates', [])
    if not isinstance(made_up, list):
        raise ValueError('已补登记录格式无效')
    made_up = {parse_date(value) for value in made_up}
    cells = dig(heatmap_body, 'cells')
    if not isinstance(cells, list):
        raise ValueError('活跃日历缺少日期列表')
    scores = {}
    for cell in cells:
        if not isinstance(cell, dict):
            raise ValueError('活跃日历记录格式无效')
        day = parse_date(cell.get('date'))
        score = cell.get('score')
        if type(score) not in (int, float) or not math.isfinite(score) or score < 0:
            raise ValueError('活跃日历分数无效')
        if day in scores and scores[day] != score:
            raise ValueError('活跃日历同一日期的记录冲突')
        scores[day] = score
    return sorted((day.isoformat() for day, score in scores.items() if start <= day < today and score == 0 and (day not in made_up)), reverse=True)

def fmt_credit(v):
    """积分显示用：能转 int 就转，否则原样返回（OverflowError 同理，见 as_int）。"""
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):
        return v

def as_int(v, default=0):
    """把可能为字符串的数字安全地转成 int，避免与数值比较/累加时抛异常。

    OverflowError 必须一并捕获：json.loads 默认接受 Infinity，服务端返回该字面量时
    v 已经是 float('inf')，int(v) 抛的是 OverflowError 而非 ValueError。
    """
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        return int(float(v))
    except (TypeError, ValueError, OverflowError):
        return default

def _first_int(body, key, fallback=0):
    """优先取响应里的实际数值，挖不到才用回落值。

    任务列表里的 reward_credit 只是活动配置，与服务端这次实际发放的可能不同；
    上报按响应值才不会虚报，响应里没有时才退回配置值。
    """
    if isinstance(body, dict):
        v = dig(body, key)
        if v is not None:
            return as_int(v)
    return as_int(fallback)

def _fmt_eta(arrive_at, server_now):
    """把服务端返回的 Unix 时间戳换算成"还有多久回来"。

    任一时间戳缺失/非法就返回空串——这是纯展示信息，绝不能因为它让整轮执行失败。
    json.loads 默认接受 Infinity/NaN，非有限值同样按缺失处理，否则会汇报出
    "约 inf 小时后回"这种话。
    """
    try:
        left = float(arrive_at) - float(server_now)
    except (TypeError, ValueError, OverflowError):
        return ''
    if not math.isfinite(left):
        return ''
    if left <= 0:
        return '，已到达待领取'
    minutes = int(round(left / 60.0))
    if minutes < 60:
        return '，约 %d 分钟后回' % max(1, minutes)
    return '，约 %.1f 小时后回' % (left / 3600.0)

def _env_flag(name):
    """开关型环境变量是否为真；空串与 0/false/no/off 一律视为关。"""
    return (os.environ.get(name) or '').strip().lower() in ('1', 'true', 'yes', 'on')

def _client_token(prefix='u'):
    """活动接口（抽奖/连登兑换）要求的防重放 token。

    官方前端用 `crypto.randomUUID()` 拼成 "u-<uuid>"，服务端只做幂等去重、
    不校验格式；缺了它 /lottery/draw 直接 400（实测过）。
    """
    return '%s-%s' % (prefix, uuid.uuid4())
_REDEEM_REWARDS = {'7d': '+2 能量 +1 补登卡 +1 次抽奖', '14d': '+50 积分 +3 能量 +1 补登卡 +1 次抽奖', '28d': '+150 积分 +5 能量 +1 补登卡 +1 次抽奖'}
_REDEEM_TIERS = (('7d', 'starter', '入门', 7), ('14d', 'advanced', '进阶', 14), ('28d', 'legendary', '巅峰', 28))

def _redeem_reward_desc(body, tier):
    """兑换成功的奖励描述：优先拼服务端实发的 *_granted，挖不到才回落官方文案。

    注意实发字段名带 _granted 后缀（credit_granted / energy_granted /
    cards_granted / chances_granted）；直接读 `credit` 恒为空，会把兑换所得漏计。
    """
    bits = []
    credit = as_int(dig(body, 'credit_granted'), 0)
    energy = as_int(dig(body, 'energy_granted'), 0)
    cards = as_int(dig(body, 'cards_granted'), 0)
    chances = as_int(dig(body, 'chances_granted'), 0)
    if credit:
        bits.append('+%s 积分' % fmt_credit(credit))
    if energy:
        bits.append('+%s 能量' % fmt_credit(energy))
    if cards:
        bits.append('+%s 补登卡' % fmt_credit(cards))
    if chances:
        bits.append('+%s 次抽奖' % fmt_credit(chances))
    if bits:
        return '（%s）' % ' '.join(bits)
    return '（%s）' % _REDEEM_REWARDS.get(tier, '奖励已到账')

def _is_already_checked_in(cbody):
    """领取接口返回是否表示"今日已签"（兼容 null 与 400+code10001）。"""
    if cbody is None:
        return True
    if isinstance(cbody, dict):
        msg = cbody.get('msg') or ''
        if cbody.get('code') == 10001 or '已签' in msg:
            return True
    return False

def _already_report(status, via=None):
    """根据状态构造"今日已签"汇报 dict。"""
    today_credit = dig(status, 'today_credit') or dig(status, 'daily_credit')
    streak_days = dig(status, 'streak_days')
    total_credits = dig(status, 'total_credits')
    is_streak_day = dig(status, 'is_streak_day')
    next_streak_day = dig(status, 'next_streak_day')
    inner = []
    if today_credit is not None:
        inner.append('今日 +%s' % fmt_credit(today_credit))
    if streak_days is not None:
        inner.append('连续 %s 天' % streak_days)
    if total_credits is not None:
        inner.append('累计 %s 积分' % fmt_credit(total_credits))
    prefix = via or '今日已签过'
    report = '%s（%s）' % (prefix, '，'.join(inner)) if inner else prefix
    return {'result': 'ALREADY', 'report': report, 'today_credit': today_credit, 'streak_days': streak_days, 'total_credits': total_credits, 'is_streak_day': is_streak_day, 'next_streak_day': next_streak_day}
