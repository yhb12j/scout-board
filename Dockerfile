FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY tests_data ./tests_data

RUN mkdir -p /app/data

ENV PYTHONUNBUFFERED=1
ENV DATABASE_PATH=/app/data/scout.db

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
