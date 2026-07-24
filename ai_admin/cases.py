from dataclasses import dataclass
from datetime import date, time

from .models import ActiveIntent, ConversationState, ConversationStatus, Message


@dataclass
class Expected:
    """
    - `active_intent`: какой интент в state должен быть после хода.
    - `status`: какой `ConversationStatus`.
    - `action`: имя выполненного action ("create_booking" / "cancel_appointment")
      или None, если на этом шаге должен сработать `ClarifyUser`
    """

    active_intent: ActiveIntent | None
    status: ConversationStatus
    action: str | None


@dataclass
class Case:
    state: ConversationState
    current_message: Message
    expected: Expected


CASES: list[Case] = [
    # 1. полный booking, слот свободен → "booked"
    Case(
        state=ConversationState(),
        current_message=Message(role="user", content="Хочу записаться на маникюр завтра в 15:00"),
        expected=Expected(
            active_intent="booking",
            status="booked",
            action="create_booking",
        ),
    ),

    # 2. booking без времени — ассистент должен спросить время
    Case(
        state=ConversationState(),
        current_message=Message(role="user", content="Хочу записаться на маникюр"),
        expected=Expected(
            active_intent="booking",
            status="collecting_required_fields",
            action=None,
        ),
    ),

    # 3. service swap внутри booking-flow (continue_current_intent + смена поля)
    Case(
        state=ConversationState(
            active_intent="booking",
            service="маникюр",
            history=[
                Message(role="user", content="Хочу записаться на маникюр"),
                Message(role="assistant", content="На какое время хотите записаться?"),
            ],
        ),
        current_message=Message(role="user", content="Нет, я хочу не на маникюр, а на педикюр"),
        expected=Expected(
            active_intent="booking",
            status="collecting_required_fields",
            action=None,
        ),
    ),

    # 4. одношотный busy: full info, но слот сразу занят (b1: маникюр 20 мая 15:00)
    Case(
        state=ConversationState(),
        current_message=Message(role="user", content="Хочу записаться на стрижку 20 мая в 15:00"),
        expected=Expected(
            active_intent="booking",
            status="slot_unavailable",
            action="create_booking",
        ),
    ),

    # 5. отмена несуществующей записи (not_found)
    Case(
        state=ConversationState(),
        current_message=Message(role="user", content="Отмените мою запись на 25 мая в 14:00"),
        expected=Expected(
            active_intent="cancellation",
            status="booking_not_found",
            action="cancel_appointment",
        ),
    ),

    # 6. отмена с неправильной услугой (service_mismatch на b1)
    Case(
        state=ConversationState(),
        current_message=Message(role="user", content="Отмените мою запись на стрижку 20 мая в 15:00"),
        expected=Expected(
            active_intent="cancellation",
            status="booking_service_mismatch",
            action="cancel_appointment",
        ),
    ),

    # 7. intent flip booking → cancel в одной реплике (full info)
    Case(
        state=ConversationState(
            history=[
                Message(role="user", content="Хочу записаться на маникюр"),
                Message(role="assistant", content="На какое время хотите записаться?"),
            ],
        ),
        current_message=Message(
            role="user",
            content="Ой стоп, наоборот отмените мою запись на 20 мая в 15:00",
        ),
        expected=Expected(
            active_intent="cancellation",
            status="cancelled",
            action="cancel_appointment",
        ),
    ),

    # 8. intent flip cancel → book
    Case(
        state=ConversationState(
            history=[
                Message(role="user", content="Хочу отменить запись на 20 мая в 15:00"),
                Message(role="assistant", content="На какую услугу была запись?"),
            ],
        ),
        current_message=Message(
            role="user",
            content="Знаете что, забудьте — лучше запишите на стрижку завтра в 11",
        ),
        expected=Expected(
            active_intent="booking",
            status="booked",
            action="create_booking",
        ),
    ),

    # --- Дополнительные кейсы (Kaggle-style), покрывают сценарии, видимые в коде ---

    # 9. многоходовой booking: услуга уже собрана, приходит дата+время → booked.
    #    Слот 21 июня 12:00 заведомо свободен.
    Case(
        state=ConversationState(
            active_intent="booking",
            service="маникюр",
            history=[
                Message(role="user", content="Хочу записаться на маникюр"),
                Message(role="assistant", content="На какую дату вы хотите записаться?"),
            ],
        ),
        current_message=Message(role="user", content="21 июня в 12:00"),
        expected=Expected(
            active_intent="booking",
            status="booked",
            action="create_booking",
        ),
    ),

    # 10. booking есть услуга и дата, но нет времени → уточняющий вопрос (ClarifyUser).
    Case(
        state=ConversationState(
            active_intent="booking",
            service="стрижка",
            desired_date=date(2026, 6, 22),
            history=[
                Message(role="user", content="Запишите на стрижку 22 июня"),
                Message(role="assistant", content="На какое время хотите записаться?"),
            ],
        ),
        current_message=Message(role="user", content="Пока не решил по времени"),
        expected=Expected(
            active_intent="booking",
            status="collecting_required_fields",
            action=None,
        ),
    ),

    # 11. cancellation с указанной услугой на заведомо пустой слот → not_found.
    #     Проверяет, что услуга в cancel-ветке необязательна и не ломает поиск,
    #     а пустой слот честно даёт booking_not_found. Порядко-независимо.
    Case(
        state=ConversationState(),
        current_message=Message(role="user", content="Отмените маникюр 30 июня в 09:00"),
        expected=Expected(
            active_intent="cancellation",
            status="booking_not_found",
            action="cancel_appointment",
        ),
    ),

    # 12. одношотный booking на свободный слот другой формулировкой → booked.
    #     Слот 23 июня 18:00 заведомо свободен. Порядко-независимо.
    Case(
        state=ConversationState(),
        current_message=Message(
            role="user",
            content="Можно записаться на педикюр 23 июня к шести вечера?",
        ),
        expected=Expected(
            active_intent="booking",
            status="booked",
            action="create_booking",
        ),
    ),

    # 13. unknown intent: приветствие без активного сценария → уточняющий вопрос.
    Case(
        state=ConversationState(),
        current_message=Message(role="user", content="Здравствуйте!"),
        expected=Expected(
            active_intent=None,
            status="collecting_required_fields",
            action=None,
        ),
    ),

    # 14. booking без даты, но со временем → спросить дату (недостаёт именно даты).
    Case(
        state=ConversationState(),
        current_message=Message(role="user", content="Запишите на маникюр в 16:00"),
        expected=Expected(
            active_intent="booking",
            status="collecting_required_fields",
            action=None,
        ),
    ),

    # 15. РЕГРЕССИЯ: после завершённой записи (status="booked") клиент говорит
    #     «хочу записаться» без деталей. Старые service/date/time НЕ должны
    #     протечь в новый Action — ассистент обязан переспросить услугу.
    Case(
        state=ConversationState(
            active_intent="booking",
            status="booked",
            service="педикюр",
            desired_date=date(2026, 5, 19),
            desired_time=time(16, 0),
            history=[
                Message(role="user", content="Запишите на педикюр 19 мая в 16:00"),
                Message(role="assistant", content="Готово! Записал вас на «педикюр» 19.05.2026 в 16:00."),
            ],
        ),
        current_message=Message(role="user", content="Хочу записаться к вам"),
        expected=Expected(
            active_intent="booking",
            status="collecting_required_fields",
            action=None,
        ),
    ),

    # 16. РЕГРЕССИЯ: после завершённой отмены (status="cancelled") клиент говорит
    #     «запишите меня» без деталей. Хвост полей от отменённой записи не должен
    #     привести к автосозданию брони — ассистент переспрашивает услугу.
    Case(
        state=ConversationState(
            active_intent="cancellation",
            status="cancelled",
            service="педикюр",
            desired_date=date(2026, 5, 19),
            desired_time=time(16, 0),
            history=[
                Message(role="user", content="Отмените запись на 19 мая в 16:00"),
                Message(role="assistant", content="Запись на 19.05.2026 в 16:00 отменена."),
            ],
        ),
        current_message=Message(role="user", content="А теперь запишите меня"),
        expected=Expected(
            active_intent="booking",
            status="collecting_required_fields",
            action=None,
        ),
    ),
]
