# Образ сандбокса

Dockerfile собирается Compose через sandbox-image. Пользовательские команды: UID/GID 10001, rootfs read-only, network none. Manager создаёт ограниченные tmpfs для /work и /tmp.

- tools.py: RunCommand, ReadFile, WriteFile.
- seed.py: установка доверенной fixture до пользовательских операций.
- collect.py: bounded tar с проверкой обычных файлов и отказом от ссылок.
- idle.py: PID 1 и сбор завершившихся дочерних процессов.

[Описание и ограничения](../docs/sandbox.md), [ADR-0002](../docs/adr/0002-sandbox.md). Cancel/timeout завершает контейнер с потерей временной /work.
