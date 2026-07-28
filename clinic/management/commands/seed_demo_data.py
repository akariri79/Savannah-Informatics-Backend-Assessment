from django.core.management.base import BaseCommand

from clinic.models import Doctor, DoctorSchedule, Patient


class Command(BaseCommand):
    help = "Seeds the database with 5 doctors (Mon-Fri, 09:00-17:00) and a demo patient."

    def handle(self, *args, **options):
        doctor_names = [
            "Dr. Amara Okafor",
            "Dr. Wanjiru Kamau",
            "Dr. Otieno Owuor",
            "Dr. Fatima Hassan",
            "Dr. Brian Mwangi",
        ]

        for name in doctor_names:
            doctor, created = Doctor.objects.get_or_create(name=name)
            if created:
                for weekday in range(0, 5):  # Monday-Friday
                    DoctorSchedule.objects.create(
                        doctor=doctor,
                        weekday=weekday,
                        start_time="09:00",
                        end_time="17:00",
                    )
                self.stdout.write(self.style.SUCCESS(f"Created {name}"))
            else:
                self.stdout.write(f"{name} already exists, skipping.")

        patient, created = Patient.objects.get_or_create(
            email="demo.patient@example.com",
            defaults={"name": "Demo Patient", "phone": "+254700000000"},
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f"Created demo patient: {patient.name} (id={patient.id})"))
        else:
            self.stdout.write(f"Demo patient already exists (id={patient.id}).")

        self.stdout.write(self.style.SUCCESS("Seeding complete."))
