FROM python:3.11-slim

# Para que el python no genere .pyc y loguee bien
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencias del sistema para psycopg2
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Instalar dependencias de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código
COPY . .

# Puerto interno del contenedor
EXPOSE 8000

ENV FLASK_ENV=production

CMD ["python", "app.py"]
