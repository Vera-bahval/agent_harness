# Сервер: CP1-03…CP1-06

Собраны четыре отдельных сервиса на Python 3.12.12 (образ `python:3.12.12-slim-bookworm`). Bootstrap использует стандартную библиотеку. Manager дополнительно использует jsonschema с закреплёнными зависимостями в services/sandbox-manager/requirements.txt. Зависимости локальных тестов — requirements-dev.txt.

## Запуск

Нужны Linux, доступ к Docker daemon и Docker Compose с поддержкой `--wait`.

```bash
# .env необязателен для этого этапа. Если его ещё нет:
cp .env.example .env
make up
make status
curl --fail http://localhost:8080/health
make logs
```

Не перезаписывать существующий `.env`. Ключ модели можно оставить пустым для replay; real требует настройки .env по [harness.md](harness.md). Авторизация клиента включена в CP1-06; `make setup-client` генерирует токен, пустой CLIENT_TOKENS отклоняет все v0-запросы. Для внутреннего sandbox API случайный токен генерируется автоматически в runtime-auth volume. Без `.env` действуют значения по умолчанию, поэтому работает и `docker compose up --build` из чистого клона. Первая сборка скачивает базовый образ; replay после сборки не требует внешней сети.

Gateway слушает 8000 внутри контейнера; на хосте опубликован `${GATEWAY_PORT:-8080}`. У остальных сервисов нет опубликованных портов. Все четыре соединены сетью backend (`internal: true`), gateway также подключён к frontend, а llm-proxy — к llm-egress для доступа к провайдеру.

## Что уже работает

- `GET /live` возвращает 200, если HTTP-процесс отвечает.
- `GET /health` возвращает 200/ready или 503/not_ready с состоянием зависимостей. Gateway проверяет harness; harness параллельно проверяет llm-proxy и sandbox-manager. Manager проверяет доступность Docker daemon и завершающуюся до ready очистку старых контейнеров; прокси проверяет процесс, но не баланс аккаунта провайдера.
- Healthcheck Compose вызывает `/health`, а `depends_on: service_healthy` задаёт порядок старта. Во время работы readiness также реагирует на отказ зависимостей; сам Docker автоматически не перезапускает unhealthy-контейнер.
- Gateway, harness и прокси работают от UID/GID 10001. Manager работает от root и один получает Docker socket — см. ADR-0002. Rootfs read-only, capabilities сброшены, no-new-privileges включён. Динамические сандбоксы запускают команды от UID 10001, подробнее в [sandbox.md](sandbox.md).
- Каждый сервис получает только свои переменные. LLM_API_KEY передаётся исключительно прокси; `.env` и другие файлы пользователя исключены из контекста сборки allowlist-правилами `.dockerignore`.
- В stdout пишутся JSON-события listening/stopping/stopped. Содержимое запросов, заголовков и env не логируется. Это стартовые логи, не полноценные агентные трейсы A-7.

`stage: bootstrap` у gateway/harness/прокси означает готовность HTTP-каркаса. У harness/прокси `/v0/requests` и `/v0/events` остаются неиспользуемыми (501); gateway реализует клиентские v0-маршруты. Manager имеет `stage: sandbox` и реализует авторизованные операции v0/SSE/артефакты. Внутренний агентный API и прокси реализованы в CP1-05 (см. harness.md). Клиентские сессии, auth и SSE gateway реализованы в CP1-06: [client-gateway.md](client-gateway.md).

Общий код — [services/common/server.py](../services/common/server.py); настройки зависимостей и точки входа — `services/<service>/app.py`. Для этого узкого этапа использован `http.server`; [документация Python](https://docs.python.org/3.12/library/http.server.html) не рекомендует его для production. Для локального CP1-06 сохранён этот HTTP-стек; ограничения и причины — [client-gateway.md](client-gateway.md).

## Проверки

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
make test-unit       # 12 тестов каркаса, инструментов и адаптера
make test-contracts  # 12 тестов схем
make test-server     # запущенные контейнеры и опубликованный порт gateway
```

`test-server` запускается на машине Docker daemon: обращается к опубликованному localhost-порту. Проверяет все четыре healthcheck, ожидаемый UID (root только manager), socket mount только у manager, наличие LLM_API_KEY только у прокси и отсутствие публикации внутренних сервисов. Не печатает содержимое env. Проверки не вызывают внешнюю LLM.

Проверка отказа вручную:

```bash
docker compose stop llm-proxy
curl --include --max-time 5 http://localhost:8080/health  # ожидается 503
curl --fail http://localhost:8080/live                   # ожидается 200
docker compose up -d --wait --wait-timeout 90
make test-server
```

## Остановка и повторный запуск

```bash
make down
make up
make test-server
```

`down` удаляет контейнеры, сети и тома именно этого Compose-проекта; скачанные образы и build cache остаются. Динамические контейнеры CP1-04 удаляет manager при штатной остановке. После SIGKILL их собирает manager при следующем запуске. Auth-volume и harness-results (трейсы/архивы) удаляются через down -v. Поведение проверяется `make test-lifecycle`.
