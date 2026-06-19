FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd -m appuser
COPY --chown=appuser:appuser . .
USER appuser

EXPOSE 8000

CMD ["sh", "-c", "python migrate.py && exec gunicorn -w 2 -b 0.0.0.0:8000 --timeout 60 --access-logfile - --error-logfile - --capture-output wsgi:app"]
