FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Bouwafhankelijkheden apart zodat de laag gecachet blijft.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# De hoster geeft de poort via $PORT door. Render gebruikt 10000 en scant de
# poort uit EXPOSE; die twee moeten gelijk zijn, anders wordt de draaiende app
# niet gevonden ("no open ports detected") terwijl uvicorn wel luistert.
ENV PORT=10000
EXPOSE 10000

# Migraties draaien bij het starten, daarna de app. `exec` maakt uvicorn het
# hoofdproces, zodat het stopsignaal van de hoster aankomt en de container
# netjes afsluit in plaats van hard te worden afgebroken.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
