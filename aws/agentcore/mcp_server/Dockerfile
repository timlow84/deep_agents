FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (layer-cached separately from source)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# AgentCore Runtime calls the container on port 8080
EXPOSE 8080

ENV PYTHONUNBUFFERED=1

# Required at runtime (set via AgentCore environment config or AWS Secrets Manager):
#   API_BASE_URL  — Root URL of the REST API this server fronts
#   API_KEY       — Optional bearer token for the API
CMD ["python", "server.py"]
