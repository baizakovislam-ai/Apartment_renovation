# Часть Ислама: функции → код → где залито

Части 20–80% были залиты до введения правила «одна функция — один PR» ([CONTRIBUTING.md](CONTRIBUTING.md)), поэтому несколько функций лежат в одном PR или коммите. Историю `main` не переписываем, чтобы не сломать ветки других участников. Эта таблица показывает, где искать каждую функцию. Всё новое (100% и исправления) идёт отдельными PR.

Как проверить каждую функцию по шагам — [ISLAM_PART.md](ISLAM_PART.md).

| Этап | ТЗ | Функция | Где залито | Основной код | Тесты |
|---|---|---|---|---|---|
| 20% | FR03 | Участники проекта: приглашения, роли, удаление с передачей работы | [PR #1](https://github.com/baizakovislam-ai/Apartment_renovation/pull/1) (`df87da7`), доработка в [PR #6](https://github.com/baizakovislam-ai/Apartment_renovation/pull/6) | `apps/projects/services.py`, `apps/workspace/views.py` (`members`, `accept`) | `apps/projects/tests.py`, `apps/projects/test_team_and_archive.py` |
| 20% | FR04 | Этапы ремонта: создание, редактирование, удаление, история | [PR #1](https://github.com/baizakovislam-ai/Apartment_renovation/pull/1) (`df87da7`) | `apps/repairs/services.py`, `apps/workspace/views.py` (`stage_list`, `stage_form`) | `apps/repairs/tests.py` |
| 40% | FR05 | Задачи, сроки и контроль просрочек | коммит `65c649b` прямо в `main` | `apps/repairs/task_services.py`, `apps/workspace/work_views.py` | `apps/repairs/test_tasks.py` |
| 60% | FR09 | Создание и редактирование сметы, версии | [PR #6](https://github.com/baizakovislam-ai/Apartment_renovation/pull/6) (`d11e77e`) | `apps/estimates/services.py` (`create_draft`, `add_line`, `update_line`, `delete_line`) | `apps/estimates/tests.py` |
| 60% | FR10 | Согласование сметы: отправить, отозвать, утвердить, отклонить; запуск работ | [PR #6](https://github.com/baizakovislam-ai/Apartment_renovation/pull/6) | `apps/estimates/services.py` (`submit_estimate` … `start_works`) | `apps/estimates/tests.py` |
| 80% | FR16 | Предварительный расчёт и перенос в смету | [PR #6](https://github.com/baizakovislam-ai/Apartment_renovation/pull/6) | `apps/estimates/services.py` (`create_calculation` … `calculation_to_estimate`) | `apps/estimates/tests.py` |
| 80% | FR17 | Поиск по проекту, журнал согласований, фильтры задач | [PR #6](https://github.com/baizakovislam-ai/Apartment_renovation/pull/6) | `apps/estimates/search.py`, `apps/repairs/views.py` (`filter_tasks`) | `apps/estimates/tests.py` |
| — | — | Новый дизайн всего сайта | [PR #6](https://github.com/baizakovislam-ai/Apartment_renovation/pull/6) | `apps/workspace/static/workspace/ui.css` | — |
| 100% | FR19 | Уведомления по срокам, просрочкам, задачам, замечаниям и согласованиям (сайт и Email) | будет отдельной веткой `islam/notifications` | — | — |

## После введения правила

| Ветка | Что | PR |
|---|---|---|
| `islam/github-workflow` | Шаблон PR, правила веток, эта таблица | — |
| `islam/fix-estimate-revision` | Ошибка 500 при неверной версии сметы в API | — |
| `islam/docs-demo-estimate` | Исправлено описание демо-сметы в `ISLAM_PART.md` | — |
