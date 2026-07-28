from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from clinic.models import Appointment, Doctor, DoctorSchedule, Patient
from clinic.services import get_slots_for_day


def _next_weekday(weekday):
    today = timezone.localdate()
    days_ahead = (weekday - today.weekday()) % 7
    if days_ahead < 2:
        days_ahead += 7
    return today + timedelta(days=days_ahead)


class AppointmentAPITests(APITestCase):
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

    def test_availability_endpoint_lists_open_slots(self):
        url = reverse("doctor-availability", args=[self.doctor.id])
        response = self.client.get(url, {"date": self.monday.isoformat()})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["available_slots"]), 4)

    def test_availability_requires_date_param(self):
        url = reverse("doctor-availability", args=[self.doctor.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_book_appointment_success(self):
        url = reverse("appointment-create")
        response = self.client.post(
            url,
            {
                "doctor_id": self.doctor.id,
                "patient_id": self.patient.id,
                "start_time": self.first_start.isoformat(),
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Appointment.objects.count(), 1)
        self.assertEqual(response.data["status"], "booked")

    def test_book_appointment_rejects_taken_slot(self):
        Appointment.objects.create(
            doctor=self.doctor,
            patient=self.patient,
            start_time=self.first_start,
            end_time=self.first_end,
            status=Appointment.Status.BOOKED,
        )
        url = reverse("appointment-create")
        response = self.client.post(
            url,
            {
                "doctor_id": self.doctor.id,
                "patient_id": self.patient.id,
                "start_time": self.first_start.isoformat(),
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("error", response.data)

    def test_book_appointment_rejects_out_of_hours_slot(self):
        bad_time = self.first_start.replace(hour=20)
        url = reverse("appointment-create")
        response = self.client.post(
            url,
            {
                "doctor_id": self.doctor.id,
                "patient_id": self.patient.id,
                "start_time": bad_time.isoformat(),
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cancel_appointment(self):
        appt = Appointment.objects.create(
            doctor=self.doctor,
            patient=self.patient,
            start_time=self.first_start,
            end_time=self.first_end,
            status=Appointment.Status.BOOKED,
        )
        url = reverse("appointment-cancel", args=[appt.id])
        response = self.client.patch(url, {"reason": "Patient is unwell"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        appt.refresh_from_db()
        self.assertEqual(appt.status, Appointment.Status.CANCELLED)
        self.assertEqual(appt.cancellation_reason, "Patient is unwell")

    def test_cancel_already_cancelled_appointment_errors(self):
        appt = Appointment.objects.create(
            doctor=self.doctor,
            patient=self.patient,
            start_time=self.first_start,
            end_time=self.first_end,
            status=Appointment.Status.CANCELLED,
            cancellation_reason="Already cancelled",
        )
        url = reverse("appointment-cancel", args=[appt.id])
        response = self.client.patch(url, {"reason": "Trying again"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cancel_requires_reason(self):
        appt = Appointment.objects.create(
            doctor=self.doctor,
            patient=self.patient,
            start_time=self.first_start,
            end_time=self.first_end,
            status=Appointment.Status.BOOKED,
        )
        url = reverse("appointment-cancel", args=[appt.id])
        response = self.client.patch(url, {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reschedule_appointment_frees_old_slot(self):
        appt = Appointment.objects.create(
            doctor=self.doctor,
            patient=self.patient,
            start_time=self.first_start,
            end_time=self.first_end,
            status=Appointment.Status.BOOKED,
        )
        new_start, _ = get_slots_for_day(self.doctor, self.monday)[1]
        url = reverse("appointment-reschedule", args=[appt.id])
        response = self.client.patch(url, {"start_time": new_start.isoformat()}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        appt.refresh_from_db()
        self.assertEqual(appt.start_time, new_start)

        # Old slot should be available again.
        avail_url = reverse("doctor-availability", args=[self.doctor.id])
        avail_response = self.client.get(avail_url, {"date": self.monday.isoformat()})
        starts = [s["start_time"] for s in avail_response.data["available_slots"]]
        self.assertIn(self.first_start.isoformat(), starts)

    def test_reschedule_cancelled_appointment_errors(self):
        appt = Appointment.objects.create(
            doctor=self.doctor,
            patient=self.patient,
            start_time=self.first_start,
            end_time=self.first_end,
            status=Appointment.Status.CANCELLED,
            cancellation_reason="n/a",
        )
        new_start, _ = get_slots_for_day(self.doctor, self.monday)[1]
        url = reverse("appointment-reschedule", args=[appt.id])
        response = self.client.patch(url, {"start_time": new_start.isoformat()}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_patient_appointments_bonus_endpoint(self):
        Appointment.objects.create(
            doctor=self.doctor,
            patient=self.patient,
            start_time=self.first_start,
            end_time=self.first_end,
            status=Appointment.Status.BOOKED,
        )
        url = reverse("patient-appointments", args=[self.patient.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)
