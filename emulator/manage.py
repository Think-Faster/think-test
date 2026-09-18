#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""Запуск стенда из этой папки, первый раз:

    python -m venv venv
    venv\Scripts\activate              (Linux и macOS: source venv/bin/activate)
    pip install -r requirements.txt
    python manage.py runserver

В следующие разы — только activate и runserver. Панель открывается на
http://127.0.0.1:8000/.

Датасет лежит в data/, другую папку можно задать через TF_DATA. Остальные
настройки движка тоже читаются из переменных окружения: TF_SPEED, TF_JITTER,
TF_FAULT_INTERVAL, TF_SEED. Ускорение меняется и на ходу, из панели.
"""
import os
import sys


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
