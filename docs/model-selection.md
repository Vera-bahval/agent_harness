# Модель для ЧП1

Дата проверки: 2026-11-05. Правило 0-1 курса требует Intelligence Index не ниже DeepSeek Flash на момент выбора.

Для текущего ЧП1 выбрана пара **cp1-sol-low**: свой simple-v0 + **gpt-6.1-sol**, reasoning_effort=low, Responses API. На одной странице [Artificial Analysis](https://artificialanalysis.ai/leaderboards/models?deprecation=all), версия Intelligence Index v5.0.1:

| Модель и режим | Индекс |
|---|---|
| DeepSeek V4.1 Flash 1017 (max), ориентир | 33 |
| GPT-6.1 Sol (low), выбранная | 35 |

35 ≥ 33. Машиночитаемая фиксация — [model-eligibility.json](model-eligibility.json). Индексы разных версий методики нельзя смешивать. Результаты поиска/старые карточки могут содержать другие числа; сравнение сделано по одной текущей таблице.

[Официальная документация OpenAI](https://developers.openai.com/api/docs/models/gpt-6.1-sol) указывает поддержку low и необходимость Responses API для tool calling. [Stateless reasoning](https://developers.openai.com/api/docs/guides/reasoning) сохраняется в приватной истории как response output items с encrypted_content; store=false, в JSONL/SSE reasoning не попадает.

В конфигурации пары явно закреплены model=gpt-6.1-sol, reasoning low, max_completion_tokens=8192, parallel_tool_calls=false, API responses и бюджеты. Страница OpenAI перечисляет только алиас gpt-6.1-sol; фактический model из ответа провайдера сохраняется в каждом результате, а изменение model внутри run отклоняется.

Изначальная first-real-pair/gpt-5.4-mini с reasoning none сохранена как техническая контрольная пара: она прошла задачи, но её допуск по индексу для выбранного режима не подтверждён. Результаты mini не выдаются за подтверждение допуска модели. Переход на Sol создаёт новую кампанию и отдельный каталог результатов.

Это выбор доступной пары для ЧП1, а не итоговый победитель сравнения A-11. Несколько пар и три повтора для полноценного сравнения остаются дальнейшей работой.
