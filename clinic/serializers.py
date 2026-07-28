from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework import serializers

from .models import Appointment, Doctor, Patient
from .services import validate_slot_is_bookable


class DoctorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Doctor
        fields = ["id", "name", "is_active"]


class PatientSerializer(serializers.ModelSerializer):
    class Meta:
        model = Patient
        fields = ["id", "name", "email", "phone"]


class AppointmentSerializer(serializers.ModelSerializer):
    """Read serializer used for responses."""

    doctor = DoctorSerializer(read_only=True)
    patient = PatientSerializer(read_only=True)

    class Meta:
        model = Appointment
        fields = [
            "id",
            "doctor",
            "patient",
            "start_time",
            "end_time",
            "status",
            "cancellation_reason",
            "created_at",
            "updated_at",
        ]


class AppointmentCreateSerializer(serializers.Serializer):
    """
    Write serializer for POST /appointments.

    Accepts IDs for doctor/patient and a start_time; end_time is derived
    from the fixed slot duration rather than accepted from the client, so
    a patient can't submit a slot of the wrong length.
    """

    doctor_id = serializers.PrimaryKeyRelatedField(
        queryset=Doctor.objects.filter(is_active=True), source="doctor"
    )
    patient_id = serializers.PrimaryKeyRelatedField(
        queryset=Patient.objects.all(), source="patient"
    )
    start_time = serializers.DateTimeField()

    def validate(self, attrs):
        doctor = attrs["doctor"]
        start_time = attrs["start_time"]
        end_time = start_time + timedelta(minutes=settings.SLOT_DURATION_MINUTES)

        validate_slot_is_bookable(doctor=doctor, start_time=start_time, end_time=end_time)

        attrs["end_time"] = end_time
        return attrs

    def create(self, validated_data):
        return Appointment.objects.create(
            doctor=validated_data["doctor"],
            patient=validated_data["patient"],
            start_time=validated_data["start_time"],
            end_time=validated_data["end_time"],
            status=Appointment.Status.BOOKED,
        )


class AppointmentRescheduleSerializer(serializers.Serializer):
    """Write serializer for PATCH /appointments/{id}/reschedule."""

    start_time = serializers.DateTimeField()

    def validate(self, attrs):
        appointment = self.instance
        start_time = attrs["start_time"]
        end_time = start_time + timedelta(minutes=settings.SLOT_DURATION_MINUTES)

        validate_slot_is_bookable(
            doctor=appointment.doctor,
            start_time=start_time,
            end_time=end_time,
            exclude_appointment_id=appointment.id,
        )

        attrs["end_time"] = end_time
        return attrs


class AppointmentCancelSerializer(serializers.Serializer):
    """Write serializer for PATCH /appointments/{id}/cancel."""

    reason = serializers.CharField(max_length=500, required=True, allow_blank=False)
