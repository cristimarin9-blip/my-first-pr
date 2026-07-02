FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY polybot/ polybot/
COPY main.py .

# Runtime state (paper portfolio, seen-trades cache, logs) lives here;
# mount a volume so it survives container restarts.
VOLUME /app/data

CMD ["python", "main.py"]
