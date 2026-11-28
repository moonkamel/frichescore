FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FRICHESCORE_DB=/data/frichescore.db TZ=Europe/Paris
# Police DejaVu pour des PDF avec tous les caractères français
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core tzdata && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY frontend ./frontend
COPY site ./site
COPY tests ./tests
VOLUME ["/data"]
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
