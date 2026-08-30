FROM python:3.11-slim

WORKDIR /app

ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY apps /app/apps
COPY db /app/db
COPY scripts /app/scripts
COPY tests /app/tests
COPY pyproject.toml /app/pyproject.toml

EXPOSE 9000

CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "9000"]
