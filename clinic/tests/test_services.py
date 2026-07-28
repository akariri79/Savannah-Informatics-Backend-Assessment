from datetime import date, datetime, timedelta

from django.test import TestCase
from django.utils import timezone

from clinic.models import Appointment, Doctor, DoctorSchedule, Patient
from clinic.services import (
    BookingError,
    get_available_slots,
    get_slots_for_day,
    validate_slot_is_bookable,
)


def _next_weekday(weekday):
    """Return the date of the next occurrence of `weekday` (0=Mon) from today, at least 2 days out."""
    today = timezone.localdate()
    days_ahead = (weekday - today.weekday()) % 7
    if days_ahead < 2:
        days_ahead += 7
    return today + timedelta(days=days_ahead)


class SlotGenerationTests(TestCase):
    def setUp(self):
        self.doctor = Doctor.objects.create(name="Dr. Amara Okafor")
        # Works Mondays 09:00-11:00 -> 4 slots of 30 minutes
        DoctorSchedule.objects.create(
            doctor=self.doctor,
            weekday=DoctorSchedule.Weekday.MONDAY,
            start_time="09:00",
            end_time="11:00",
        )
        self.monday = _next_weekday(DoctorSchedule.Weekday.MONDAY)

    def test_generates_correct_number_of_slots(self):
        slots = get_slots_for_day(self.doctor, self.monday)
        self.assertEqual(len(slots), 4)
        self.assertEqual(slots[0][0].time().strftime("%H:%M"), "09:00")
        self.assertEqual(slots[-1][1].time().strftime("%H:%M"), "11:00")

    def test_no_schedule_means_no_slots(self):
        tuesday = self.monday + timedelta(days=1)
        self.assertEqual(get_slots_for_day(self.doctor, tuesday), [])

    def test_booked_slot_excluded_from_availability(self):
        patient = Patient.objects.create(name="Jane Doe", email="jane@example.com")
        first_start, first_end = get_slots_for_day(self.doctor, self.monday)[0]
        Appointment.objects.create(
            doctor=self.doctor,
            patient=patient,
            start_time=first_start,
            end_time=first_end,
            status=Appointment.Status.BOOKED,
        )
        available = get_available_slots(self.doctor, self.monday)
        self.assertNotIn((first_start, first_end), available)
        self.assertEqual(len(available), 3)

    def test_cancelled_appointment_frees_the_slot(self):
        patient = Patient.objects.create(name="Jane Doe", email="jane@example.com")
        first_start, first_end = get_slots_for_day(self.doctor, self.monday)[0]
        Appointment.objects.create(
            doctor=self.doctor,
            patient=patient,
            start_time=first_start,
            end_time=first_end,
            status=Appointment.Status.CANCELLED,
        )
        available = get_available_slots(self.doctor, self.monday)
        self.assertIn((first_start, first_end), available)


class BookingValidationTests(TestCase):
    def setUp(self):
        self.doctor = Doctor.objects.create(name="Dr. Amara Okafor")
        DoctorSchedule.objects.create(
            doctor=self.doctor,
            weekday=DoctorSchedule.Weekday.MONDAY,
            start_time="09:00",
            end_time="11:00",
        )
        self.patient = Patient.objects.create(name="Jane Doe", email="jane@example.com")
        self.monday = _next_weekday(DoctorSchedule.Weekday.MONDAY)
        self.first_start, self.first_end = get_slots_for_day(self.doctor, self.monday)[0]

    def test_valid_slot_passes(self):
        # Should not raise.
        validate_slot_is_bookable(self.doctor, self.first_start, self.first_end)

    def test_slot_outside_working_hours_rejected(self):
        bad_start = self.first_start.replace(hour=20, minute=0)
        bad_end = bad_start + timedelta(minutes=30)
        with self.assertRaises(BookingError):
            validate_slot_is_bookable(self.doctor, bad_start, bad_end)

    def test_misaligned_slot_rejected(self):
        # 09:15 doesn't align to a 30-minute boundary starting at 09:00.
        bad_start = self.first_start.replace(minute=15)
        bad_end = bad_start + timedelta(minutes=30)
        with self.assertRaises(BookingError):
            validate_slot_is_bookable(self.doctor, bad_start, bad_end)

    def test_slot_in_the_past_rejected(self):
        past_start = timezone.now() - timedelta(days=1)
        past_end = past_start + timedelta(minutes=30)
        with self.assertRaises(BookingError):
            validate_slot_is_bookable(self.doctor, past_start, past_end)

    def test_slot_within_lead_time_rejected(self):
        soon_start = timezone.now() + timedelta(minutes=10)
        soon_end = soon_start + timedelta(minutes=30)
        with self.assertRaises(BookingError):
            validate_slot_is_bookable(self.doctor, soon_start, soon_end)

    def test_double_booking_rejected(self):
        Appointment.objects.create(
            doctor=self.doctor,
            patient=self.patient,
            start_time=self.first_start,
            end_time=self.first_end,
            status=Appointment.Status.BOOKED,
        )
        with self.assertRaises(BookingError):
            validate_slot_is_bookable(self.doctor, self.first_start, self.first_end)

    def test_reschedule_excludes_own_appointment(self):
        appt = Appointment.objects.create(
            doctor=self.doctor,
            patient=self.patient,
            start_time=self.first_start,
            end_time=self.first_end,
            status=Appointment.Status.BOOKED,
        )
        # Rescheduling to the same slot it already occupies should be fine
        # once we tell validate_slot_is_bookable to ignore its own row.
        validate_slot_is_bookable(
            self.doctor, self.first_start, self.first_end, exclude_appointment_id=appt.id
        )
