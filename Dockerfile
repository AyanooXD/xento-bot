FROM mcr.microsoft.com/playwright/python:v1.45.0-noble

WORKDIR /app

# Copy requirements and install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all bot code
COPY . .

# Create data directory
RUN mkdir -p /app/data /app/proofs

# Start the bot
CMD ["python3", "bot.py"]
