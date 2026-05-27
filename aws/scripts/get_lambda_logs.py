"""Fetch the most recent Lambda log events directly."""
import boto3
import time

logs = boto3.client("logs", region_name="ap-southeast-1")

log_group = "/aws/lambda/lta-datamall-api-interceptor"

# Get the most recent log stream
streams = logs.describe_log_streams(
    logGroupName=log_group,
    orderBy="LastEventTime",
    descending=True,
    limit=3,
)
for stream in streams["logStreams"]:
    print(f"\n=== Stream: {stream['logStreamName']} ===")
    events = logs.get_log_events(
        logGroupName=log_group,
        logStreamName=stream["logStreamName"],
        startFromHead=True,
    )
    for ev in events["events"]:
        print(ev["message"].rstrip())
