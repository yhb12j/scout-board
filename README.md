# Платформа интеллектуального мониторинга и Guardrails-фильтрации медиа-данных «Scout Board»

Scout Board собирает публичные новости из GNews, приводит их к единому JSON и останавливает сбор, если запрос размытый или невыполним. Флаг `needs_review` не даёт модели выдавать уверенный ответ там, где данных нет.

Запуск с нуля занимает не больше 10 минут.

## Архитектура

```text
Веб-панель → FastAPI → sqlite3 → gpt-4o-mini → Guardrails → GNews → audit_runs
```

- Веб-панель: наборы, сбор, витрина, аудит.
- API: FastAPI.
- База: один файл SQLite через модуль `sqlite3`. SQLAlchemy не используется.
- Модель: `gpt-4o-mini` по совместимому с OpenAI адресу. Температура `0.2`. Ответ — один JSON.
- Guardrails: решение о сборе принимается по правилам запроса. При `needs_review: true` GNews не вызывается.
- Источник: GNews. В витрину попадают только `title`, `date`, `source`, `url`.

Таблицы: `datasets`, `records`, `agent_runs`, `audit_runs`.

## Переменные окружения

Скопируйте `.env.example` в `.env`. Значения пишутся сразу после `=`, без кавычек. Файл `.env` в репозиторий не входит.

| Переменная | Назначение |
| --- | --- |
| `GNEWS_API_KEY` | Ключ GNews |
| `OPENAI_API_KEY` | Ключ модели |
| `OPENAI_BASE_URL` | `https://api.proxyapi.ru/openai/v1` |
| `OPENAI_MODEL` | `gpt-4o-mini` |
| `LLM_TEMPERATURE` | `0.2` |
| `DATABASE_PATH` | `data/scout.db` |

После правки `.env` процесс нужно перезапустить.

## Быстрый запуск

Локально:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Панель: http://127.0.0.1:8000

Остановка: `Ctrl+C`.

Инфраструктура через Docker:

```powershell
docker compose up --build
```

Тот же адрес: http://127.0.0.1:8000. Остановка: `Ctrl+C`, затем `docker compose down`.

База создаётся сама: `data/scout.db`. Журнал в панели — вкладка **Аудит**. Поля `audit_runs`: `action`, `input`, `output`, `status`, `error`, `duration_ms`, `created_at`.

## Примеры запросов

В PowerShell используйте `curl.exe`.

Создать набор:

```powershell
curl.exe -X POST http://127.0.0.1:8000/datasets -H "Content-Type: application/json" -d '{"name":"Новости недели","source":"GNews"}'
```

План. Корректный запрос возвращает `needs_review: false`:

```powershell
curl.exe -X POST http://127.0.0.1:8000/ai/plan_and_collect -H "Content-Type: application/json" -d '{"query":"Собери новости за последние 24 часа по запросу «ноутбук» и сохрани поля title, date, source, url"}'
```

Guardrails. Размытый запрос возвращает `needs_review: true` и `confidence: low`. Сбор не стартует:

```powershell
curl.exe -X POST http://127.0.0.1:8000/ai/plan_and_collect -H "Content-Type: application/json" -d '{"query":"Собери самое важное"}'
```

Сбор. Подставьте `dataset_id` из ответа создания набора:

```powershell
curl.exe -X POST http://127.0.0.1:8000/datasets/ВСТАВЬТЕ_ID/collect -H "Content-Type: application/json" -d '{"query":"Собери новости за последние 24 часа по запросу «ноутбук» и сохрани поля title, date, source, url"}'
```

Витрина:

```powershell
curl.exe "http://127.0.0.1:8000/datasets/ВСТАВЬТЕ_ID/records?limit=50"
```

## Регламент верификации платформы для системных администраторов

Файл `tests_data/queries.jsonl`: 7 корректных запросов, 2 размытых, 1 невыполнимый.

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Чек-лист на стенде:

1. Создать набор с именем и источником GNews. В таблице появляются имя, источник и дата создания. Идентификатор набора в таблице не показывается.
2. Корректный запрос: **Спланировать** → **Собрать** → **Открыть витрину** → **Скачать JSON**. В записях только `title`, `date`, `source`, `url`.
3. Запрос `Собери самое важное`: блок ручной остановки, причина и подсказки. Кнопка **Собрать** заблокирована. В ответе API `needs_review: true`.
4. Вкладка **Аудит**: виден запуск и текст причины в `error`.
5. Повторить запрос `Собери данные по рынку и сделай вывод` и `Собери закрытую статистику по всем конкурентам за год`. Оба остаются на Guardrails, GNews не вызывается.

Успех: набор создаётся, корректный сбор пишет строки в `records`, размытый и невыполнимый запросы не запускают сбор, выгрузка JSON открывается, в `audit_runs` есть причина остановки.
