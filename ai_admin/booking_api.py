import uuid
from dataclasses import dataclass, field
from datetime import datetime

from .models import (
    BookingResult,
    BookingSearchResult,
    CancellationResult,
)


@dataclass
class _Booking:
    id: str
    service: str
    datetime: datetime


@dataclass
class _Calendar:
    bookings: list[_Booking] = field(default_factory=list)

    def find_at(self, when: datetime) -> _Booking | None:
        for b in self.bookings:
            if b.datetime == when:
                return b
        return None

    def reset(self) -> None:
        self.bookings.clear()


_CALENDAR = _Calendar(
    bookings=[
        _Booking(
            id="b1",
            service="маникюр",
            datetime=datetime(2026, 5, 20, 15, 0),
        ),
    ],
)


def reset_calendar(seed: list[_Booking] | None = None) -> None:
    """Сбросить in-memory календарь. Полезно в тестах между кейсами."""
    _CALENDAR.reset()
    if seed:
        _CALENDAR.bookings.extend(seed)


def create_booking(service: str, when: datetime) -> BookingResult:
    """Создать запись. Возвращает needs_clarification=False, если время уже занято."""
    if _CALENDAR.find_at(when) is not None:
        return BookingResult(needs_clarification=True, reason="slot already booked")
    booking = _Booking(id=f"b{uuid.uuid4().hex[:8]}", service=service, datetime=when)
    _CALENDAR.bookings.append(booking)
    return BookingResult(needs_clarification=False, booking_id=booking.id)


def find_booking(when: datetime, service: str | None = None) -> BookingSearchResult:
    """Найти запись на указанное время.

    Если `service` задан — учитываем его в статусе: совпадает или нет.
    """
    existing = _CALENDAR.find_at(when)
    if existing is None:
        return BookingSearchResult(status="not_found", needs_clarification=True)
    if service is not None and existing.service != service:
        return BookingSearchResult(
            status="service_mismatch",
            needs_clarification=True,
            booking_id=existing.id,
            actual_service=existing.service,
        )
    return BookingSearchResult(
        status="found",
        needs_clarification=False,
        booking_id=existing.id,
        actual_service=existing.service,
    )


def cancel_booking(booking_id: str) -> CancellationResult:
    """Отменить запись по id. Идемпотентно: повторный вызов вернёт needs_clarification=False."""
    for i, b in enumerate(_CALENDAR.bookings):
        if b.id == booking_id:
            del _CALENDAR.bookings[i]
            return CancellationResult(needs_clarification=False)
    return CancellationResult(needs_clarification=True, reason="booking not found")
