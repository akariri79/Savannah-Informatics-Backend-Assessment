"""
Core booking business logic, kept separate from views/serializers so it can
be unit tested directly and reused (e.g. availability is used both by the
availability endpoint and by booking validation).
"""

from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework import serializers

from .models import Appointment, DoctorSchedule


class BookingError(serializers.ValidationError):
    """Raised for any booking rule violation. Maps to a 400 response."""


def get_slots_for_day(doctor, date):
    """
    Return a list of (start, end) datetime tuples for every 30-minute slot
    in the doctor's working hours on the given date, in the doctor's
    schedule for that weekday. Returns an empty list if the doctor has no
    schedule for that weekday (i.e. they don't work that day).
    """
    weekday = date.weekday()
    schedule = DoctorSchedule.objects.filter(doctor=doctor, weekday=weekday).first()
    if schedule is None:
        return []

    tz = timezone.get_current_timezone()
    slot_minutes = settings.SLOT_DURATION_MINUTES

    day_start = timezone.make_aware(datetime.combine(date, schedule.start_time), tz)
    day_end = timezone.make_aware(datetime.combine(date, schedule.end_time), tz)

    slots = []
    cursor = day_start
    while cursor + timedelta(minutes=slot_minutes) <= day_end:
        slots.append((cursor, cursor + timedelta(minutes=slot_minutes)))
        cursor += timedelta(minutes=slot_minutes)
    return slots


def get_available_slots(doctor, date):
    """
    Return the subset of get_slots_for_day() that aren't already booked.
    """
    all_slots = get_slots_for_day(doctor, date)
    if not all_slots:
        return []

    booked_starts = set(
        Appointment.objects.filter(
            doctor=doctor,
            status=Appointment.Status.BOOKED,
            start_time__date=date,
        ).values_list("start_time", flat=True)
    )

    return [(start, end) for start, end in all_slots if start not in booked_starts]


def validate_slot_is_bookable(doctor, start_time, end_time, exclude_appointment_id=None):
    """
    Raise BookingError if the given slot cannot be booked for this doctor.
    Used by both fresh bookings and reschedules.

    Checks, in order:
    1. Slot is not in the past (and honours the 1-hour lead-time rule).
    2. Slot aligns with one of the doctor's actual 30-minute working slots.
    3. Slot isn't already taken by another *booked* appointment.
    """
    now = timezone.now()

    min_start = now + timedelta(minutes=settings.MIN_BOOKING_LEAD_TIME_MINUTES)
    if start_time < min_start:
        if start_time < now:
            raise BookingError(
                {"start_time": "Cannot book an appointment in the past."}
            )
        raise BookingError(
            {
                "start_time": (
                    f"Appointments must be booked at least "
                    f"{settings.MIN_BOOKING_LEAD_TIME_MINUTES} minutes in advance."
                )
            }
        )

    valid_slots = get_slots_for_day(doctor, start_time.date())
    if (start_time, end_time) not in valid_slots:
        raise BookingError(
            {
                "start_time": (
                    "This is not a valid working slot for this doctor on this date. "
                    "Slots must align to the doctor's working hours in "
                    f"{settings.SLOT_DURATION_MINUTES}-minute increments."
                )
            }
        )

    clashing = Appointment.objects.filter(
        doctor=doctor,
        start_time=start_time,
        status=Appointment.Status.BOOKED,
    )
    if exclude_appointment_id is not None:
        clashing = clashing.exclude(id=exclude_appointment_id)

    if clashing.exists():
        raise BookingError({"start_time": "This slot is already booked."})
