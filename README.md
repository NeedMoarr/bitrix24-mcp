# bitrix24-mcp

MCP-сервер для Bitrix24 (bitrix.boostra.ru) через входящий вебхук. Один файл — [server.py](server.py), запускается через `uv run` (зависимости ставятся автоматически из PEP 723-заголовка).

## Инструменты

**Задачи**

| Инструмент | Что делает |
|---|---|
| `task_create` | Создать задачу (BB-код, дедлайн, приоритет, исполнитель, соисполнители, наблюдатели, группа, чек-лист) |
| `task_get` | Задача по ID + чек-лист |
| `task_list` | Список задач с фильтрами |
| `task_update` / `task_complete` | Обновить / завершить |
| `task_comments` | Комментарии из чата задачи (im.chat.get `ENTITY_TYPE=TASKS_TASK` → im.dialog.messages.get) |
| `task_comment_add` | Добавить комментарий (легаси task.commentitem.add — пишет в чат корректно) |

**Чаты и сообщения**

| Инструмент | Что делает |
|---|---|
| `chats_recent` | Последние диалоги: кто писал, непрочитанное; `only_today=True` — за сегодня |
| `chat_messages` | Переписка диалога (dialog_id: "2265" для юзера, "chat86230" для чата) |
| `chat_send` | Отправить сообщение — **только по явной просьбе пользователя** |

**Остальное**

| Инструмент | Что делает |
|---|---|
| `user_search` / `departments` | Сотрудники и структура компании |
| `group_search` | Поиск групп/проектов (для group_id задач) |
| `calendar_events` / `calendar_event_add` | Календарь |
| `crm_deals` / `crm_deal_get` | Сделки CRM |
| `call_stats` | Статистика звонков (voximplant; на портале пока пусто — возможно, внешняя телефония) |
| `my_digest` | Просрочка, дедлайны недели, открытые задачи, события сегодня, непрочитанные чаты |
| `whoami` | Профиль владельца вебхука |

## Настройка

Зарегистрирован в Claude Code (user scope):

```
claude mcp add --scope user bitrix24 \
  -e BITRIX_WEBHOOK_URL='https://bitrix.boostra.ru/rest/<user_id>/<token>/' \
  -- /Users/anatoliimilovskii/.local/bin/uv run /Users/anatoliimilovskii/Claude/Projects/bitrix24-mcp/server.py
```

Токен только в env `BITRIX_WEBHOOK_URL` — в коде его нет. После изменения server.py новые инструменты подхватываются при новом подключении сервера (новая сессия).

## Права вебхука (расширены 14.08.2026)

`task`, `tasks`, `tasks_extended`, `calendar`, `user`, `user_basic`, `im`, `im.import`, `crm`, `disk`, `sonet_group`, `telephony`, `call`, `department`, `log`, `messageservice`, `pull_channel`, `ai_admin`.

Ключевой нюанс портала: комментарии задач живут в чатах. Легаси-чтение `task.commentitem.getlist` всегда пусто; читать через `im.chat.get {ENTITY_TYPE: "TASKS_TASK", ENTITY_ID: <taskId>}` → `im.dialog.messages.get {DIALOG_ID: "chat<ID>"}`. Запись легаси-методом работает.

## Проверено на живом портале (14.08.2026)

Полный цикл задач (тестовая 89597), чтение комментариев реальных задач, дайджест чатов за день, поиск групп («Мексика» = 370), сделки CRM (воронка жалоб), структура компании.
