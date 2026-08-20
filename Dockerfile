FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render/Koyeb/Cloud Run all inject $PORT at runtime; main.py already
# reads it via os.getenv("PORT", 10000).
ENV PORT=10000
EXPOSE 10000

CMD ["python", "main.py"]
