# Tuesday's brain for a server you control: the seven open-weight minds run in the Ollama container next to it.
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 TUESDAY_HOST=0.0.0.0 TUESDAY_ROBOT=remote TUESDAY_DATA=/data
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && python -m playwright install --with-deps chromium
COPY tuesday ./tuesday
COPY train ./train
COPY deploy/entrypoint.sh ./entrypoint.sh
EXPOSE 4343
CMD ["sh", "entrypoint.sh"]
