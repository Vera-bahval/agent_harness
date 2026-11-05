# Agent Harness — среда исполнения агента

Учебный проект задания A курса ИТМО × DeepPavlov. Цель — построить сервер исполнения агента, подключить десктоп-клиент с удалённым монтированием рабочей папки и сравнить пары «харнес + LLM» на БАФ.

**Сейчас:** выполнены контракты v0, серверный каркас CP1-03 и sandbox API CP1-04. Manager создаёт Docker-сандбоксы, исполняет команды, читает/пишет файлы, отдаёт события и очищает ресурсы. Добавлен CP1-05: агентный цикл, replay и адаптер OpenAI. Replay и реальный Б1.1 на gpt-5.4-mini проходят (2026-11-05). CP1-06: CLI подключается через gateway с токеном и SSE; файлы и история сохраняются в сессии. [Клиент](client/README.md). [Харнес](docs/harness.md). [Сервер](docs/server.md), [сандбокс и ограничения](docs/sandbox.md).

CP1-08: [пять простых Б1–Б5](docs/baf-simple.md) проходят 5/5 на replay и реальной gpt-5.4-mini. [Раннер](baf/runner/README.md), [результаты](docs/report.md). Все 15 задач Б1–Б5 проходят на выбранной по AA паре **cp1-sol-low (gpt-6.1-sol low)**. [Готовность к ЧП1](docs/readiness.md).

Начать с [плана ЧП1](docs/checkpoint-1.md). Затем прочитать [архитектуру](docs/architecture.md), [ADR](docs/adr/README.md) и [разбор источников](docs/sources.md).

## Структура

```text
client/                    будущий десктоп-клиент и его CLI
services/
  common/                  общий HTTP-bootstrap и проверки зависимостей
  gateway/                 входной API: токен, сессии, сообщения, события
  harness/                 цикл LLM → инструмент → наблюдение
  sandbox-manager/         создание, исполнение, отмена, удаление сандбоксов
  llm-proxy/               доступ к провайдеру модели; ключ только здесь
sandbox/                   Dockerfile, файловые инструменты, seed/collect и PID 1
contracts/                 два версионированных протокола
configs/pairs/             конфигурации пар харнес + LLM
baf/
  catalog.yaml             курсовой каталог задач (описания, не готовый набор)
  B1-read/                 готовый курсовой пример Б1.1
  runner/                  раннер через публичный API и доверенный checker
tests/                    contracts, unit, ui, infra, fixtures/llm
docs/                     план, архитектура, ADR, харнес, БАФ, отчёт
results/                  сырые результаты БАФ, трейсы, замеры
scripts/                  будущие команды проверки и демонстрации
```

## Команды

```bash
make up                # собрать, запустить, дождаться healthy
make status
make setup-client      # создать локальный клиентский токен, затем make up
make demo-client       # публичный API и SSE на replay
make baf-smoke         # раннер Б1.1 на replay
make baf-b1            # раннер Б1.1 на реальной модели, расходует API-баланс
make baf-simple-smoke  # пять простых Б1–Б5 на replay
make baf-simple-real   # пять простых Б1–Б5 на контрольной mini
make baf-cp1-replay    # все 15 задач без внешней LLM
make baf-cp1           # все 15 на выбранной cp1-sol-low, платный API
make test-ci           # общие проверки и 15 replay-задач
make test-clean        # отдельная чистая копия, без ключа модели
curl --fail http://localhost:8080/health
make logs
make demo-sandbox      # пример из harness через sandbox API, без LLM
make test-sandbox      # 18 интеграционных проверок с временными контейнерами
make test-harness      # 8 сценариев агентного цикла на replay
make demo-agent        # Б1.1 через агентный цикл, без ключа
make demo-agent PAIR=first-real-pair  # реальная модель, нужен .env
make down              # удалить контейнеры, сети и тома проекта
```

`.env` пока необязателен; для настройки порта скопировать `.env.example`, если своего `.env` ещё нет. Replay не требует ключа. Настройка настоящего провайдера — в [docs/harness.md](docs/harness.md).

После установки `requirements-dev.txt` в `.venv` доступны `make test-unit`, `make test-contracts` и `make test-server` (последний — против запущенного сервера). Подробности: [сервер](docs/server.md). `make test-lifecycle` проверяет crash recovery и down -v, перезапуская dev-сервер без рабочих сессий.

`make test-ui` и `make test-client` проверяют клиент/gateway. `make test-ci`, `make test-infra`, `make baf-cp1` и `make baf` реализованы. `baf` прогоняет текущие оформленные задачи трижды; курсовые Б6–Б8 и составные пока впереди. Фактический статус подключения модели и прогонов записан в [отчёте](docs/report.md); Допуск cp1-sol-low зафиксирован в [выборе модели](docs/model-selection.md).

## Основание

[ТЗ задания A](https://github.com/deeppavlov/agentic-course-itmo/blob/main/projects/A-agent-runtime.md), [общие требования](https://github.com/deeppavlov/agentic-course-itmo/blob/main/projects/README.md), [шаблон](https://github.com/deeppavlov/agentic-course-itmo/tree/main/projects/templates/track-a-agent-runtime). Прочитанная ревизия: `b42540f5215b4ad30820bdd0330c466ba19e19e1`, дата: 2026-11-04.

По правилам курса проект должен быть public с первого дня. Рабочий Git-репозиторий инициализирован пользователем. Регистрация темы и проверка публичной доступности остаются организационными задачами CP1-01.

Подготовка к встрече: [сценарий показа](docs/cp1-demo.md), [выбор модели](docs/model-selection.md).
