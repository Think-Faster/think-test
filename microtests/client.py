# -*- coding: utf-8 -*-
"""Общая часть микротестов: параметры стенда, вход и запросы к сервисам.

Адреса и имена живут в config.json, поэтому при переезде стенда или смене
маршрутов код не трогают:

    base_url        адрес хоста, куда смотрит reverse proxy
    backend_prefix  общий префикс бэкенда, например /api
    services[]      name — как сервис называется в выводе,
                    suffix — его суффикс за префиксом (bff, ml…),
                    health — путь ручки проверки,
                    base_url — свой хост, если сервис живёт не за прокси
    auth            куда и в каком виде отправлять логин и пароль,
                    как называется кука с токеном и как её передавать

Адрес ручки собирается как base_url + backend_prefix + suffix + путь.

Логин, пароль и токен в config.json не пишутся: только переменные окружения
MT_LOGIN, MT_PASSWORD, MT_TOKEN или файл .env рядом со скриптом, который
git не видит. MT_BASE_URL подменяет хост без правки config.json.

Вход устроен как у браузера: логин и пароль уходят в сервис аутентификации,
токен приходит в куках ответа, дальше каждый запрос несёт эти куки. Токен
живёт 10 минут, поэтому нигде не хранится и берётся заново при каждом
запуске. Если токен уже есть на руках, его можно передать напрямую.
"""
import http.client
import json
import os
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE / 'config.json'
DOTENV = HERE / '.env'

ENV_LOGIN = 'MT_LOGIN'
ENV_PASSWORD = 'MT_PASSWORD'
ENV_TOKEN = 'MT_TOKEN'
ENV_BASE_URL = 'MT_BASE_URL'


class ConfigError(Exception):
    """Параметры стенда не прочитались или заданы неполно."""


class Unreachable(Exception):
    """Сервис не ответил вовсе: нет соединения, таймаут, DNS, TLS."""


class LoginFailed(Exception):
    """Сервис аутентификации ответил, но токена не выдал."""


class Response(object):
    """Ответ сервиса: код, заголовки, тело и время ответа в миллисекундах."""

    def __init__(self, status, headers, body, elapsed_ms):
        self.status = status
        self.headers = headers
        self.body = body
        self.elapsed_ms = elapsed_ms

    @property
    def ok(self):
        return 200 <= self.status < 300

    def text(self):
        return self.body.decode('utf-8', 'replace')

    def json(self):
        return json.loads(self.body.decode('utf-8'))


def load_dotenv(path=DOTENV):
    """Читает KEY=VALUE из .env. Уже заданные переменные окружения главнее."""
    path = Path(path)
    if not path.is_file():
        return
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if line.startswith('export '):
            line = line[len('export '):]
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = (part.strip() for part in line.split('=', 1))
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
            value = value[1:-1]
        os.environ.setdefault(key, value)


def load_config(path=None):
    """Читает config.json и проверяет, что по нему вообще можно пойти."""
    path = Path(path) if path else CONFIG
    try:
        cfg = json.loads(path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        raise ConfigError('нет файла %s' % path)
    except ValueError as e:
        raise ConfigError('%s: ошибка в JSON — %s' % (path.name, e))

    if os.environ.get(ENV_BASE_URL):
        cfg['base_url'] = os.environ[ENV_BASE_URL]
    if not cfg.get('base_url'):
        raise ConfigError('%s: не задан base_url' % path.name)
    services = cfg.get('services')
    if not isinstance(services, list) or not services:
        raise ConfigError('%s: список services пуст' % path.name)
    for number, service in enumerate(services, 1):
        if not service.get('name'):
            raise ConfigError('%s: у сервиса №%d нет name' % (path.name, number))
    return cfg


def url_for(cfg, suffix='', path='', base_url=None):
    """Адрес ручки: хост + префикс бэкенда + суффикс сервиса + путь."""
    base = (base_url or cfg['base_url']).rstrip('/')
    parts = [cfg.get('backend_prefix', ''), suffix, path]
    tail = '/'.join(p.strip('/') for p in parts if p and p.strip('/'))
    return '%s/%s' % (base, tail) if tail else base


def parse_set_cookie(headers):
    """Имя и значение из каждого Set-Cookie; атрибуты (Path, HttpOnly…) не нужны."""
    cookies = {}
    for header in headers:
        pair = header.split(';', 1)[0]
        if '=' in pair:
            name, value = pair.split('=', 1)
            cookies[name.strip()] = value.strip()
    return cookies


def describe_network_error(error, timeout):
    """Причина, по которой ответа не было, — человеческими словами."""
    reason = getattr(error, 'reason', error)
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return 'не ответил за %g с' % timeout
    if isinstance(reason, ConnectionRefusedError):
        return 'нет соединения: порт закрыт'
    if isinstance(reason, (ConnectionResetError, ConnectionAbortedError,
                           http.client.RemoteDisconnected)):
        return 'соединение оборвано'
    if isinstance(reason, socket.gaierror):
        return 'адрес хоста не найден'
    if isinstance(reason, ssl.SSLCertVerificationError):
        return ('сертификат не прошёл проверку — если на стенде самоподписанный, '
                'поставьте verify_tls: false')
    if isinstance(reason, ssl.SSLError):
        return 'ошибка TLS: %s' % reason
    if isinstance(reason, http.client.HTTPException):
        return 'ответ не похож на HTTP — не тот порт или не та схема (http/https)'
    return 'нет соединения: %s' % reason


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # куки входа приходят в первом ответе, а перенаправление на страницу входа
    # не должно выглядеть как живой сервис, поэтому за редиректами не ходим
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client(object):
    """HTTP-клиент стенда: держит куки входа и подставляет токен в запросы."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.auth = cfg.get('auth') or {}
        self.timeout = float(cfg.get('timeout_seconds', 5))
        self.token_cookie = self.auth.get('token_cookie', 'access_token')
        self.cookies = {}
        self.token = None

        context = ssl.create_default_context()
        if not cfg.get('verify_tls', True):
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        self._opener = urllib.request.build_opener(
            _NoRedirect, urllib.request.HTTPSHandler(context=context))

    def url(self, service, path=''):
        """Адрес внутри сервиса из config.json: client.url(service, '/items')."""
        return url_for(self.cfg, service.get('suffix', ''), path,
                       service.get('base_url'))

    def use_token(self, token):
        """Готовый токен: уходит в запросы так же, как полученный при входе."""
        self.token = token
        self.cookies[self.token_cookie] = token

    def login(self, login, password):
        """Отправляет логин и пароль, забирает токен из кук ответа.

        Бросает Unreachable, если сервис не ответил, и LoginFailed, если
        ответил, но токена не выдал. Возвращает ответ сервиса аутентификации.
        """
        auth = self.auth
        if not auth.get('path'):
            raise ConfigError('в config.json не задан auth.path — адрес входа')

        fields = {auth.get('login_field', 'login'): login,
                  auth.get('password_field', 'password'): password}
        if auth.get('body_format', 'json') == 'form':
            body = urllib.parse.urlencode(fields).encode('utf-8')
            content_type = 'application/x-www-form-urlencoded'
        else:
            body = json.dumps(fields, ensure_ascii=False).encode('utf-8')
            content_type = 'application/json'

        url = url_for(self.cfg, auth.get('suffix', ''), auth['path'],
                      auth.get('base_url'))
        resp = self.request(auth.get('method', 'POST'), url, body,
                            {'Content-Type': content_type}, with_auth=False)

        if resp.status in (401, 403):
            raise LoginFailed('ответ %d: неверный логин или пароль' % resp.status)
        if not 200 <= resp.status < 400:
            raise LoginFailed('ответ %d' % resp.status)
        received = parse_set_cookie(resp.headers.get_all('Set-Cookie') or [])
        if self.token_cookie not in received:
            names = ', '.join(received) or 'ни одной'
            raise LoginFailed('в ответе нет куки %s, пришли: %s'
                              % (self.token_cookie, names))

        self.cookies.update(received)
        self.token = received[self.token_cookie]
        return resp

    def request(self, method, url, body=None, headers=None, with_auth=True):
        """Один запрос без перенаправлений. Код 4xx или 5xx — тоже ответ."""
        req = urllib.request.Request(url, data=body, method=method)
        for name, value in (headers or {}).items():
            req.add_header(name, value)
        if with_auth:
            self._authorize(req)

        started = time.monotonic()
        try:
            with self._opener.open(req, timeout=self.timeout) as r:
                return Response(r.status, r.headers, r.read(), _ms(started))
        except urllib.error.HTTPError as e:
            return Response(e.code, e.headers, e.read(), _ms(started))
        except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
            raise Unreachable(describe_network_error(e, self.timeout))

    def get(self, url, **kwargs):
        return self.request('GET', url, **kwargs)

    def _authorize(self, req):
        if self.cookies:
            req.add_header('Cookie', '; '.join(
                '%s=%s' % pair for pair in self.cookies.items()))
        if self.token and self.auth.get('send_token_as') == 'bearer':
            req.add_header('Authorization', 'Bearer %s' % self.token)


def _ms(started):
    return int((time.monotonic() - started) * 1000)
