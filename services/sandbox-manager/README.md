# Sandbox-manager

Реализует HTTP-протокол v0: connect, provision, exec, operation.get, cancel, collect, release и SSE. Проверяет сервисный токен и JSON Schema. Только manager получает Docker socket; выполняемые команды находятся в отдельных контейнерах.

app.py — HTTP/auth; runtime/manager.py — состояние и lifecycle; runtime/docker_api.py — Docker Engine API. Runtime-зависимости закреплены в requirements.txt. Контейнер manager работает от root для доступа к socket, это доверенная граница системы.

[Подробности](../../docs/sandbox.md). Проверки: `make test-sandbox`, `make test-lifecycle`; пример вызова из харнеса: `make demo-sandbox`.
