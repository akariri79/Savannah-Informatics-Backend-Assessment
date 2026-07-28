from django.shortcuts import render
from datetime import datetime

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Appointment, Doctor
from .serializers import (
    AppointmentCancelSerializer,
    AppointmentCreateSerializer,
    AppointmentRescheduleSerializer,
    AppointmentSerializer,
)
from .services import get_available_slots


class AppointmentCreateView(generics.CreateAPIView):
    """POST /appointments"""

    serializer_class = AppointmentCreateSerializer

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # select_for_update() locks the doctor row for the duration of the
        # transaction so two concurrent requests for the same slot can't
        # both pass validation before either commits. Combined with the
        # partial unique DB constraint, this closes the double-booking
        # race condition at both the app and DB layers.
        Doctor.objects.select_for_update().get(pk=serializer.validated_data["doctor"].pk)
        appointment = serializer.save()
        out = AppointmentSerializer(appointment)
        return Response(out.data, status=status.HTTP_201_CREATED)


class DoctorAvailabilityView(APIView):
    """GET /doctors/{id}/availability?date=YYYY-MM-DD"""

    def get(self, request, doctor_id):
        doctor = get_object_or_404(Doctor, pk=doctor_id, is_active=True)

        date_param = request.query_params.get("date")
        if not date_param:
            return Response(
                {"error": {"detail": "Query parameter 'date' (YYYY-MM-DD) is required.", "fields": None}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            date = datetime.strptime(date_param, "%Y-%m-%d").date()
        except ValueError:
            return Response(
                {"error": {"detail": "Invalid 'date' format. Use YYYY-MM-DD.", "fields": None}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        slots = get_available_slots(doctor, date)
        return Response(
            {
                "doctor_id": doctor.id,
                "date": date_param,
                "available_slots": [
                    {"start_time": start.isoformat(), "end_time": end.isoformat()}
                    for start, end in slots
                ],
            }
        )


class AppointmentCancelView(APIView):
    """PATCH /appointments/{id}/cancel"""

    @transaction.atomic
    def patch(self, request, appointment_id):
        appointment = get_object_or_404(
            Appointment.objects.select_for_update(), pk=appointment_id
        )

        if appointment.status == Appointment.Status.CANCELLED:
            return Response(
                {"error": {"detail": "This appointment is already cancelled.", "fields": None}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = AppointmentCancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        appointment.status = Appointment.Status.CANCELLED
        appointment.cancellation_reason = serializer.validated_data["reason"]
        appointment.save(update_fields=["status", "cancellation_reason", "updated_at"])

        return Response(AppointmentSerializer(appointment).data)


class AppointmentRescheduleView(APIView):
    """PATCH /appointments/{id}/reschedule"""

    @transaction.atomic
    def patch(self, request, appointment_id):
        appointment = get_object_or_404(
            Appointment.objects.select_for_update(), pk=appointment_id
        )

        if appointment.status == Appointment.Status.CANCELLED:
            return Response(
                {"error": {"detail": "Cannot reschedule a cancelled appointment.", "fields": None}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Lock the doctor row too, for the same race-prevention reason as
        # in AppointmentCreateView.
        Doctor.objects.select_for_update().get(pk=appointment.doctor_id)

        serializer = AppointmentRescheduleSerializer(instance=appointment, data=request.data)
        serializer.is_valid(raise_exception=True)

        appointment.start_time = serializer.validated_data["start_time"]
        appointment.end_time = serializer.validated_data["end_time"]
        appointment.save(update_fields=["start_time", "end_time", "updated_at"])

        return Response(AppointmentSerializer(appointment).data)


class PatientAppointmentsView(generics.ListAPIView):
    """GET /patients/{id}/appointments - bonus endpoint."""

    serializer_class = AppointmentSerializer

    def get_queryset(self):
        patient_id = self.kwargs["patient_id"]
        return (
            Appointment.objects.filter(patient_id=patient_id, status=Appointment.Status.BOOKED)
            .select_related("doctor", "patient")
            .order_by("start_time")
        )

# Create your views here.
