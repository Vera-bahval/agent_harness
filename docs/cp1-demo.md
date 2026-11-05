# Показ ЧП1

## Подготовка из репозитория

Нужны Docker Engine/Compose, Python 3.12 (локальные проверки также работали на Python 3.10), venv и доступ к Docker daemon.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
make setup-client
make up
make test-ci
```

`setup-client` не выводит токен и сохраняет существующий API-ключ. Без ключа replay-проверки работают; для real в .env нужны LLM_MODE=real, LLM_PROVIDER_URL=https://api.openai.com/v1, LLM_API_KEY и непустой LLM_MODEL. Пара cp1-sol-low задаёт свою модель явно и не зависит от выбранного алиаса LLM_MODEL.

## Сценарий на 5–7 минут

1. `make status`: показать healthy для gateway, harness, manager и proxy.
2. `make demo-client`: отдельный CLI подключается с токеном, видны tool.started/tool.finished/run.result, архив сохраняется в results/local.
3. Открыть contracts/semantics.md: объяснить два протокола, epoch, operation_id, cancel и повтор доставки.
4. `make baf-cp1-replay`: показать автоматический прогон 15 задач без внешней модели.
5. Показать реальный results.jsonl/summary из docs/report.md: точная модель, параметры, pass, токены, время, trace_id и cleanup.
6. Объяснить одно архитектурное решение: собственный цикл → sandbox API; ключ только proxy; отдельный сандбокс на сессию; файлы и история сохраняются между сообщениями.
7. `make down`: показать очистку. Это удаляет тома и runtime-трейсы; сохранённые файлы results/ на хосте остаются.

Реальный полный прогон: `make baf-cp1 PAIR=cp1-sol-low`. Он расходует API-баланс, поэтому для демонстрации можно открыть уже сохранённые результаты и воспроизвести их отдельно.

## Дополнительные проверки

- `make test-clean`: отдельный snapshot рабочей версии без .env/.venv/предыдущих результатов, собственный Compose-проект и порт; CLI + 15 replay-задач + down -v. Не изменяет основной dev-сервер. Используется уже установленный Python/venv хоста.
- `make test-lifecycle`: SIGKILL manager, orphan cleanup, down -v; перезапускает основной dev-сервер.
- `make test-gateway-lifecycle`: SIGKILL gateway при активной команде, новый epoch, cleanup сандбокса.

Чистый snapshot не является проверкой удалённого клона: новые изменения должны быть закоммичены и опубликованы, затем команды подготовки повторяются из `git clone`. Состояние публикации фиксируется в readiness.md; допуск модели — в model-selection.md.

## Что объяснить на встрече

- Replay подтверждает исполнение протокола; итоговый check.py определяет правильность файлов.
- Cancel останавливает контейнер целиком; после него временная /work теряется.
- История в памяти, restart меняет epoch; неизвестный исход не становится успешным ответом.
- Б4.2 checker сначала проверяет AST относительно допустимого переименования и лишь затем вызывает проверенную функцию на скрытых примерах. Произвольный изменённый код отвергается до исполнения.
- На ЧП1 используются серверные fixture. Настоящий удалённый mount и desktop-клиент — работа к ЧП2.
