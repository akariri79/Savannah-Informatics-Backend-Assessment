FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# psycopg2-binary needs libpq at runtime; build-essential is only needed
# transiently if a wheel isn't available for this platform.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Collect static files at build time (whitenoise serves them at runtime).
RUN DEBUG=False SECRET_KEY=build-time-placeholder python manage.py collectstatic --noinput

EXPOSE 8000


# On start: apply migrations, then boot gunicorn. Running migrate on every
# deploy keeps the DB schema in lockstep with the deployed code, which is
# fine for a project this size (a bigger system would run migrations as a
# separate release step instead of inside the web process).
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3"]




LABEL authors="Alvin Kariri"
LABEL version="1.0"
LABEL description="Clinic Booking API for Savannah Informatics Backend Assessment"
