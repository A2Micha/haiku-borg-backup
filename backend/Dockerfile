FROM python:3.12-slim-trixie
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends borgbackup openssh-client ca-certificates tzdata \
    && mkdir -p /data /repos /restore \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
ENV DATABASE_URL=sqlite:////data/borg-manager.db RESTORE_ROOT=/restore TZ=Europe/Berlin
EXPOSE 8000
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000"]
