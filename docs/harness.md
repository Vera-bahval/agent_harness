# Харнес и LLM-прокси: CP1-05

Реализован свой харнес `simple-v0`: явный цикл «модель → инструмент → наблюдение → модель». Решение — [ADR-0001](adr/0001-harness.md). Replay проходит Б1.1 через настоящий Docker-сандбокс; результат реального запуска отдельно фиксируется в [отчёте](report.md).

## Как идёт выполнение

1. `AgentService.start` проверяет пару, prompt, fixture, бюджет и run_id, создаёт поток запуска.
2. `AgentService.execute` создаёт отдельную сессию через sandbox-manager. Начальный контекст состоит из system-инструкции и задачи пользователя.
3. `Proxy.complete` получает историю и конфигурацию пары. В replay ответ берётся из сценария с проверкой предыдущего наблюдения; в real вызывается Chat Completions.
4. Ответ assistant добавляется в историю. Аргументы каждого tool call проверяются по JSON Schema; команды передаются только через `SandboxClient.execute`.
5. Результат инструмента возвращается модели сообщением role=tool с исходным tool_call_id. Ошибка команды или неверные аргументы тоже становятся наблюдением — модель может исправиться.
6. Финальный ответ без tool calls завершает цикл. Харнес собирает архив /work и в finally освобождает сандбокс. При провале cleanup запуск считается неуспешным.

| Механизм | Код |
|---|---|
| Запуск, история, лимиты, остановка | [AgentService.start / execute / check](../services/harness/runtime/agent.py) |
| JSONL и идентификаторы | [AgentService.trace](../services/harness/runtime/agent.py) |
| Инструменты, аргументы, наблюдения | [definitions / command / observation](../services/common/tools.py) |
| Протокол сандбокса, опрос, отмена, сбор | [SandboxClient](../services/harness/runtime/sandbox.py) |
| Пары и бюджеты | [load_pairs](../services/common/pairs.py), [конфигурации](../configs/pairs/README.md) |
| Replay / настоящий провайдер | [Proxy.complete / replay](../services/llm-proxy/runtime/proxy.py) |
| Ответ модели и связность истории | [normalize / validate_history](../services/llm-proxy/runtime/proxy.py) |
| HTTP и безопасные ошибки | [request_json](../services/common/http_client.py) |

RunCommand принимает argv и cwd; shell запускается только явно как argv, внутри сандбокса. ReadFile принимает относительный путь. WriteFile принимает UTF-8 текст, который адаптер кодирует в base64 для sandbox-протокола. Харнес не исполняет subprocess и не получает Docker socket или ключ модели.

## Бюджеты и ошибки

`max_steps` считает обращения к модели, включая финальное. Общий timeout ограничивает цикл, tool_timeout — исполнение одной команды. Один вызов провайдера ограничен 30 секундами. Cancel проверяется между шагами и при опросе инструмента; текущий HTTP-вызов модели не прерывается мгновенно. Освобождение ресурсов имеет отдельный бюджет до 20 секунд и может завершиться после deadline.

Контекст ограничен 262144 символами сериализованного JSON, вывод каждого поля инструмента — 8192 символами с признаком truncated. Это простой предел, без суммаризации и подсчёта токенов контекста. За запуск модель не должна менять возвращаемый идентификатор. Usage берётся из ответа провайдера; если недоступен, записывается null (также для replay).

Неисправимые ошибки сандбокса/транспорта завершают запуск. Автоматических повторов запросов модели и команд с неизвестным исходом нет. Ошибки провайдера нормализованы; HTTP 429 с кодом insufficient_quota отличается от provider_rate_limit. Тела ошибок и ключи не возвращаются вызывающему сервису.

## Внутренний API

Пока это API доверенной сети Compose, без публикации портов harness/proxy. Публичная авторизация, клиентские сессии и SSE gateway реализованы в CP1-06: [описание](client-gateway.md).

- POST `/internal/runs`: поля run_id (32 hex), pair_id, prompt, workdir (`kind=fixture`, source_id из серверного allowlist (`B1_1`…`B5_1` или `empty`), mode `ro`/`rw`), timeout_s. Возвращает 202 и состояние.
- GET `/internal/runs/<run_id>`: состояние и terminal result.
- POST `/internal/runs/<run_id>/cancel`: запрос отмены.
- GET `/internal/runs/<run_id>/trace`: JSON со списком событий.
- GET `/internal/runs/<run_id>/artifact`: tar итоговой /work успешного запуска.
- POST proxy `/internal/completions`: pair_id, messages, timeout_s; нормализованные message, model, mode, usage.

Одинаковый run_id и payload возвращают существующий запуск без нового исполнения; другой payload отклоняется HTTP 400. Лимиты: 8 активных и 1000 сохранённых в памяти запусков. Для старого вызова без session_id один запуск владеет временной сессией. CP1-06 добавляет session_id, сохранение контекста и /work между run; release выполняется при session.close. Restart теряет индекс запусков и историю контекста.

Трейсы `/app/results/traces/<run_id>.jsonl` и успешные архивы `/app/results/artifacts/<run_id>.tar` сохраняются в volume harness-results. Демо дополнительно пишет `/app/results/runs/<run_id>.json`. JSONL содержит идентификаторы, имена операций, хеш аргументов, длительности, исходы и usage; тексты prompt, ответов, файлов и reasoning в трейс не пишутся. Архив и итог демо содержат результат задачи. `make down` удаляет и этот volume; нужные результаты следует заранее скопировать через `docker compose cp`.

## Проверка

```bash
make up
make test-harness                 # 8 сценариев на replay, реальные сандбоксы
make demo-agent                   # replay Б1.1: answer.txt содержит 8137
make demo-agent PAIR=first-real-pair  # реальный провайдер, требует локальный .env
```

В `.env`: LLM_MODE=real, LLM_PROVIDER_URL=https://api.openai.com/v1, LLM_MODEL=gpt-5.4-mini, LLM_API_KEY — локальный ключ. После изменения выполнить `make up`. Replay-пары работают и при LLM_MODE=real.

Для первого подключения выбрана небольшая gpt-6-luna. По [официальной документации OpenAI](https://developers.openai.com/api/docs/models/gpt-6-luna), её function calling в Chat Completions требует reasoning_effort=none; это задано в паре вместе с max_completion_tokens=2048 и parallel_tool_calls=false. Проверка допуска модели по правилам курса ещё не выполнена. Алиас и фактически возвращённый model фиксируются отдельно; успешного ответа провайдера до прогона не предполагаем.

Демо проверяет answer.txt, но ещё не заменяет раннер БАФ CP1-07. CLI реализован в CP1-06; раннер и полная матрица Б1–Б5 остаются следующими этапами.

Проверено 2026-11-05: после пополнения баланса Б1.1 проходит на gpt-5.4-mini (ответ провайдера gpt-5.4-mini-2026-03-17). Текущий локальный .env использует эту модель; исходная gpt-6-luna была заменена при диагностике. См. [результаты](report.md).

## Responses API (подготовка ЧП1)

Для cp1-sol-low прокси вызывает /responses: преобразует tool definitions, историю и tool_call_id/function_call_output, сохраняет stateless output items для следующего шага. Адаптер — services/llm-proxy/runtime/responses.py. Reasoning находится только в памяти контекста; метаданные трейс/SSE не содержат response_items. store=false. Нормализованные message/usage/model остаются теми же для харнеса. Старые Chat Completions пары продолжают использовать свой API.
