"""Kafka REST Proxy client tool for agents.

Provides:
  - KafkaRestConsumer  : async context manager managing a Confluent REST Proxy consumer
  - run_kafka_listener : reusable background-task helper (use with asyncio.create_task)
  - listen_kafka_topic : LangChain @tool for one-shot polling by agents

Typical background-listener usage in an A2A agent:

    from tools.kafka.kafka_client import run_kafka_listener

    async def my_handler(message: dict) -> None:
        print(message)

    asyncio.create_task(run_kafka_listener(
        server_url="http://localhost",
        port=8082,
        topic="my-topic",
        on_message=my_handler,
    ))
"""

import asyncio
import json
import logging
import os
import uuid
from collections.abc import Awaitable, Callable

import httpx
from langchain_core.tools import tool

log = logging.getLogger(__name__)

_KAFKA_V2 = "application/vnd.kafka.v2+json"
_KAFKA_JSON_V2 = "application/vnd.kafka.json.v2+json"


class KafkaRestConsumer:
    """Manages a Confluent REST Proxy v2 consumer lifecycle.

    Creates a transient consumer group on entry and deletes it on exit, so each
    instance starts from the latest offset and leaves no server-side state behind.

    REST Proxy endpoints used:
        POST   /consumers/{group}
        POST   /consumers/{group}/instances/{id}/subscription
        POST   /consumers/{group}/instances/{id}/positions          (seek)
        GET    /topics/{topic}/partitions                           (partition discovery)
        GET    /consumers/{group}/instances/{id}/records
        DELETE /consumers/{group}/instances/{id}
    """

    def __init__(
        self,
        server_url: str,
        port: int,
        topic: str,
        group_id: str | None = None,
        seek_offset: int | None = None,
    ) -> None:
        self.base_url = f"{server_url.rstrip('/')}:{port}"
        self.topic = topic
        self.group_id = group_id or f"agent-{uuid.uuid4().hex[:8]}"
        self._instance_id = f"inst-{uuid.uuid4().hex[:8]}"
        self._consumer_base_url: str | None = None
        self._client: httpx.AsyncClient | None = None
        self._seek_offset = seek_offset

    async def __aenter__(self) -> KafkaRestConsumer:
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        await self._create_consumer()
        await self._subscribe()
        if self._seek_offset is not None:
            await self._seek(self._seek_offset)
        return self

    async def __aexit__(self, *_) -> None:
        if self._consumer_base_url and self._client:
            try:
                await self._client.delete(
                    self._consumer_base_url,
                    headers={"Content-Type": _KAFKA_V2},
                )
                log.debug("Kafka consumer deleted: %s", self._consumer_base_url)
            except Exception:
                pass
        if self._client:
            await self._client.aclose()

    async def _create_consumer(self) -> None:
        assert self._client is not None
        resp = await self._client.post(
            f"{self.base_url}/consumers/{self.group_id}",
            json={
                "name": self._instance_id,
                "format": "json",
                # "auto.offset.reset": "latest". This means each new consumer instance starts from the end of the topic, so it only receives new messages.
                #  Changed to "earliest" to start from the beginning of the topic instead.
                "auto.offset.reset": "earliest",
            },
            headers={"Content-Type": _KAFKA_V2},
        )
        if not resp.is_success:
            log.error(
                "Kafka consumer creation failed [%s] for group=%r: %s",
                resp.status_code,
                self.group_id,
                resp.text,
            )
        resp.raise_for_status()
        raw_uri = resp.json()["base_uri"]
        # The REST Proxy runs inside Docker and advertises its container-internal
        # hostname in base_uri (e.g. "http://rest-proxy:8082/...").  Rewrite it
        # to the externally-reachable base URL so subsequent requests work from
        # outside the Docker network.
        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(raw_uri)
        reachable = urlparse(self.base_url)
        self._consumer_base_url = urlunparse(
            parsed._replace(
                scheme=reachable.scheme,
                netloc=reachable.netloc,
            )
        )
        if self._consumer_base_url != raw_uri:
            log.debug("Rewrote consumer base_uri %s -> %s", raw_uri, self._consumer_base_url)
        log.debug("Kafka consumer created: %s", self._consumer_base_url)

    async def _subscribe(self) -> None:
        assert self._client is not None
        assert self._consumer_base_url is not None
        resp = await self._client.post(
            f"{self._consumer_base_url}/subscription",
            json={"topics": [self.topic]},
            headers={"Content-Type": _KAFKA_V2},
        )
        resp.raise_for_status()
        log.debug("Subscribed to Kafka topic: %s", self.topic)

    async def _seek(self, offset: int) -> None:
        """Seek all partitions of the topic to *offset* via the REST Proxy positions API.

        Partition IDs are discovered by GET /topics/{topic}/partitions so this works
        regardless of how many partitions the topic has.

        The REST Proxy assigns partitions lazily — a dummy poll is required first to
        trigger assignment, otherwise POST /positions returns 409 Conflict.
        """
        assert self._client is not None
        assert self._consumer_base_url is not None

        # Trigger lazy partition assignment before seeking. This ia a known issue in Kafka.
        await self._client.get(
            f"{self._consumer_base_url}/records",
            params={"max_bytes": 1},
            headers={"Accept": _KAFKA_JSON_V2},
        )

        # Discover partition IDs for this topic.
        resp = await self._client.get(
            f"{self.base_url}/topics/{self.topic}/partitions",
            headers={"Accept": _KAFKA_V2},
        )
        resp.raise_for_status()
        partition_ids = [p["partition"] for p in resp.json()]

        offsets = [
            {"topic": self.topic, "partition": pid, "offset": offset} for pid in partition_ids
        ]
        # Retry on 409: partition assignment may not be visible to /positions
        # immediately after the dummy poll even though the poll triggered it.
        for _attempt in range(5):
            resp = await self._client.post(
                f"{self._consumer_base_url}/positions",
                json={"offsets": offsets},
                headers={"Content-Type": _KAFKA_V2},
            )
            if resp.status_code == 409 and _attempt < 4:
                await asyncio.sleep(1.0)
                continue
            break
        if resp.status_code == 409:
            # Stale instance from a previous crashed run may hold the partition
            # assignment, preventing seek. Non-fatal: auto.offset.reset="earliest"
            # already handles the common case; warn and let polling proceed.
            log.warning(
                "Seek to offset %d failed (409 after retries) — "
                "falling back to auto.offset.reset for topic=%s",
                offset,
                self.topic,
            )
            return
        resp.raise_for_status()
        log.info(
            "Seeked topic=%s partitions=%s to offset=%d",
            self.topic,
            partition_ids,
            offset,
        )

    async def poll(self, max_bytes: int = 65536) -> list[dict]:
        """Poll once and return a list of parsed message payload dicts."""
        assert self._client is not None
        assert self._consumer_base_url is not None
        resp = await self._client.get(
            f"{self._consumer_base_url}/records",
            params={"max_bytes": max_bytes},
            headers={"Accept": _KAFKA_JSON_V2},
        )
        resp.raise_for_status()
        messages: list[dict] = []
        for record in resp.json():
            value = record.get("value")
            if value is None:
                continue
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    continue
            messages.append(value)
        return messages


async def run_kafka_listener(
    server_url: str,
    port: int,
    topic: str,
    on_message: Callable[[dict], Awaitable[None]],
    poll_interval: float = 2.0,
    group_id: str | None = None,
    seek_offset: int | None = None,
) -> None:
    """Continuously poll a Kafka REST Proxy topic and call *on_message* for each record.

    Designed to run as an asyncio background Task:

        asyncio.create_task(run_kafka_listener(
            server_url=_KAFKA_URL,
            port=_KAFKA_PORT,
            topic=_KAFKA_TOPIC,
            on_message=my_handler,
            group_id="my-consumer-group",
            seek_offset=0,   # optional: replay from a specific offset
        ))

    The consumer is recreated automatically after poll errors.  Cancel the
    enclosing Task to stop the listener cleanly.

    Args:
        server_url:    Kafka REST Proxy base URL (e.g. "http://localhost")
        port:          REST Proxy port (e.g. 8082)
        topic:         Kafka topic name to subscribe to
        on_message:    Async callback receiving each parsed message dict
        poll_interval: Seconds between poll requests (default 2.0)
        group_id:      Consumer group ID; random UUID used when omitted
        seek_offset:   If set, seek all partitions to this offset before polling;
                       overrides auto.offset.reset for this session only
    """
    log.info(
        "Starting Kafka listener: %s:%d topic=%s (poll every %.1fs)",
        server_url,
        port,
        topic,
        poll_interval,
    )
    base_backoff = float(os.getenv("KAFKA_CLIENT_BACKOFF_INTERVAL_SEC", "10"))
    attempt = 0
    retry_interval = base_backoff
    while True:
        try:
            async with KafkaRestConsumer(
                server_url, port, topic, group_id=group_id, seek_offset=seek_offset
            ) as consumer:
                # Reset backoff state on successful connection
                attempt = 0
                retry_interval = base_backoff
                log.debug("Kafka consumer ready, polling topic: %s", topic)
                while True:
                    try:
                        messages = await consumer.poll()
                        for msg in messages:
                            try:
                                await on_message(msg)
                            except asyncio.CancelledError:
                                raise
                            except Exception:
                                log.exception("Error handling Kafka message")
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        log.exception("Kafka poll error — recreating consumer")
                        break  # drop to outer loop to recreate consumer
                    await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:
            log.info("Kafka listener stopped (task cancelled)")
            raise
        except Exception as exc:
            attempt += 1
            # Double the interval before each attempt after the first (exponential back-off)
            if attempt >= 2:
                retry_interval = retry_interval * 2
            log.error(
                "Kafka consumer setup failed — retrying in %.0f s, back-off attempt #%d: %s",
                retry_interval,
                attempt,
                exc,
            )
            # Countdown to next retry in 10-second ticks so the agent appears alive
            _countdown_interval = 10.0
            remaining = retry_interval
            while remaining > _countdown_interval:
                remaining -= _countdown_interval
                await asyncio.sleep(_countdown_interval)
                log.info(
                    "Kafka consumer reconnecting in %.0f s...",
                    remaining,
                )


@tool
async def listen_kafka_topic(server_url: str, port: int, topic: str) -> str:
    """Poll a Kafka REST Proxy topic once and return pending messages as a JSON array.

    For continuous background listening, use run_kafka_listener instead.

    Args:
        server_url: Kafka REST Proxy base URL (e.g. "http://localhost")
        port:       REST Proxy port (e.g. 8082)
        topic:      Kafka topic name

    Returns a JSON array of message payload objects (may be empty if no new messages).
    """
    async with KafkaRestConsumer(server_url, port, topic) as consumer:
        messages = await consumer.poll()
    return json.dumps(messages)
