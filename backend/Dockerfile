FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Bouwafhankelijkheden apart zodat de laag gecachet blijft.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# App Service geeft de poort via $PORT door; lokaal is 8000 de standaard.
ENV PORT=8000
EXPOSE 8000

# Migraties draaien bij het starten, daarna de app.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
