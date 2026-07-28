from django.contrib import admin

# Register your models here.
from .models import Appointment, Doctor, DoctorSchedule, Patient


class DoctorScheduleInline(admin.TabularInline):
    model = DoctorSchedule
    extra = 1


@admin.register(Doctor)
class DoctorAdmin(admin.ModelAdmin):
    list_display = ["name", "is_active"]
    inlines = [DoctorScheduleInline]


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ["name", "email", "phone"]


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ["doctor", "patient", "start_time", "status"]
    list_filter = ["status", "doctor"]
