"""Publish curated CV knowledge and the browser's public endpoint configuration."""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import time

import boto3
from botocore.exceptions import ClientError


CONFIGURATION_PATH = Path("dist/assets/chat-config.json")
KNOWLEDGE_PATH = Path("knowledge/cv.txt")
KNOWLEDGE_OBJECT_KEY = "cv/cv.txt"
INGESTION_TIMEOUT_SECONDS = 1200
INGESTION_POLL_SECONDS = 10
TERMINAL_INGESTION_STATUSES = {"COMPLETE", "FAILED", "STOPPED"}
ENVIRONMENT_VARIABLE_NAMES = (
    "CV_BUCKET", "CV_KNOWLEDGE_BASE", "CV_DATA_SOURCE", "CHAT_API_URL",
)


@dataclass(frozen=True)
class KnowledgeConfiguration:
    bucket_name: str
    knowledge_base_id: str
    data_source_id: str
    chat_endpoint: str

    def ingestion_parameters(self):
        return {
            "knowledgeBaseId": self.knowledge_base_id,
            "dataSourceId": self.data_source_id,
        }


def read_knowledge_configuration():
    """Return no configuration for static builds; reject partially configured builds."""
    environment_values = [os.environ.get(name) for name in ENVIRONMENT_VARIABLE_NAMES]
    if all(environment_values):
        return KnowledgeConfiguration(*environment_values)
    if any(environment_values):
        raise RuntimeError("Incomplete chat build environment")
    return None


def write_public_configuration(chat_endpoint):
    CONFIGURATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIGURATION_PATH.write_text(
        json.dumps({"endpoint": chat_endpoint}), encoding="utf-8",
    )


def read_published_digest(storage_client, bucket_name):
    try:
        object_metadata = storage_client.head_object(
            Bucket=bucket_name, Key=KNOWLEDGE_OBJECT_KEY,
        )["Metadata"]
        return object_metadata.get("sha256")
    except ClientError as error:
        if error.response["Error"]["Code"] not in {"404", "NoSuchKey"}:
            raise
        return None


def upload_changed_knowledge(storage_client, bucket_name):
    knowledge_content = KNOWLEDGE_PATH.read_bytes()
    content_digest = hashlib.sha256(knowledge_content).hexdigest()
    if content_digest == read_published_digest(storage_client, bucket_name):
        return
    storage_client.put_object(
        Bucket=bucket_name,
        Key=KNOWLEDGE_OBJECT_KEY,
        Body=knowledge_content,
        ContentType="text/plain; charset=utf-8",
        Metadata={"sha256": content_digest},
    )


def require_time_remaining(deadline, error_message):
    if time.monotonic() > deadline:
        raise TimeoutError(error_message)


def start_ingestion_when_available(knowledge_client, ingestion_parameters, deadline):
    while True:
        try:
            return knowledge_client.start_ingestion_job(
                **ingestion_parameters,
            )["ingestionJob"]
        except knowledge_client.exceptions.ConflictException:
            require_time_remaining(deadline, "Another ingestion is still running")
            time.sleep(INGESTION_POLL_SECONDS)


def wait_for_ingestion(knowledge_client, ingestion_parameters, ingestion_job, deadline):
    while ingestion_job["status"] not in TERMINAL_INGESTION_STATUSES:
        require_time_remaining(deadline, "CV ingestion did not complete within 20 minutes")
        time.sleep(INGESTION_POLL_SECONDS)
        ingestion_job = knowledge_client.get_ingestion_job(
            **ingestion_parameters,
            ingestionJobId=ingestion_job["ingestionJobId"],
        )["ingestionJob"]
    return ingestion_job


def require_successful_ingestion(ingestion_job):
    failed_document_count = ingestion_job.get("statistics", {}).get("numberOfDocumentsFailed", 0)
    if ingestion_job["status"] != "COMPLETE" or failed_document_count:
        failure_details = ingestion_job.get("failureReasons", ingestion_job["status"])
        raise RuntimeError(f"CV ingestion failed: {failure_details}")


def synchronize_knowledge(knowledge_client, configuration):
    """Re-sync every release to recover failed ingestion and populate replacement knowledge bases."""
    ingestion_parameters = configuration.ingestion_parameters()
    deadline = time.monotonic() + INGESTION_TIMEOUT_SECONDS
    ingestion_job = start_ingestion_when_available(
        knowledge_client, ingestion_parameters, deadline,
    )
    completed_job = wait_for_ingestion(
        knowledge_client, ingestion_parameters, ingestion_job, deadline,
    )
    require_successful_ingestion(completed_job)


def publish():
    configuration = read_knowledge_configuration()
    if configuration is None:
        write_public_configuration(None)
        return
    storage_client = boto3.client("s3")
    knowledge_client = boto3.client("bedrock-agent")
    upload_changed_knowledge(storage_client, configuration.bucket_name)
    synchronize_knowledge(knowledge_client, configuration)
    write_public_configuration(configuration.chat_endpoint)


if __name__ == "__main__":
    publish()
