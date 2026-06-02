FROM python:3.12-alpine

RUN apk add --no-cache font-dejavu \
  && pip install --no-cache-dir reportlab==4.2.5

WORKDIR /app
CMD ["python", "/app/server.py"]
