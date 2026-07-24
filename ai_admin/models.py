from datetime import date, time
from typing import Any, Literal

from pydantic import BaseModel, Field


class Message(BaseModel):
    """Одна реплика в истории диалога — от пользователя или от ассистента."""

    role: Literal["user", "assistant"]
    content: str


ActiveIntent = Literal["booking", "cancellation"]


# Фаза, в которой находится сценарий. Используется `generate_response`,
# чтобы понять, какой текст выдать пользователю.
ConversationStatus = Literal[
    "collecting_required_fields",       # ассистент уточняет недостающие поля или интент
    "booked",                            # запись создана
    "cancelled",                         # запись отменена
    "slot_unavailable",                  # последняя попытка create_booking — слот занят
    "booking_not_found",                 # последняя попытка cancel: записи на это время нет
    "booking_service_mismatch",          # последняя попытка cancel: услуга не совпала
    "cancellation_failed",               # cancel_booking упал по технической причине
]


class ConversationState(BaseModel):
    """Полное состояние диалога между ходами бота.

    `process_message` каждый ход возвращает обновлённую копию state-а — старый
    state не мутируется. Все хелперы оркестратора работают чисто с этим типом.

    Поля:
    - `active_intent` — какой сценарий сейчас активен (booking / cancellation / None).
    - `service` — услуга, которую запросил пользователь.
    - `desired_date` — дата, на которую хотят записаться или отменить запись.
    - `desired_time` — время-суток (без даты).
    - `status` — текущая фаза сценария (см. `ConversationStatus`).
    - `mismatched_service` — при `status="booking_service_mismatch"` хранит
      услугу, которая фактически забронирована на запрошенный слот.
      Заполняется в `apply_action_result`, читается в `generate_response`.
    - `history` — хронология реплик user-а и ассистента.

    `desired_date` и `desired_time` независимы — LLM-экстрактор может вытащить
    только то, что есть в реплике пользователя; недостающее `decide_next_step`
    спросит отдельно.
    """

    active_intent: ActiveIntent | None = None

    service: str | None = None
    desired_date: date | None = None
    desired_time: time | None = None

    status: ConversationStatus = "collecting_required_fields"
    mismatched_service: str | None = None

    history: list[Message] = Field(default_factory=list)


class BookingResult(BaseModel):
    """Результат вызова `create_booking` из booking_api.

    `needs_clarification=True` означает, что слот занят и нужно попросить
    другое время.
    """

    needs_clarification: bool
    booking_id: str | None = None
    reason: str | None = None


class BookingSearchResult(BaseModel):
    """Результат вызова `find_booking` из booking_api.

    - `found`            — есть запись и услуга совпадает (или фильтр по услуге не задан).
    - `service_mismatch` — на это время есть запись, но услуга другая (`actual_service`).
    - `not_found`        — записи на это время нет.
    """

    status: Literal["found", "service_mismatch", "not_found"]
    needs_clarification: bool
    booking_id: str | None = None
    actual_service: str | None = None


class CancellationResult(BaseModel):
    """Результат вызова `cancel_booking` из booking_api.

    `needs_clarification=True` — отмена не удалась по технической причине
    """

    needs_clarification: bool
    reason: str | None = None


# Объединение всех возможных результатов инструментов. Тип-узел `ExecutedAction.result`.
ActionResult = BookingResult | BookingSearchResult | CancellationResult


class ClarifyUser(BaseModel):
    """План оркестратора: задать пользователю уточняющий вопрос.
    """

    question: str


class Action(BaseModel):
    """План оркестратора: вызвать инструмент с детерминированными аргументами.

    Имя action — логическое (`"create_booking"`, `"cancel_appointment"`),
    маппинг на конкретные функции `booking_api` живёт в `execute_action`.
    """

    name: str
    args: dict[str, Any]


class ExecutedAction(BaseModel):
    """Action после выполнения инструмента с результатом"""

    name: str
    args: dict[str, Any]
    result: ActionResult


Plan = ClarifyUser | Action


class AssistantTurn(BaseModel):
    """Результат одного хода `process_message`.

    - `response` — текст, который надо показать пользователю.
    - `state` — новое состояние диалога (передаётся в следующий вызов process_message).
    - `action` — лог выполненной action на этом ходу (None, если был ClarifyUser).
    """

    response: str
    state: ConversationState
    action: ExecutedAction | None = Field(default=None)
