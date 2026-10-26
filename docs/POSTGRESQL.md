# PostgreSQL

Проект поддерживает PostgreSQL. Подключение задаётся в `.env`; основное значение `DB_ENGINE=postgresql`. SQLite используется только при явном `DB_ENGINE=sqlite`, например для перехода со старой базы. Драйвер `psycopg[binary]` входит в `requirements.txt`.

## Подготовка установленного PostgreSQL

На этом компьютере установлен PostgreSQL 17, сервер работает на `127.0.0.1:5432`. Для нового компьютера сначала установите и запустите PostgreSQL.

Заполните `.env`:

```dotenv
DB_ENGINE=postgresql
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_DB=repair_control
POSTGRES_USER=repair_control_app
POSTGRES_PASSWORD=your-project-database-password
POSTGRES_ADMIN_USER=postgres
POSTGRES_ADMIN_PASSWORD=your-postgresql-administrator-password
```

Почтовые настройки в том же файле сохраняются. Пароль `POSTGRES_ADMIN_PASSWORD` относится к PostgreSQL и нужен только при первоначальном создании базы и роли. После подготовки можно удалить его из `.env`. Программа работает под отдельной ролью `repair_control_app`, а не администратором. Эта локальная роль имеет `CREATEDB`, чтобы Django мог создавать отдельную базу для тестов.

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe scripts/setup_postgres.py
```

Скрипт создаёт отдельную роль и базу. Он не сбрасывает пароли существующих ролей и не меняет базу, принадлежащую другому пользователю.

## Перенос существующей SQLite базы

Остановите сервер приложения перед переносом. Если он ещё работает с SQLite, оставьте `DB_ENGINE=sqlite` до завершения импорта. Команда переноса использует отдельное подключение к PostgreSQL из переменных `POSTGRES_*`:

```powershell
.\venv\Scripts\python.exe manage.py migrate_sqlite_to_postgres
```

Команда:

1. Отказывается перезаписывать PostgreSQL-базу с записями приложения.
2. Сохраняет согласованный снимок SQLite в `backups/`.
3. Создаёт таблицы PostgreSQL из актуальных миграций.
4. Переносит пользователей, пароли в виде хешей, роли, проекты, этапы, историю, сессии и остальные сущности текущих моделей с сохранением ID.
5. Сравнивает все перенесённые записи с исходными. При расхождении импорт откатывается.
6. Настраивает последовательности ID, чтобы после переноса можно было создавать новые записи.
7. Сохраняет исходные таблицы SQLite, включая старые поля и таблицы из прежних веток, в отдельной схеме PostgreSQL `sqlite_archive` в виде JSONB. Приложение использует актуальные таблицы схемы `public`.

Исходный `db.sqlite3` сохраняется. Пароли аккаунтов и почтовые настройки не меняются. Файлы из `media/` остаются на прежнем месте; при переносе всего проекта на другой компьютер их нужно передать вместе с ним.

После успешного импорта установите в `.env` `DB_ENGINE=postgresql` и запустите:

```powershell
.\venv\Scripts\python.exe manage.py check
.\venv\Scripts\python.exe manage.py runserver 127.0.0.1:8001
```

Для новой пустой базы без переноса достаточно `manage.py migrate`. Демонстрационные аккаунты автоматически не создаются.

## Открытие базы

В pgAdmin добавьте подключение к серверу: Host `127.0.0.1`, Port `5432`, Maintenance database `repair_control`, Username `repair_control_app`, Password — значение `POSTGRES_PASSWORD` из локального `.env`.

Таблицы приложения: `Databases → repair_control → Schemas → public → Tables`. Для просмотра записей откройте контекстное меню таблицы и выберите `View/Edit Data → All Rows`.

## Передача и резервная копия

PostgreSQL-база хранится на сервере. Для передачи создайте переносимый архив:

```powershell
.\venv\Scripts\python.exe scripts/backup_postgres.py
```

Скрипт напечатает путь к файлу `backups/repair_control_*.dump`. Архив содержит структуру, данные и архив старых таблиц; пароли подключения к PostgreSQL и Gmail в него не включаются. Для работы скрипта `pg_dump` должен быть в PATH либо в каталоге `POSTGRES_BIN`; на этом компьютере используется `C:\Program Files\PostgreSQL\17\bin`.

Получатель создаёт свою базу и восстанавливает `.dump` через pgAdmin **Restore** либо `pg_restore --no-owner --no-acl --dbname=repair_control path-to-backup.dump`. Параметры своего сервера получатель задаёт в собственном `.env`. В репозиторий входят `.env.example` и миграции; `.env` и `backups/` исключены из Git.

## Тесты

```powershell
.\venv\Scripts\python.exe manage.py test --noinput
```

При `DB_ENGINE=postgresql` Django создаёт отдельную PostgreSQL-базу `test_repair_control` и удаляет её после тестов. Рабочие данные не очищаются. SQLite-специфичная проверка восстановления старой SQLite-схемы на PostgreSQL пропускается.
