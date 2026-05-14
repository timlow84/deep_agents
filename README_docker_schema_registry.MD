# Docker Schema Registry Setup

This document explains how to run Confluent Schema Registry in Docker and connect it from the VS Code Confluent extension.

## 1. Prerequisites

- Docker is installed and running
- A Kafka broker is already running in Docker
- The Kafka broker in this workspace is available at `localhost:50511` on the host

## 2. Start Schema Registry in Docker

Use the following command to start Schema Registry and connect it to the existing Kafka broker:

```powershell
docker run -d --name schema-registry \
  --network vscode-confluent-local-network \
  -p 8081:8081 \
  -e SCHEMA_REGISTRY_HOST_NAME=schema-registry \
  -e SCHEMA_REGISTRY_LISTENERS=http://0.0.0.0:8081 \
  -e SCHEMA_REGISTRY_KAFKASTORE_BOOTSTRAP_SERVERS=PLAINTEXT://vscode-confluent-local-broker-1:50512 \
  confluentinc/cp-schema-registry:7.9.0
```

### Notes

- `--network vscode-confluent-local-network` is required so Schema Registry can resolve the Kafka broker container by name.
- `SCHEMA_REGISTRY_KAFKASTORE_BOOTSTRAP_SERVERS` must point to the Kafka broker's internal Docker listener, not `9092`.
- The broker inside this setup is reachable from the Schema Registry container as `vscode-confluent-local-broker-1:50512`.

## 3. Verify Schema Registry is running

Run:

```powershell
docker ps --filter "name=schema-registry"
```

You should see a container in `Up` status with `0.0.0.0:8081->8081/tcp`.

Then open in the browser:

```text
http://localhost:8081
```

If the service is running, the endpoint should respond.

## 4. VS Code Confluent extension setup

In VS Code, open the Confluent extension and create a new connection.

### General
- Connection Name: `docker_schema_registry`
- Connection Type: `Apache Kafka®`

### Kafka Cluster
- Bootstrap Server(s): `localhost:50511`
- Authentication Type: `None`
- SSL/TLS enabled: unchecked

### Schema Registry
- URL: `http://localhost:8081`
- Authentication Type: `None`
- SSL/TLS enabled: unchecked

## 5. Troubleshooting

### Schema Registry container fails to start

- Confirm the Kafka broker container is running.
- Confirm the broker is reachable at `vscode-confluent-local-broker-1:50512` from the same Docker network.
- Confirm the schema registry container is on `vscode-confluent-local-network`.

### Schema Registry is running but VS Code cannot connect

- Ensure `http://localhost:8081` is accessible from your host browser.
- Verify port `8081` is not blocked by another process.
- Confirm the Confluent extension `Schema Registry` URL is exactly `http://localhost:8081`.

## 6. Clean up

To stop and remove the schema registry container:

```powershell
docker rm -f schema-registry
```
