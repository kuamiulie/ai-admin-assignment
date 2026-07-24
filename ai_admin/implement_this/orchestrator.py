from datetime import datetime
from typing import assert_never

from .extractor import ExtractedIntent, IntentLabel, extract_user_intent
from .. import booking_api
from ..models import (
    Action,
    AssistantTurn,
    BookingResult,
    BookingSearchResult,
    CancellationResult,
    ClarifyUser,
    ConversationState,
    ExecutedAction,
    Message,
    Plan,
)

NOW = datetime(2026, 5, 18, 10, 0)


def process_message(
    message: str,
    state: ConversationState,
) -> AssistantTurn:
    """Обработать одну реплику пользователя и вернуть ход ассистента.

    Args:
        message: текст реплики пользователя.
        state:   текущее состояние диалога (immutable — функция возвращает новое).

    Returns:
        `AssistantTurn` с reply-текстом, новым состоянием и executed action (если был).
    """
    intent: ExtractedIntent = extract_user_intent(message, state.history, now=NOW)
    new_state: ConversationState = update_state(state, intent=intent, message=message)
    plan: Plan = decide_next_step(new_state)

    match plan:
        case Action():
            executed = execute_action(plan)
            post_action_state = apply_action_result(new_state, executed)
            response = generate_response(post_action_state)
        case ClarifyUser():
            executed = None
            post_action_state = new_state
            response = plan.question
        case _ as unreachable:
            assert_never(unreachable)

    final_state = post_action_state.model_copy(
        update={"history": [*post_action_state.history, Message(role="assistant", content=response)]},
    )

    return AssistantTurn(
        response=response,
        state=final_state,
        action=executed,
    )


def update_state(
    state: ConversationState,
    intent: ExtractedIntent,
    message: str,
) -> ConversationState:
    """Применить новый intent поверх state и вернуть обновлённый ConversationState.

    Что нужно сделать:
    - Всегда дописать текущую user-реплику в `history`.
    - Если в intent пришёл новый интент относительно `state.active_intent` — переключить `active_intent` и сбросить `status` в "collecting_required_fields".
    - Если в intent заполнены услуга и желамая дата и время визита — переписать соответствующие поля state. Значения None не затирают существующие.
    """
    updates: dict = {
        "history": [*state.history, Message(role="user", content=message)],
    }

    is_new_intent_turn = intent.intent in (IntentLabel.BOOKING, IntentLabel.CANCELLATION)

    # Предыдущий ход завершил сценарий (запись создана / отменена). Любая следующая
    # реплика начинает сбор заново: не тащим старые service/date/time, иначе реплика
    # вроде «хочу записаться» (которую LLM часто метит continue_current_intent)
    # «доехала» бы до готового Action на прошлых полях и создала бы запись без запроса.
    if state.status in ("booked", "cancelled"):
        updates["service"] = None
        updates["desired_date"] = None
        updates["desired_time"] = None
        updates["mismatched_service"] = None
        updates["status"] = "collecting_required_fields"

    # Смена интента: booking/cancellation, отличный от текущего, переключает сценарий
    # и сбрасывает фазу. `continue_current_intent` / `unknown` не трогают active_intent.
    if is_new_intent_turn:
        new_intent = intent.intent.value
        if new_intent != state.active_intent:
            updates["active_intent"] = new_intent
            updates["status"] = "collecting_required_fields"

    # Перезапись полей только непустыми значениями — None не затирает существующее.
    if intent.service is not None:
        updates["service"] = intent.service
    if intent.desired_date is not None:
        updates["desired_date"] = intent.desired_date
    if intent.desired_time is not None:
        updates["desired_time"] = intent.desired_time

    return state.model_copy(update=updates)


def decide_next_step(state: ConversationState) -> Plan:
    """Решить, что делать следующим шагом: задать уточняющий вопрос или сделать Action с `booking_api`.

    Задать уточняющий вопрос нужно в случаях:
    - Интент неизвестен
    - Для booking: не хватает service или даты или времени
    - Для cancellation: нет времени
    Иначе - необходимо сформировать параметризованный Action для `booking_api` на конкретный интент
    """
    if state.active_intent is None:
        return ClarifyUser(
            question="Здравствуйте! Вы хотите записаться на услугу или отменить запись?",
        )

    if state.active_intent == "booking":
        if state.service is None:
            return ClarifyUser(question="На какую услугу вы хотите записаться?")
        if state.desired_date is None:
            return ClarifyUser(question="На какую дату вы хотите записаться?")
        if state.desired_time is None:
            return ClarifyUser(question="На какое время хотите записаться?")
        return Action(
            name="create_booking",
            args={
                "service": state.service,
                "date": state.desired_date,
                "time": state.desired_time,
            },
        )

    # cancellation: обязательны дата и время; услуга опциональна.
    if state.desired_date is None:
        return ClarifyUser(question="На какую дату была запись, которую нужно отменить?")
    if state.desired_time is None:
        return ClarifyUser(question="На какое время была запись, которую нужно отменить?")
    return Action(
        name="cancel_appointment",
        args={
            "date": state.desired_date,
            "time": state.desired_time,
            "service": state.service,
        },
    )


def execute_action(action: Action) -> ExecutedAction:
    """Выполнить запланированный action через функции из `booking_api`.

    В зависимости от имени action:
    - `create_booking` — позвать `create_booking(service, when)` и вернуть результат как есть.
    - `cancel_appointment` — это композит: сначала `find_booking(when, service)`.
        Если статус `found` — позвать `cancel_booking(booking_id)`.
        Если запись не найдена или услуга не совпала — вернуть search-результат как есть, без вызова cancel.
    """
    when = datetime.combine(action.args["date"], action.args["time"])
    # booking_api хранит naive datetime; LLM может вернуть время с tzinfo (UTC).
    # Приводим к naive, иначе сравнение слотов (aware != naive) промахивается.
    if when.tzinfo is not None:
        when = when.replace(tzinfo=None)

    if action.name == "create_booking":
        result = booking_api.create_booking(action.args["service"], when)
        return ExecutedAction(name=action.name, args=action.args, result=result)

    if action.name == "cancel_appointment":
        search = booking_api.find_booking(when, action.args.get("service"))
        if search.status == "found" and search.booking_id is not None:
            cancel = booking_api.cancel_booking(search.booking_id)
            return ExecutedAction(name=action.name, args=action.args, result=cancel)
        return ExecutedAction(name=action.name, args=action.args, result=search)

    raise ValueError(f"Unknown action: {action.name}")


def apply_action_result(
    state: ConversationState,
    action: ExecutedAction,
) -> ConversationState:
    """Обновить `status` state (и `mismatched_service` при необходимости) по результату action.

    Логика переходов:
    - create_booking успешен → status "booked".
    - create_booking уперся в занятый слот → сбросить дату и время и поставить status "slot_unavailable".
    - find_booking вернул not_found → status "booking_not_found".
    - find_booking вернул service_mismatch → status "booking_service_mismatch", записать actual_service в `mismatched_service` (это пригодится generate_response).
    - cancel_booking успешен → status "cancelled".
    - cancel_booking упал → status "cancellation_failed".
    """
    result = action.result

    if isinstance(result, BookingResult):
        if not result.needs_clarification:
            return state.model_copy(update={"status": "booked"})
        # слот занят — сбрасываем дату/время, чтобы уточнить заново
        return state.model_copy(
            update={
                "status": "slot_unavailable",
                "desired_date": None,
                "desired_time": None,
            },
        )

    if isinstance(result, BookingSearchResult):
        if result.status == "not_found":
            return state.model_copy(update={"status": "booking_not_found"})
        if result.status == "service_mismatch":
            return state.model_copy(
                update={
                    "status": "booking_service_mismatch",
                    "mismatched_service": result.actual_service,
                },
            )
        # status == "found" здесь не ожидается: found ведёт к CancellationResult
        return state

    if isinstance(result, CancellationResult):
        if not result.needs_clarification:
            return state.model_copy(update={"status": "cancelled"})
        return state.model_copy(update={"status": "cancellation_failed"})

    assert_never(result)


def generate_response(state: ConversationState) -> str:
    """Собрать текстовый ответ пользователю по `state.status` шаблонами (без LLM).

    В зависимости от статуса:
    - "booked" — подтверждение записи с услугой, временем и датой
    - "slot_unavailable" — попросить выбрать другое дату и/или время.
    - "booking_not_found" — сказать, что записи на это время нет.
    - "booking_service_mismatch" — пояснить, что на это время другая услуга (`state.mismatched_service`).
    - "cancelled" — подтверждение отмены.
    - "cancellation_failed" — пояснить, что отменить не вышло.
    """
    date_str = state.desired_date.strftime("%d.%m.%Y") if state.desired_date else "—"
    time_str = state.desired_time.strftime("%H:%M") if state.desired_time else "—"
    service = state.service or "услугу"

    match state.status:
        case "booked":
            return f"Готово! Записал вас на «{service}» {date_str} в {time_str}."
        case "slot_unavailable":
            return (
                "К сожалению, это время уже занято. "
                "Пожалуйста, выберите другую дату и/или время."
            )
        case "booking_not_found":
            return f"На {date_str} в {time_str} записи не найдено — отменять нечего."
        case "booking_service_mismatch":
            actual = state.mismatched_service or "другая услуга"
            return (
                f"На {date_str} в {time_str} записана другая услуга — «{actual}». "
                "Проверьте, пожалуйста, услугу или время записи."
            )
        case "cancelled":
            return f"Запись на {date_str} в {time_str} отменена."
        case "cancellation_failed":
            return (
                "Не удалось отменить запись по технической причине. "
                "Попробуйте, пожалуйста, ещё раз."
            )
        case _:
            # collecting_required_fields сюда обычно не доходит (обрабатывается ClarifyUser).
            return "Уточните, пожалуйста, детали записи."
