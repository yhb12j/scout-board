# Scout Board — ИИ-разведчик данных

Веб-панель собирает публичные новости из GNews, показывает их таблицей и останавливается на ручную проверку, если запрос неясный или невыполнимый.

Запуск с нуля занимает меньше 10 минут.

## Переменные окружения

Скопируйте `.env.example` в `.env` и вставьте ключи сразу после знака `=`, без кавычек. Файл `.env` в репозиторий не входит.

| Переменная | Назначение |
| --- | --- |
| `GNEWS_API_KEY` | Ключ GNews |
| `OPENAI_API_KEY` | Ключ языковой модели |
| `OPENAI_BASE_URL` | `https://api.proxyapi.ru/openai/v1` |
| `OPENAI_MODEL` | `gpt-4o-mini` |
| `LLM_TEMPERATURE` | `0.2` |
| `DATABASE_PATH` | Файл SQLite, по умолчанию `data/scout.db` |

После изменения `.env` сервер нужно перезапустить.

## Запуск

```powershell
git clone https://github.com/yhb12j/scout-board.git
cd scout-board
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Панель: http://127.0.0.1:8000

Остановить сервер: `Ctrl+C` в том же окне.

## База и аудит

База — файл `data/scout.db`. Отдельная установка не нужна.

Таблицы: `datasets`, `records`, `agent_runs`, `audit_runs`.

Журнал в панели: вкладка **Аудит**. В `audit_runs` пишутся `action`, `input`, `output`, `status`, `error`, `duration_ms`, `created_at`.

Число записей:

```powershell
.\.venv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect(r'data/scout.db'); print('audit', c.execute('select count(*) from audit_runs').fetchone()[0]); print('review', c.execute('select count(*) from agent_runs where needs_review=1').fetchone()[0])"
```

## Примеры запросов

В PowerShell используйте `curl.exe`.

Создать набор:

```powershell
curl.exe -X POST http://127.0.0.1:8000/datasets -H "Content-Type: application/json" -d '{"name":"Новости недели","source":"GNews"}'
```

Спланировать корректный запрос:

```powershell
curl.exe -X POST http://127.0.0.1:8000/ai/plan_and_collect -H "Content-Type: application/json" -d '{"query":"Собери новости за последние 24 часа по запросу «ноутбук» и сохрани поля title, date, source, url"}'
```

Ручная проверка. Сбор не стартует, в ответе `"needs_review": true` и `"confidence": "low"`:

```powershell
curl.exe -X POST http://127.0.0.1:8000/ai/plan_and_collect -H "Content-Type: application/json" -d '{"query":"Собери самое важное"}'
```

Собрать новости. Подставьте `dataset_id` из первого ответа:

```powershell
curl.exe -X POST http://127.0.0.1:8000/datasets/ВСТАВЬТЕ_ID/collect -H "Content-Type: application/json" -d '{"query":"Собери новости за последние 24 часа по запросу «ноутбук» и сохрани поля title, date, source, url"}'
```

Витрина:

```powershell
curl.exe "http://127.0.0.1:8000/datasets/ВСТАВЬТЕ_ID/records?limit=50"
```

## Как воспроизвести ручную проверку

Файл `tests_data/queries.jsonl`: 7 корректных запросов, 2 двусмысленных, 1 невыполнимый.

| № | Запрос | expected_needs_review | Почему |
| --- | --- | --- | --- |
| 1–7 | Тема в кавычках и ограничение: период, количество или поля | false | Запрос выполним |
| 8 | Собери самое важное | true | Нет темы и ограничения |
| 9 | Собери данные по рынку и сделай вывод | true | Неясно, что собирать |
| 10 | Собери закрытую статистику по всем конкурентам за год | true | Таких данных в GNews нет |

```powershell
.\.venv\Scripts\python.exe -m pytest
```

В панели: двусмысленный запрос → **Спланировать**. Появляется блок «Нужна ручная проверка запроса», причина и 2–3 подсказки. Кнопка **Собрать** остаётся заблокированной.

## Контроль качества

`POST /ai/plan_and_collect` запрашивает у модели строгий JSON. Решение о сборе принимает проверка запроса:

- нет темы в кавычках или нет ограничения — ручная проверка;
- «самое важное», «по рынку», «сделай вывод» — ручная проверка;
- закрытые данные, конкуренты или период длиннее 30 дней — ручная проверка, GNews не вызывается;
- тема и ограничение есть — сбор разрешён, в витрину попадают только `title`, `date`, `source`, `url`.

GNews в этой сборке: не больше 10 статей, задержка около 12 часов, история 30 дней.

## Docker

```powershell
docker compose up --build
```

Панель: http://127.0.0.1:8000

Остановить: `Ctrl+C`, затем `docker compose down`.

## Демонстрация

Видео 2–4 минуты, по порядку:

1. Создать набор: имя и источник GNews.
2. Корректный запрос: Спланировать → Собрать → витрина → Скачать JSON.
3. Запрос «Собери самое важное»: блок ручной проверки, кнопка «Собрать» заблокирована.
4. Вкладка Аудит: запуски и причина ручной проверки.

Защита 5–7 минут — по разделам файла `ОТЧЁТ.md`.
