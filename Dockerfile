FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py index.html ./
COPY data ./data
COPY lesson ./lesson
COPY schedule ./schedule
COPY pdf ./pdf
COPY video ./video

RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--access-logfile", "-", "app:app"]
