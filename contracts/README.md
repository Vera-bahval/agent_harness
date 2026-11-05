# Контракты v0

Статус: первая спецификация для реализации CP1-03…CP1-06. Сервер пока не реализован. Версия каждого JSON-сообщения — строка `v0`. Неизвестные поля и версии отклоняются; несовместимое изменение требует новой версии.

| Документ | Назначение |
|---|---|
| [client-server.v0.schema.json](client-server.v0.schema.json) | сообщения клиента, ответы gateway, события запуска |
| [harness-sandbox.v0.schema.json](harness-sandbox.v0.schema.json) | операции manager, ответы, события исполнения |
| [semantics.md](semantics.md) | HTTP-маршруты, таймауты, состояния, ошибки и повторы |
| [Пример клиента](examples/client-server.json) | сообщения одного сеанса |
| [Пример sandbox](examples/harness-sandbox.json) | создание среды, команда, сбор результата, очистка |

Для первого чтения: открыть пример клиента и проследить `session.create → run.start → run.accepted → run.result`. Затем сравнить с `provision → exec → exec.accepted → exec.result` у manager.

В JSON Schema корневой `oneOf` перечисляет допустимые типы сообщений, а `$defs` содержит их определения. В каждой ветке `type` фиксирован, обязательные поля перечислены в `required`. Схемы самостоятельные, без сетевых `$ref`. Выбран [JSON Schema 2020-12](https://json-schema.org/draft/2020-12/json-schema-core); валидатор — [python-jsonschema](https://python-jsonschema.readthedocs.io/en/stable/validate/).

## Проверка

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
make test-contracts
```

Установка зависимостей требует сети; сами проверки выполняются без неё. Если окружение расположено иначе: `make test-contracts PYTHON=/path/to/python`.

Примеры — иллюстрации формата, не запись выполненного запуска: идентификаторы, время, токены и хеш артефакта условные. Перед отправкой настоящего запроса нужны свежие request_id/expires_at и server_epoch из connect. Числа в примерах не являются результатами БАФ.

Проверки схем не доказывают исполнение, изоляцию, корректность auth или семантическую идемпотентность. Соответствующие тесты появятся вместе с сервисами.
