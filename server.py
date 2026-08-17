#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=1.2.0", "httpx>=0.27"]
# ///
"""MCP-сервер для Bitrix24 (bitrix.boostra.ru) через входящий вебхук.

Доступные scope вебхука: task, tasks_extended, calendar, user, messageservice.
Вебхук передаётся через переменную окружения BITRIX_WEBHOOK_URL.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from mcp.server import MCPServer

WEBHOOK = os.environ.get("BITRIX_WEBHOOK_URL", "").rstrip("/")
MSK = timezone(timedelta(hours=3))

mcp = MCPServer("bitrix24", instructions=(
    "Инструменты Bitrix24 (bitrix.boostra.ru): задачи и их комментарии, чаты/сообщения, "
    "календарь, сотрудники и отделы, рабочие группы, сделки CRM, статистика звонков. "
    "Описания задач и комментарии — в BB-коде. chat_send использовать только по явной просьбе пользователя."
))

STATUS_NAMES = {
    "1": "новая",
    "2": "ждёт выполнения",
    "3": "выполняется",
    "4": "ждёт контроля",
    "5": "завершена",
    "6": "отложена",
    "7": "отклонена",
}
PRIORITY_NAMES = {"0": "низкий", "1": "обычный", "2": "высокий"}

TASK_SELECT = [
    "ID", "TITLE", "DESCRIPTION", "STATUS", "PRIORITY", "DEADLINE",
    "CREATED_BY", "RESPONSIBLE_ID", "ACCOMPLICES", "AUDITORS",
    "GROUP_ID", "CREATED_DATE", "CLOSED_DATE", "COMMENTS_COUNT",
]


def b24(method: str, params: dict | None = None) -> Any:
    """Вызов метода REST API Bitrix24 через вебхук."""
    if not WEBHOOK:
        raise RuntimeError("Не задана переменная окружения BITRIX_WEBHOOK_URL")
    resp = httpx.post(f"{WEBHOOK}/{method}.json", json=params or {}, timeout=30)
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Bitrix24 {method}: {data['error']} — {data.get('error_description', '')}")
    return data.get("result")


def _me() -> dict:
    return b24("profile")


def _slim_task(t: dict, full: bool = False) -> dict:
    """Компактное представление задачи для вывода. full=True — полное описание."""
    out = {
        "id": t.get("id"),
        "title": t.get("title"),
        "status": STATUS_NAMES.get(str(t.get("status")), t.get("status")),
        "priority": PRIORITY_NAMES.get(str(t.get("priority")), t.get("priority")),
        "deadline": t.get("deadline"),
        "createdDate": t.get("createdDate"),
        "url": f"{WEBHOOK.split('/rest/')[0]}/company/personal/user/{t.get('responsibleId')}/tasks/task/view/{t.get('id')}/",
    }
    if t.get("creator"):
        out["creator"] = t["creator"].get("name")
    if t.get("responsible"):
        out["responsible"] = t["responsible"].get("name")
    if t.get("group") and isinstance(t.get("group"), dict):
        out["group"] = t["group"].get("name")
    desc = t.get("description") or ""
    if desc:
        out["description"] = desc if full or len(desc) <= 300 else desc[:300] + "…"
    return out


def _list_tasks(flt: dict, limit: int = 50, order: dict | None = None) -> list[dict]:
    tasks: list[dict] = []
    start = 0
    while len(tasks) < limit:
        res = b24("tasks.task.list", {
            "filter": flt,
            "select": TASK_SELECT,
            "order": order or {"DEADLINE": "asc"},
            "start": start,
        })
        batch = res.get("tasks", [])
        tasks.extend(batch)
        if len(batch) < 50:
            break
        start += 50
    return tasks[:limit]


@mcp.tool()
def whoami() -> dict:
    """Профиль пользователя, от имени которого работает вебхук (ID нужен для задач/календаря)."""
    p = _me()
    return {"id": p["ID"], "name": f"{p.get('NAME', '')} {p.get('LAST_NAME', '')}".strip(),
            "admin": p.get("ADMIN"), "timezone": p.get("TIME_ZONE")}


@mcp.tool()
def task_create(
    title: str,
    description: str = "",
    responsible_id: int | None = None,
    deadline: str = "",
    priority: int = 1,
    accomplices: list[int] | None = None,
    auditors: list[int] | None = None,
    group_id: int | None = None,
    checklist: list[str] | None = None,
) -> dict:
    """Создать задачу в Bitrix24.

    description — BB-код ([B]жирный[/B], [URL=...]ссылка[/URL], списки через "-").
    deadline — ISO 8601, например "2026-08-21T18:00:00+03:00".
    priority: 0 низкий, 1 обычный, 2 высокий.
    responsible_id — исполнитель (по умолчанию владелец вебхука); ID ищи через user_search.
    accomplices/auditors — соисполнители/наблюдатели. group_id — ID группы/проекта.
    checklist — пункты чек-листа (создаются после задачи).
    """
    fields: dict[str, Any] = {"TITLE": title, "PRIORITY": str(priority)}
    if description:
        fields["DESCRIPTION"] = description
    fields["RESPONSIBLE_ID"] = responsible_id or int(_me()["ID"])
    if deadline:
        fields["DEADLINE"] = deadline
    if accomplices:
        fields["ACCOMPLICES"] = accomplices
    if auditors:
        fields["AUDITORS"] = auditors
    if group_id:
        fields["GROUP_ID"] = group_id

    task = b24("tasks.task.add", {"fields": fields})["task"]
    task_id = int(task["id"])

    created_items = []
    for item in checklist or []:
        b24("task.checklistitem.add", {"TASKID": task_id, "FIELDS": {"TITLE": item}})
        created_items.append(item)

    result = _slim_task(task)
    if created_items:
        result["checklist"] = created_items
    return result


@mcp.tool()
def task_get(task_id: int) -> dict:
    """Получить задачу по ID: все основные поля + чек-лист."""
    task = b24("tasks.task.get", {"taskId": task_id, "select": TASK_SELECT})["task"]
    out = _slim_task(task, full=True)
    try:
        items = b24("task.checklistitem.getlist", {"TASKID": task_id}) or []
        out["checklist"] = [
            {"id": i["ID"], "title": i["TITLE"], "done": i.get("IS_COMPLETE") == "Y"}
            for i in items
            if not str(i.get("TITLE", "")).startswith("BX_CHECKLIST")
        ]
    except RuntimeError:
        pass
    return out


@mcp.tool()
def task_list(
    responsible_id: int | None = None,
    created_by: int | None = None,
    only_open: bool = True,
    search_title: str = "",
    deadline_before: str = "",
    deadline_after: str = "",
    group_id: int | None = None,
    limit: int = 25,
) -> list[dict]:
    """Список задач с фильтрами.

    only_open=True — только незавершённые. search_title — поиск по названию (подстрока).
    deadline_before/after — ISO-даты для фильтра по дедлайну.
    Если фильтры не заданы, вернёт открытые задачи владельца вебхука.
    """
    flt: dict[str, Any] = {}
    if responsible_id:
        flt["RESPONSIBLE_ID"] = responsible_id
    if created_by:
        flt["CREATED_BY"] = created_by
    if not responsible_id and not created_by:
        flt["RESPONSIBLE_ID"] = int(_me()["ID"])
    if only_open:
        flt["REAL_STATUS"] = ["1", "2", "3", "4", "6"]
    if search_title:
        flt["%TITLE"] = search_title
    if deadline_before:
        flt["<=DEADLINE"] = deadline_before
    if deadline_after:
        flt[">=DEADLINE"] = deadline_after
    if group_id:
        flt["GROUP_ID"] = group_id
    return [_slim_task(t) for t in _list_tasks(flt, limit)]


@mcp.tool()
def task_update(
    task_id: int,
    title: str = "",
    description: str = "",
    deadline: str = "",
    responsible_id: int | None = None,
    priority: int | None = None,
    status: int | None = None,
) -> dict:
    """Обновить поля задачи. Заполняй только то, что меняется.

    status: 2 ждёт выполнения, 3 выполняется, 5 завершена, 6 отложена.
    """
    fields: dict[str, Any] = {}
    if title:
        fields["TITLE"] = title
    if description:
        fields["DESCRIPTION"] = description
    if deadline:
        fields["DEADLINE"] = deadline
    if responsible_id:
        fields["RESPONSIBLE_ID"] = responsible_id
    if priority is not None:
        fields["PRIORITY"] = str(priority)
    if status is not None:
        fields["STATUS"] = str(status)
    if not fields:
        raise RuntimeError("Не передано ни одного поля для обновления")
    task = b24("tasks.task.update", {"taskId": task_id, "fields": fields})["task"]
    return _slim_task(task)


@mcp.tool()
def task_complete(task_id: int) -> str:
    """Завершить задачу (статус «завершена»)."""
    b24("tasks.task.complete", {"taskId": task_id})
    return f"Задача {task_id} завершена"


def _dialog_messages(dialog_id: str, limit: int) -> list[dict]:
    r = b24("im.dialog.messages.get", {"DIALOG_ID": dialog_id, "LIMIT": min(max(limit, 1), 50)})
    users = {u["id"]: u["name"] for u in r.get("users", [])}
    msgs = sorted(r.get("messages", []), key=lambda m: str(m.get("date")))
    return [
        {
            "date": str(m.get("date"))[:16],
            "author": users.get(m.get("author_id")) or "система",
            "text": m.get("text") or "",
        }
        for m in msgs
    ]


@mcp.tool()
def task_comments(task_id: int, limit: int = 20, include_system: bool = False) -> list[dict] | str:
    """Комментарии к задаче из её чата (свежие в конце).

    include_system=True — включить служебные записи (создание, смена срока и т.п.).
    """
    chat = b24("im.chat.get", {"ENTITY_TYPE": "TASKS_TASK", "ENTITY_ID": task_id})
    if not chat:
        return f"У задачи {task_id} нет чата — комментариев ещё не было."
    msgs = _dialog_messages(f"chat{chat['ID']}", limit=50)
    if not include_system:
        msgs = [m for m in msgs if m["author"] != "система"]
    return msgs[-limit:]


@mcp.tool()
def chats_recent(only_today: bool = False, limit: int = 30) -> list[dict]:
    """Последние диалоги и чаты: кто писал, последнее сообщение, непрочитанное.

    only_today=True — только диалоги с сообщениями за сегодня. Для чтения переписки
    используй chat_messages с полем dialog_id из результата.
    """
    items = b24("im.recent.list", {"SKIP_OPENLINES": "Y", "LIMIT": limit}).get("items", [])
    me = int(_me()["ID"])
    today = datetime.now(MSK).strftime("%Y-%m-%d")
    out = []
    for i in items:
        m = i.get("message") or {}
        if only_today and not str(m.get("date", "")).startswith(today):
            continue
        out.append({
            "dialog_id": str(i["id"]) if i.get("type") == "user" else f"chat{i.get('chat_id')}",
            "title": i.get("title"),
            "type": i.get("type"),
            "last_message": (m.get("text") or "")[:120],
            "last_from_me": m.get("author_id") == me,
            "date": str(m.get("date", ""))[:16],
            "unread": i.get("counter") or 0,
        })
    return out


@mcp.tool()
def chat_messages(dialog_id: str, limit: int = 20) -> list[dict]:
    """Прочитать переписку диалога. dialog_id — ID пользователя ("2265") или чат ("chat86230")."""
    return _dialog_messages(dialog_id, limit)[-limit:]


@mcp.tool()
def chat_send(dialog_id: str, message: str) -> str:
    """Отправить сообщение в диалог/чат ОТ ИМЕНИ владельца вебхука.

    ВАЖНО: использовать только по явной просьбе пользователя, всегда показывать
    текст и получателя перед отправкой.
    """
    msg_id = b24("im.message.add", {"DIALOG_ID": dialog_id, "MESSAGE": message})
    return f"Сообщение {msg_id} отправлено в {dialog_id}"


@mcp.tool()
def group_search(query: str) -> list[dict]:
    """Найти рабочую группу/проект по названию (для group_id при создании задач)."""
    groups = b24("sonet_group.get", {"FILTER": {"%NAME": query}}) or []
    return [
        {"id": g["ID"], "name": g["NAME"], "project": g.get("PROJECT") == "Y",
         "members": g.get("NUMBER_OF_MEMBERS"), "active": g.get("ACTIVE") == "Y"}
        for g in groups
    ][:20]


@mcp.tool()
def crm_deals(
    stage_id: str = "",
    category_id: int | None = None,
    created_after: str = "",
    search_title: str = "",
    limit: int = 25,
) -> list[dict]:
    """Список сделок CRM (свежие первыми). Фильтры: стадия, воронка, дата создания, название."""
    flt: dict[str, Any] = {}
    if stage_id:
        flt["STAGE_ID"] = stage_id
    if category_id is not None:
        flt["CATEGORY_ID"] = category_id
    if created_after:
        flt[">=DATE_CREATE"] = created_after
    if search_title:
        flt["%TITLE"] = search_title
    deals: list[dict] = []
    start = 0
    while len(deals) < limit:
        batch = b24("crm.deal.list", {
            "order": {"ID": "DESC"},
            "filter": flt,
            "select": ["ID", "TITLE", "STAGE_ID", "CATEGORY_ID", "OPPORTUNITY",
                       "CURRENCY_ID", "ASSIGNED_BY_ID", "DATE_CREATE", "CLOSED"],
            "start": start,
        }) or []
        deals.extend(batch)
        if len(batch) < 50:
            break
        start += 50
    return deals[:limit]


@mcp.tool()
def crm_deal_get(deal_id: int) -> dict:
    """Сделка CRM по ID — все поля."""
    return b24("crm.deal.get", {"id": deal_id})


@mcp.tool()
def call_stats(from_date: str = "", to_date: str = "") -> dict:
    """Статистика звонков (телефония). Даты ISO; по умолчанию — последние 7 дней.

    Возвращает сводку по первым 200 звонкам периода: всего, входящие/исходящие,
    средняя длительность, топ сотрудников.
    """
    now = datetime.now(MSK)
    flt = {
        ">=CALL_START_DATE": from_date or (now - timedelta(days=7)).isoformat(),
        "<=CALL_START_DATE": to_date or now.isoformat(),
    }
    calls: list[dict] = []
    start = 0
    while len(calls) < 200:
        batch = b24("voximplant.statistic.get", {"FILTER": flt, "SORT": "CALL_START_DATE", "ORDER": "DESC", "start": start}) or []
        calls.extend(batch)
        if len(batch) < 50:
            break
        start += 50
    incoming = [c for c in calls if str(c.get("CALL_TYPE")) == "2"]
    outgoing = [c for c in calls if str(c.get("CALL_TYPE")) == "1"]
    durations = [int(c.get("CALL_DURATION") or 0) for c in calls]
    by_user: dict[str, int] = {}
    for c in calls:
        u = str(c.get("PORTAL_USER_ID") or "?")
        by_user[u] = by_user.get(u, 0) + 1
    return {
        "period": {"from": flt[">=CALL_START_DATE"][:10], "to": flt["<=CALL_START_DATE"][:10]},
        "analyzed": len(calls),
        "note": "проанализированы первые 200 звонков периода" if len(calls) >= 200 else None,
        "incoming": len(incoming),
        "outgoing": len(outgoing),
        "avg_duration_sec": round(sum(durations) / len(durations)) if durations else 0,
        "top_users_by_calls": sorted(by_user.items(), key=lambda x: -x[1])[:10],
    }


@mcp.tool()
def b24_call(method: str, params: dict | None = None) -> Any:
    """Вызвать ЛЮБОЙ метод REST API Bitrix24 — полный охват API портала.

    Примеры: method="crm.lead.list", params={"filter": {">DATE_CREATE": "2026-08-01"}, "select": ["ID", "TITLE"]};
    method="disk.folder.getchildren", params={"id": 123}; method="methods" — список доступных методов.
    Доступно всё в рамках прав вебхука: task, crm, im, disk, calendar, sonet_group,
    telephony, user, department, log. Документация: apidocs.bitrix24.com.
    ВАЖНО: перед вызовом изменяющих методов (add/update/delete/set) показывай
    пользователю, что именно будет сделано.
    """
    return b24(method, params or {})


@mcp.tool()
def b24_list_all(
    method: str,
    filter: dict | None = None,
    select: list | None = None,
    order: dict | None = None,
    limit: int = 200,
) -> list:
    """Выгрузить записи любого списочного метода с автопагинацией.

    Работает с crm.deal.list, crm.lead.list, crm.contact.list, user.get,
    tasks.task.list и другими *.list-методами. limit — максимум записей (потолок 2000).
    """
    limit = min(limit, 2000)
    params: dict[str, Any] = {}
    if filter:
        params["filter"] = filter
    if select:
        params["select"] = select
    if order:
        params["order"] = order
    rows: list = []
    start = 0
    while len(rows) < limit:
        res = b24(method, {**params, "start": start})
        batch = res if isinstance(res, list) else next((v for v in res.values() if isinstance(v, list)), [])
        rows.extend(batch)
        if len(batch) < 50:
            break
        start += 50
    return rows[:limit]


@mcp.tool()
def departments() -> list[dict]:
    """Структура компании: отделы, иерархия, руководители."""
    deps = b24("department.get") or []
    return [
        {"id": d["ID"], "name": d["NAME"], "parent": d.get("PARENT"), "head_user_id": d.get("UF_HEAD")}
        for d in deps
    ]


@mcp.tool()
def task_comment_add(task_id: int, message: str) -> str:
    """Добавить комментарий к задаче (BB-код поддерживается)."""
    comment_id = b24("task.commentitem.add", {
        "TASKID": task_id,
        "FIELDS": {"POST_MESSAGE": message},
    })
    return f"Комментарий {comment_id} добавлен к задаче {task_id}"


@mcp.tool()
def user_search(query: str) -> list[dict]:
    """Найти сотрудников по имени, фамилии или должности (для responsible_id и т.п.)."""
    users = b24("user.search", {"FILTER": {"FIND": query}}) or []
    return [
        {
            "id": u["ID"],
            "name": f"{u.get('NAME', '')} {u.get('LAST_NAME', '')}".strip(),
            "position": u.get("WORK_POSITION"),
            "active": u.get("ACTIVE"),
        }
        for u in users
        if u.get("ACTIVE")
    ][:25]


@mcp.tool()
def calendar_events(from_date: str = "", to_date: str = "", owner_id: int | None = None) -> list[dict]:
    """События календаря пользователя. Даты в формате YYYY-MM-DD.

    По умолчанию — ближайшие 7 дней владельца вебхука.
    """
    today = datetime.now(MSK).date()
    events = b24("calendar.event.get", {
        "type": "user",
        "ownerId": owner_id or int(_me()["ID"]),
        "from": from_date or str(today),
        "to": to_date or str(today + timedelta(days=7)),
    }) or []
    return [
        {
            "id": e.get("ID"),
            "name": e.get("NAME"),
            "from": e.get("DATE_FROM"),
            "to": e.get("DATE_TO"),
            "location": e.get("LOCATION") or None,
            "description": (e.get("DESCRIPTION") or "")[:200] or None,
        }
        for e in events
    ]


@mcp.tool()
def calendar_event_add(
    name: str,
    from_datetime: str,
    to_datetime: str,
    description: str = "",
    all_day: bool = False,
) -> str:
    """Создать событие в личном календаре.

    from_datetime/to_datetime — "ДД.ММ.ГГГГ ЧЧ:ММ:СС" или ISO; для all_day достаточно даты.
    """
    event_id = b24("calendar.event.add", {
        "type": "user",
        "ownerId": int(_me()["ID"]),
        "name": name,
        "from": from_datetime,
        "to": to_datetime,
        "description": description,
        "skip_time": "Y" if all_day else "N",
        "section": _default_section(),
    })
    return f"Событие {event_id} создано"


def _default_section() -> int:
    sections = b24("calendar.section.get", {"type": "user", "ownerId": int(_me()["ID"])}) or []
    if not sections:
        raise RuntimeError("Не найдено ни одного раздела календаря")
    return int(sections[0]["ID"])


@mcp.tool()
def my_digest() -> dict:
    """Дайджест: просроченные задачи, дедлайны на неделю, открытые задачи, события на сегодня."""
    me = int(_me()["ID"])
    now = datetime.now(MSK)
    week = now + timedelta(days=7)
    open_filter = {"RESPONSIBLE_ID": me, "REAL_STATUS": ["1", "2", "3", "4", "6"]}

    overdue = _list_tasks({**open_filter, "<DEADLINE": now.isoformat()}, limit=25)
    due_week = _list_tasks(
        {**open_filter, ">=DEADLINE": now.isoformat(), "<=DEADLINE": week.isoformat()}, limit=25
    )
    all_open = _list_tasks(open_filter, limit=50, order={"ID": "desc"})

    today_events = b24("calendar.event.get", {
        "type": "user", "ownerId": me,
        "from": str(now.date()), "to": str(now.date()),
    }) or []

    unread = [
        {"title": i.get("title"), "unread": i.get("counter")}
        for i in b24("im.recent.list", {"SKIP_OPENLINES": "Y", "LIMIT": 50}).get("items", [])
        if i.get("counter")
    ]

    slim = lambda ts: [{"id": t["id"], "title": t["title"], "deadline": t.get("deadline")} for t in map(_slim_task, ts)]
    return {
        "date": now.strftime("%Y-%m-%d %H:%M"),
        "overdue": slim(overdue),
        "due_this_week": slim(due_week),
        "open_total": len(all_open),
        "open_without_deadline": len([t for t in all_open if not t.get("deadline")]),
        "today_events": [
            {"name": e.get("NAME"), "from": e.get("DATE_FROM"), "to": e.get("DATE_TO")}
            for e in today_events
        ],
        "unread_chats": unread[:15],
    }


if __name__ == "__main__":
    if os.environ.get("MCP_TRANSPORT") == "http":
        # Remote-режим (Fly.io): авторизация секретным сегментом пути.
        secret = os.environ.get("MCP_SECRET_PATH", "")
        if not secret:
            raise SystemExit("В HTTP-режиме обязателен MCP_SECRET_PATH")
        mcp.run(
            transport="streamable-http",
            host="0.0.0.0",
            port=int(os.environ.get("PORT", "8080")),
            streamable_http_path=f"/{secret}/mcp",
            stateless_http=True,
            json_response=True,
        )
    else:
        mcp.run()
