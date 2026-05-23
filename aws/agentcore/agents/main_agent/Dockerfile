FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (layer-cached)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# AgentCore Runtime calls the container on port 8080
EXPOSE 8080

# Pass env vars at runtime via AgentCore environment config or AWS Secrets Manager
ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
