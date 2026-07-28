from django.db import models
from django.conf import settings

# Create your models here.
class Doctor(models.Model):
    name = models.CharField(max_length=64)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class DoctorSchedule(models.Model):

    class Weekday(models.IntegerChoices):
        MONDAY = 0, "Monday"
        TUESDAY = 1, "Tuesday"
        WEDNESDAY = 2, "Wednesday"
        THURSDAY = 3, "Thursday"
        FRIDAY = 4, "Friday"
        SATURDAY = 5, "Saturday"
        SUNDAY = 6, "Sunday"


    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name="schedules")
    weekday = models.IntegerField(choices=Weekday.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()


    class Meta:
        ordering = ["weekday", "start_time"]
        constraints = [
            models.UniqueConstraint(
                fields=["doctor", "weekday"], name="unique_doctor_schedule_weekday"
            ),
            models.CheckConstraint(
            condition=models.Q(end_time__gt=models.F("start_time")),
            name= "schedule_end_after_start"
            )
        ]

    def __str__(self):
        return f"{self.doctor.name} - {self.get_weekday_display()} {self.start_time}-{self.end_time}"


class Patient(models.Model):
    name = models.CharField(max_length=200)
    email = models.EmailField()
    phone = models.CharField(max_length=30, blank=True)


    class Meta:
        ordering = ["name"]


    def __str__(self):
        return self.name


class Appointment(models.Model):
    """A booked (or cancelled) 30-minute slot with a doctor."""

    class Status(models.TextChoices):
        BOOKED = "booked", "Booked"
        CANCELLED = "cancelled", "Cancelled"

    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name="appointments")
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="appointments")

    start_time = models.DateTimeField()
    end_time = models.DateTimeField()

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.BOOKED)
    cancellation_reason = models.CharField(max_length=500, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["start_time"]
        indexes = [
            models.Index(fields=["doctor", "start_time"]),
            models.Index(fields=["patient", "start_time"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_time__gt=models.F("start_time")),
                name="appointment_end_after_start",
            ),
            # Belt-and-braces against double-booking: even if two requests
            # race past the application-level check, Postgres will reject
            # the second INSERT for the same doctor+slot while status is
            # still "booked". (Partial unique index - Postgres-specific.)
            models.UniqueConstraint(
                fields=["doctor", "start_time"],
                condition=models.Q(status="booked"),
                name="unique_booked_doctor_slot",
            ),
        ]

    def __str__(self):
        return f"{self.patient.name} with {self.doctor.name} @ {self.start_time} ({self.status})"
