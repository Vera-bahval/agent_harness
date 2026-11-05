# gateway

CP1-06: авторизованный API v0, владение сессиями, дедупликация, SSE/reconnect, cancel и collect. [Архитектура и ограничения](../../docs/client-gateway.md), [CLI](../../client/README.md).

CLIENT_TOKENS — JSON principal → token; пустая конфигурация отклоняет всех клиентов. Наружу публикуется только loopback-порт gateway.
