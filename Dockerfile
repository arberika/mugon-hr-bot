FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Create logs directory
RUN mkdir -p /app/logs

# Run bot
CMD ["python", "bot.py"]
