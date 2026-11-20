# Запись на приём к специалисту

Курсовая работа по ОКСП. ДЗ 1, вариант 3, С. З. Тедеты. Стенд `stanislav_tedety`.

## Запуск

Требуются Python 3.14 и PostgreSQL 14. Создайте отдельную базу `stanislav_tedety` и укажите её адрес. Пользователю базы нужны права на создание схемы в этой базе. Скрипт наполнения пересоздаёт данные схемы `stanislav_tedety`, запускайте его только на отдельной учебной базе.

```powershell
createdb -h 127.0.0.1 -U postgres stanislav_tedety
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:DATABASE_URL = 'postgresql://postgres:<password>@127.0.0.1:5432/stanislav_tedety'
$env:APP_PORT = '8766'
.\.venv\Scripts\python.exe seed.py small
.\.venv\Scripts\python.exe app.py
```

Откройте http://127.0.0.1:8766 и войдите как `demo` / `demo`. Учётные данные можно задать через `APP_USER` и `APP_PASSWORD`. Сервер привязан к loopback. Порт по умолчанию — 8765.

## Тесты и измерения

В другом терминале повторите переменные окружения. Для тестов сервер должен работать на малом наполнении. Тесты создают и отменяют одну запись.

```powershell
.\.venv\Scripts\python.exe -m unittest -v
.\.venv\Scripts\python.exe seed.py small
.\.venv\Scripts\python.exe bench.py small
.\.venv\Scripts\python.exe seed.py work
.\.venv\Scripts\python.exe bench.py work
```

Каждая серия имеет 1 прогрев и 25 повторов. Скрипт сохраняет `bench-small.json` и `bench-work.json`. Каждая серия замеров добавляет 26 записей, затем отменяет их. Перед каждой повторной серией верните нужное исходное наполнение через `seed.py`. Время ответа отсчитывается на сервере до отправки заголовков, время БД суммируется вокруг выполнения SQL и получения строк. Остаток включает соединение, Python, JSON и границы транзакции.

## Программный интерфейс

Все операции требуют HTTP Basic. Формат тела и ответа API — JSON. Общие ошибки: 401 при отсутствии действительной авторизации, 503 при ошибке базы. Тело ошибки содержит строку `error`. Время и число запросов указаны в заголовках `X-Response-Time-Ms`, `X-DB-Time-Ms`, `X-DB-Queries`.

| Метод и путь | Параметры | Успешный ответ | Ошибки |
| --- | --- | --- | --- |
| GET /api/services | — | 200, массив объектов id, name, minutes | — |
| GET /api/appointments | page (1), size (20, до 100), status (booked или cancelled) | 200, items (карточки), total, page, size | 422 |
| GET /api/appointments/{id} | id записи | 200, карточка | 404, 422 |
| GET /api/slots | service_id | 200, до 50 свободных слотов подходящей длительности, поля id, specialist, starts_at, ends_at | 404, 422 |
| GET /api/summary | from (2026-01-01), to (2027-01-01), даты ISO, правая граница исключена | 200, from, to, booked, cancelled, cancelled_share, specialists (id, name, slots, booked, cancelled, utilization) | 422 |
| POST /api/appointments | slot_id, service_id в теле | 201, карточка | 404, 409, 422 |
| POST /api/appointments/{id}/cancel | id записи, тело {} | 200, карточка со статусом cancelled | 404, 422 |

Карточка содержит id, status, slot_id, starts_at, ends_at, specialist_id, specialist, service_id, service. Слот подходит, если его длительность не меньше длительности услуги. Бронируется весь слот. Для одного специалиста активные записи не могут пересекаться, смежные интервалы допустимы. Повторная отмена возвращает текущую отменённую запись. Доля отмен считается среди всех записей за период, загрузка специалиста — доля его слотов с действующей бронью.
