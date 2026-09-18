# -*- coding: utf-8 -*-
"""Проверка доступности сервисов стенда: обновили стенд, запустили — видно,
какой сервис лёг.

    python health.py                    вход по MT_LOGIN и MT_PASSWORD из окружения или .env
    python health.py --login tester     пароль спросит сам, в историю оболочки он не попадёт
    python health.py --token <токен>    без входа, с готовым токеном
    python health.py --base-url https://stand.example.ru
    python health.py -v                 ещё и адреса ручек, и начало ответа при сбое

Готовый токен главнее логина и пароля. Если не задано ни того, ни другого,
сервисы проверяются без токена — об этом говорит первая строка вывода.

Код выхода: 0 — всё доступно, 1 — что-то недоступно или вход не прошёл,
2 — не прочитались параметры.
"""
import argparse
import getpass
import os
import sys

import client

ALL_OK, SOMETHING_DOWN, BROKEN_CONFIG = 0, 1, 2


def parse_args(argv):
    parser = argparse.ArgumentParser(description='Проверка доступности сервисов стенда.')
    parser.add_argument('--config', help='другой файл параметров вместо config.json')
    parser.add_argument('--base-url', help='адрес хоста вместо base_url из параметров')
    parser.add_argument('--login', help='логин учётки; пароль — из MT_PASSWORD, иначе спросит')
    parser.add_argument('--token', help='готовый токен доступа, вход пропускается')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='показать адреса ручек и ответы при сбое')
    return parser.parse_args(argv)


def _interactive():
    # на Windows пустой stdin (NUL) тоже отвечает isatty() == True, а getpass
    # читает прямо с консоли и в CI повис бы навсегда, поэтому терминалом
    # считаем только случай, когда и ввод, и вывод смотрят в консоль
    return sys.stdin.isatty() and sys.stdout.isatty()


def sign_in(api, args):
    """Готовит токен и печатает строку про вход. False — вход не прошёл."""
    token = args.token or os.environ.get(client.ENV_TOKEN)
    if token:
        api.use_token(token)
        print('аутентификация пропущена — токен из параметров')
        return True

    login = args.login or os.environ.get(client.ENV_LOGIN)
    password = os.environ.get(client.ENV_PASSWORD)
    if login and not password and _interactive():
        password = getpass.getpass('Пароль для %s: ' % login)
    if not login and not password:
        print('аутентификация пропущена — логин и пароль не заданы, проверка без токена')
        return True
    if not login or not password:
        missing = client.ENV_PASSWORD if login else client.ENV_LOGIN
        print('вход не выполнен — не задан %s' % missing)
        return False

    if args.verbose:
        auth = api.auth
        print('    %s %s' % (auth.get('method', 'POST'), client.url_for(
            api.cfg, auth.get('suffix', ''), auth.get('path', ''), auth.get('base_url'))))
    try:
        resp = api.login(login, password)
    except client.Unreachable as e:
        print('аутентификация недоступна — %s' % e)
        return False
    except client.LoginFailed as e:
        print('вход не выполнен — %s' % e)
        return False
    print('аутентификация доступна — токен получен, %d мс' % resp.elapsed_ms)
    return True


def explain(resp, has_token):
    """Почему ответ не считается живым сервисом."""
    code = resp.status
    if code in (401, 403):
        why = 'токен не принят или истёк' if has_token else 'нужен вход, а токена нет'
        return 'ответ %d: %s' % (code, why)
    if 300 <= code < 400:
        where = resp.headers.get('Location') or 'другой адрес'
        why = 'похоже, токен не принят' if has_token else 'похоже, нужен вход'
        return 'ответ %d: перенаправляет на %s, %s' % (code, where, why)
    if code == 404:
        return 'ответ 404: по этому адресу ручки нет, проверьте суффикс и путь'
    return 'ответ %d' % code


def check(api, service, verbose=False):
    """Проверяет один сервис, печатает строку про него. True — доступен."""
    name = service['name']
    url = api.url(service, service.get('health', '/health'))
    try:
        resp = api.get(url)
    except client.Unreachable as e:
        print('%s недоступен — %s' % (name, e))
        resp = None
    else:
        if resp.ok:
            print('%s доступен — %d, %d мс' % (name, resp.status, resp.elapsed_ms))
        else:
            print('%s недоступен — %s' % (name, explain(resp, bool(api.token))))

    if verbose:
        print('    GET %s' % url)
        if resp is not None and not resp.ok and resp.body:
            print('    %s' % ' '.join(resp.text().split())[:200])
    return resp is not None and resp.ok


def main(argv=None):
    args = parse_args(argv)
    client.load_dotenv()
    try:
        cfg = client.load_config(args.config)
        if args.base_url:
            cfg['base_url'] = args.base_url
        api = client.Client(cfg)
        print('Стенд: %s' % cfg['base_url'])
        signed_in = sign_in(api, args)
    except client.ConfigError as e:
        print('Параметры: %s' % e)
        return BROKEN_CONFIG

    services = cfg['services']
    alive = sum(check(api, service, args.verbose) for service in services)

    summary = 'Итого: доступно %d из %d' % (alive, len(services))
    print(summary if signed_in else summary + ', вход не выполнен')
    return ALL_OK if signed_in and alive == len(services) else SOMETHING_DOWN


if __name__ == '__main__':
    sys.exit(main())
