FROM python:3.11-slim

WORKDIR /app

COPY sticker_server.py /app/sticker_server.py

ENV STICKER_SERVER_HOST=0.0.0.0
ENV STICKER_SERVER_PORT=8080

EXPOSE 8080

CMD ["python", "/app/sticker_server.py"]
