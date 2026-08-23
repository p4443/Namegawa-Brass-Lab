FROM python:3.13-slim

WORKDIR /app

RUN apt-get update \
	&& apt-get install -y --no-install-recommends curl \
	&& rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py index.html back-navigation.js build_product.py ./
COPY healthcheck-prod.sh ./
COPY data ./data
COPY lesson ./lesson
COPY products ./products
COPY download-guide ./download-guide
COPY legal ./legal
COPY schedule ./schedule
COPY pdf ./pdf
COPY video ./video
COPY ["music App", "./music App"]
COPY trumpet-transpose-lab ./trumpet-transpose-lab
COPY contract-generator ./contract-generator

RUN python build_product.py

RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT:-8000} --workers 2 --worker-class gthread --threads 4 --timeout 120 --access-logfile - app:app"]
