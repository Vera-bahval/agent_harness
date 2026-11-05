# Служебные команды

- check_server.py (`make test-server`): read-only smoke запущенных сервисов, UID, опубликованный порт, разделение env и socket.
- demo_sandbox.py (`make demo-sandbox`): из контейнера harness через HTTP выполнить команду, прочитать файл, собрать артефакт, release. Без LLM.
- check_sandbox_lifecycle.py (`make test-lifecycle`): перезапуск manager через SIGKILL, очистка orphan, проверка stale epoch и down -v с активной командой. Отказывается начинать при существующих сандбоксах. После успеха снова поднимает сервер.

Docker-проверки выполняются на машине daemon, в рамках текущего Compose-проекта. Глобальный docker prune не используется. [Документация](../docs/sandbox.md).

- demo_agent.py (`make demo-agent`, `PAIR=first-real-pair` для real): полный агентный цикл Б1.1, проверка answer.txt из архива, запись результата в harness-results.

- setup_client.py (`make setup-client`): генерация локального клиентского токена без вывода секрета.
- check_gateway_lifecycle.py (`make test-gateway-lifecycle`): SIGKILL gateway во время replay-команды, новый epoch и отсутствие оставшихся сандбоксов.

- check_clean_start.py (`make test-clean`): изолированный snapshot без секретов/старых результатов, отдельный Compose-проект, CLI + 15 replay + очистка. Не заменяет публикацию и проверку настоящего удалённого клона.
