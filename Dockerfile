# NoteTutor AI - API + video pipeline
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
# Fonts for rendered video text (DejaVu) and icons; ffmpeg ships with imageio-ffmpeg.
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-dejavu-core fonts-noto-color-emoji libgomp1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY evaluation ./evaluation

# Voice models and generated data are mounted as volumes (see docker-compose.yml).
EXPOSE 8000
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
