from datetime import date, datetime, time
from enum import StrEnum

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from ..models import Message

MODEL_NAME = "gpt-4.1-mini"

SYSTEM_PROMPT = """\
Ты — ассистент салона красоты. Твоя задача — разобрать ПОСЛЕДНЮЮ реплику клиента \
в структурированный интент. Отвечай только структурой, без пояснений.

Текущий момент времени: {now}. Используй его, чтобы превращать относительные \
выражения в абсолютные даты ("сегодня" = {today}, "завтра" = следующий день и т.д.). \
Если назван месяц без года (например "20 мая") — бери ближайшую будущую дату \
относительно текущего момента (обычно текущий год {year}).

Поле `intent` выбирай так:
- "booking" — клиент хочет ЗАПИСАТЬСЯ на услугу.
- "cancellation" — клиент хочет ОТМЕНИТЬ запись.
- "continue_current_intent" — клиент не меняет тип сценария, а лишь уточняет или \
  правит поле в рамках уже идущего диалога (например, поправляет услугу/дату/время). \
  Используй этот label ТОЛЬКО если из истории видно активный сценарий и текущая \
  реплика его продолжает, не переключая на другой тип.
- "unknown" — намерение непонятно и активного сценария нет (приветствие, оффтоп).

Важно про смену намерения (intent flip): если в истории шёл один сценарий (например \
запись), а клиент в новой реплике явно просит ДРУГОЙ тип действия (например "наоборот \
отмените" / "забудьте, лучше запишите"), выбирай новый тип ("booking" или \
"cancellation"), а НЕ "continue_current_intent".

Извлекай поля service / desired_date / desired_time ТОЛЬКО если они явно присутствуют \
в текущей реплике. Если поля нет — оставляй null (не придумывай). \
`service` пиши в нижнем регистре, в именительном падеже (маникюр, педикюр, стрижка)."""


def _format_history(history: list[Message]) -> str:
    if not history:
        return "(история пуста)"
    lines = []
    for m in history:
        who = "Клиент" if m.role == "user" else "Ассистент"
        lines.append(f"{who}: {m.content}")
    return "\n".join(lines)


class IntentLabel(StrEnum):
    BOOKING = "booking"
    CANCELLATION = "cancellation"
    UNKNOWN = "unknown"
    CONTINUE = "continue_current_intent"


class ExtractedIntent(BaseModel):
    """Структурированный результат разбора реплики пользователя.

    Заполняется LLM. Все поля, кроме `intent`, опциональны — экстрактор
    вытаскивает только то, что реально присутствует в реплике.
    """

    intent: IntentLabel = Field(
        description=(
            "Тип намерения в текущей реплике: "
            "'booking' — хочет записаться; "
            "'cancellation' — хочет отменить запись; "
            "'continue_current_intent' — уточняет/меняет поле в рамках уже активного "
            "сценария, не переключая его (например, меняет услугу при записи); "
            "'unknown' — намерение не распознано и активного сценария нет."
        ),
    )
    service: str | None = Field(
        default=None,
        description=(
            "Услуга салона, если названа (например 'маникюр', 'педикюр', 'стрижка'). "
            "В нижнем регистре, именительный падеж. None, если услуги в реплике нет."
        ),
    )
    desired_date: date | None = Field(
        default=None,
        description=(
            "Дата записи/отмены. Относительные выражения ('сегодня', 'завтра', "
            "'20 мая') нужно резолвить в абсолютную дату относительно текущего момента. "
            "None, если даты в реплике нет."
        ),
    )
    desired_time: time | None = Field(
        default=None,
        description=(
            "Время суток без даты (например 15:00, 11:00). "
            "None, если времени в реплике нет."
        ),
    )


def extract_user_intent(
    message: str,
    history: list[Message],
    now: datetime,
) -> ExtractedIntent:
    """Разобрать реплику пользователя в структурированный `ExtractedIntent`.

    Args:
        message: текущая реплика пользователя (raw text).
        history: предыдущие реплики диалога — нужны, чтобы корректно отличать
            новый интент от продолжения текущего.
        now: текущее время

    Returns:
        `ExtractedIntent` с заполненными полями
    """
    system = SYSTEM_PROMPT.format(
        now=now.strftime("%Y-%m-%d %H:%M (%A)"),
        today=now.date().isoformat(),
        year=now.year,
    )
    user = (
        f"История диалога:\n{_format_history(history)}\n\n"
        f"Текущая реплика клиента: {message}"
    )

    llm = ChatOpenAI(model=MODEL_NAME, temperature=0)
    structured = llm.with_structured_output(ExtractedIntent)

    try:
        result = structured.invoke([
            SystemMessage(content=system),
            HumanMessage(content=user),
        ])
    except Exception:
        # Мягкая деградация: не роняем pipeline, отдаём unknown без полей.
        return ExtractedIntent(intent=IntentLabel.UNKNOWN)

    if not isinstance(result, ExtractedIntent):
        return ExtractedIntent(intent=IntentLabel.UNKNOWN)
    return result
