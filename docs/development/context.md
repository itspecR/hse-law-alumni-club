# Контекст Клуба выпускников факультета права НИУ ВШЭ

Это указатель для работы с проектом. Текущее состояние проверок ведётся только в
[docs/operations/project-state.md](../operations/project-state.md). Полный список справочников – в
[docs/README.md](../README.md).

## С чего начать

1. [AGENTS.md](../../AGENTS.md) – правила изменений и инварианты выпуска.
2. [README.md](../../README.md) – назначение, роли, маршруты, стек и запуск.
3. [docs/development/architecture.md](architecture.md) – данные, сервисы, границы доверия и фоновые задачи.
4. [docs/development/configuration.md](configuration.md) – переменные, секреты и этапы их применения.
5. [docs/operations/deploy-runbook.md](../operations/deploy-runbook.md) – Ubuntu, обновление, резервирование и восстановление.
6. [docs/operations/capacity.md](../operations/capacity.md) – измерения и допущения по ресурсам.
7. [docs/development/security.md](security.md) и [docs/development/testing.md](testing.md) – границы доступа и сценарии проверки.
8. [DESIGN.md](../../DESIGN.md) – действующие решения интерфейса.

## Что сохраняем при изменениях

- Django/Jinja2 web в `frontend`, Django API в `backend`, общие справочники
  в `data`, серверные расчёты в API, хеши в `backend/club_api/modules/auth`, MariaDB
  как служба ОС и nginx перед сервисами.
- Серверную проверку прав, цены, скидки, суммы заявки и остатков.
- Роли выпускника, редактора, администратора и отдельный аккаунт API в MariaDB.
- Границы локального стенда и публичного сервиса. Собственная панель заменяет
  Studio; [ADR](../decisions/cms-options.md) фиксирует согласованный отказ от Directus.
- UUID, хеши и данные в совместимых таблицах `directus_users`, `directus_roles`,
  `directus_files`, каталог загрузок, пользовательские изменения и существующий дизайн.
- Миграции Django и повторяемый bootstrap; закрытые настройки прежней установки не
  становятся публичным API. Сотрудниками управляет оператор от аккаунта оператора MariaDB.

Маршруты определяет [pages.py](../../frontend/club_web/pages.py), страницы находятся в
`frontend/club_web/templates`, общая оболочка – в `templates/base.html`. Мобильная версия
использует те же страницы и серверные правила.

## История и примеры

История решений и удалённые рабочие отчёты доступны через Git. Для новой операции
используйте актуальный runbook, внешний env и конкретный проверенный SHA.

Сравнение с `itspecR/journal` используется для организации кода, документации и
эксплуатации. Сервер Клуба перенесён на Python/Django по отдельному поручению владельца.
MariaDB, nginx и дизайн сохраняются; основание – [ADR Django](../decisions/django-migration.md).
