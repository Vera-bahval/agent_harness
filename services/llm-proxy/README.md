# llm-proxy

CP1-05: replay и адаптер Chat Completions; ключ провайдера доступен только этому сервису. [Proxy](runtime/proxy.py) проверяет историю, нормализует tool calls, usage и ошибки. Тела ошибок провайдера не логируются; автоматических ретраев нет.

Пары — в [configs/pairs](../../configs/pairs/README.md), сценарии — tests/fixtures/llm. [Запуск и ограничения](../../docs/harness.md). Healthcheck проверяет готовность процесса, а не баланс или доступность аккаунта провайдера.
