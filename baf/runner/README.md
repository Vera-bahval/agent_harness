# Раннер БАФ: CP1-07

Реализован прогон задач через публичный API gateway. Подготовлены все 15 задач Б1–Б5: простые, средние и с подвохом. Smoke на replay проверяет инфраструктуру, а не качество LLM.

## Запуск

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
make setup-client   # если токен ещё не создан
make up
make baf-smoke      # Б1.1 на replay, один повтор
make baf-b1         # Б1.1 на first-real-pair, один платный реальный прогон
make baf-simple-smoke # пять простых задач на replay-simple
make baf-simple-real  # пять простых задач на first-real-pair
```

Выбор задач, пары и повторов:

```bash
.venv/bin/python -m baf.runner --pair replay-b1 --tasks B1.1 --repeats 2 --mode smoke
.venv/bin/python -m baf.runner --pair first-real-pair --tasks B1.1 --repeats 1 --mode quality
```

`--tasks all` выбирает все оформленные задачи; smoke затем оставляет level=simple. Quality требует пару mode=real. Режим smoke-all включает также medium/tricky и используется в `make baf-cp1-replay`; smoke оставляет только simple. Код выхода: 0 — все проверки прошли; 1 — хотя бы один неуспешный прогон; 2 — неверная конфигурация. `make baf-cp1` запускает все 15 задач на cp1-sol-low с одним повтором; `make baf` — все оформленные задачи с тремя повторами. Б6–Б8 и составные ещё не оформлены.

Раннер поддерживает `--url`, `--token-file`, CLIENT_TOKEN и `--output`. Ключ OpenAI не нужен процессу раннера: он остаётся в серверном прокси.

## Один прогон

1. [discover](core.py) читает task.yaml через закреплённый PyYAML SafeLoader, проверяет обязательные поля, повторы id, сообщения, бюджет, наличие fixture/check.py и регистрацию source_id в [fixtures.json](../fixtures.json). Неизвестные поля и повторные YAML-ключи отклоняются до запуска модели.
2. На каждый повтор создаётся новая сессия. Сервер provision-ит чистый сандбокс из зарегистрированной fixture. Раннер получает исходный snapshot и сравнивает пути и содержимое с локальной fixture. Расхождение останавливает прогон до модели. Чекер не передаётся серверу.
3. Проверяется SHA-256 конфигурации пары, которую вернул gateway. Все messages выполняются последовательно в одной сессии, с сохранением файлов/контекста и общим task.timeout_s. Бюджет каждого run не превышает ни оставшееся время, ни лимит пары.
4. Финальный collect скачивается до close. Проверяются размер и SHA-256; архив распаковывается в новую временную папку без ссылок, special files, повторных имён, выхода за корень и более 64 MiB/10000 элементов. Архивные пути не исполняются.
5. Доверенный check.py запускается на хосте отдельным Python-процессом с аргументом итоговой папки. Лимит — до 10 секунд и оставшегося времени задачи; env не содержит клиентского токена/API-ключа. Проверяются код выхода и JSON `{pass: bool, reason: str}`. После проверки уничтожается process group; ограничены CPU, память и размер вывода. Требуется Linux/POSIX.
6. В finally вызывается session.close и удаляются временные папки. Ошибка cleanup запрещает pass=true. Очистка получает отдельные 45 секунд и может завершаться после task deadline; транспорт также имеет небольшой запас. Seconds включает подготовку, выполнение, чекер и очистку, поэтому не является чистой задержкой модели.

## Результаты

Каталог `results/baf/<pair>/<UTC timestamp>-<random suffix>/` содержит:

- `results.jsonl` — запись на каждую задачу/повтор;
- `summary.json` — число прогонов, успехов и распределение outcomes;
- `<task_id>-<repeat>/result.json`, `events.jsonl`, `checker.json`, `work.tar` — детали и артефакт. При ранней ошибке часть файлов отсутствует.

В записи: task_id, repeat, pass, reason, outcome, seconds, tokens_in/out, pair и её параметры, mode, фактически возвращённая model_version, session/run/trace IDs, git_revision/git_dirty, хеши задачи, fixture, чекера и итогового архива. При отсутствии usage — null. Неизвестная версия модели не подменяется настроенным алиасом. Git-ревизия относится к локальному checkout; git_dirty=true явно показывает незакоммиченные изменения и не доказывает идентичность всех образов этой ревизии.

Outcomes: passed, wrong_answer, checker_error, checker_timeout, timeout, cancelled, agent_error, transport_error, runner_error, cleanup_error. В случае cleanup_error исходный outcome сохраняется отдельно. Ошибка provider возвращается как agent_error с отдельным reason. Ошибки checker не считаются неправильным ответом модели.

Регистрация fixture пока серверная, без upload API: новый task.yaml и fixtures.json сами по себе не добавляют задачу в образ manager. Для следующих БАФ нужно добавить источник в общий серверный реестр services/common/fixtures.py и Dockerfile manager, затем пересобрать сервер. Сверка исходного snapshot не даст незаметно запустить устаревшую фикстуру.

## Проверки

`make test-unit` включает 9 тестов раннера: правильный/неправильный/отсутствующий ответ; невалидный JSON, ненулевой exit и timeout чекера; отсутствие секретов в его env; небезопасные архивы; валидация задач; cleanup при ошибках; несовпадение fixture до модели. Сквозные smoke и real результаты сохранены в [отчёте](../../docs/report.md).

Полный ЧП1: `make baf-cp1 PAIR=cp1-sol-low`. Offline: `make baf-cp1-replay`. Общие checker-функции вынесены в baf/checks.py; их SHA-256 также включён в результаты.
