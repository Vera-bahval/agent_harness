# Пары харнес + LLM

Каждый JSON задаёт id, harness (`simple-v0`), mode (`replay`/`real`), model, max_steps, timeout_s, tool_timeout_s, parameters и replay (имя сценария или null). Конфигурации валидирует services/common/pairs.py. Ключей здесь нет.

- replay-b1: Б1.1, последовательность чтение → запись → команда → финал.
- replay-recovery / replay-invalid: восстановление после ошибки команды / аргументов.
- replay-loop / replay-slow: остановка по бюджету / отмене и времени.
- first-real-pair: модель из LLM_MODEL, 20 шагов, общий бюджет 180 секунд, инструмент 60 секунд. Параметры OpenAI: max_completion_tokens=2048, reasoning_effort=none, parallel_tool_calls=false.

Выбор: `make demo-agent PAIR=first-real-pair`. Прокси также требует LLM_MODE=real, LLM_PROVIDER_URL и LLM_API_KEY в локальном .env. По умолчанию демонстрация использует replay-b1. Изменения JSON применяются после `make up` (пересборки).

Трейс сохраняет конфигурацию пары и фактически возвращённый model. Это техническая первая пара, не выбранный победитель сравнения; допуск по индексу курса ещё нужно проверить.

replay-session: два сообщения для проверки сохранения /work и контекста; второе сообщение читает созданный первым файл и проверяет наличие истории.

replay-simple: единая replay-пара для Б1.1…Б5.1; выбирает один из пяти сценариев по тексту задачи и проверяет наблюдения. Команда `make baf-simple-smoke`.

cp1-sol-low: выбранная для ЧП1 реальная пара, gpt-6.1-sol low через Responses API. [Допуск по AA](../../docs/model-selection.md).
replay-cp1: все 15 задач Б1–Б5, включая ошибку команды как наблюдение, YAML и бинарные правки; `make baf-cp1-replay`.
