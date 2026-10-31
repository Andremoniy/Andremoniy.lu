"""One SNS publication per conversation's first saved exchange, with retry deduplication.

DynamoDB Streams and SNS provide at-least-once delivery. A crash between a
successful Publish and recording SENT can still cause a duplicate email.
"""
from datetime import datetime, timezone
import json
import os
import time
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

config = Config(connect_timeout=2, read_timeout=3, retries={'total_max_attempts': 1})
database = boto3.client('dynamodb', config=config)
notifications = boto3.client('sns', config=config)


def value(item, field):
    return item.get(field, {}).get('S', '')


def is_first_interaction(record):
    if record.get('eventName') != 'MODIFY':
        return False
    data = record.get('dynamodb', {})
    old, new = data.get('OldImage', {}), data.get('NewImage', {})
    return (value(new, 'entryId') == 'summary' and
            value(new, 'recordId').startswith('session#') and
            'N' in new.get('firstInteractionAt', {}) and
            'firstInteractionAt' not in old)


def notification_message(image):
    now = int(image['firstInteractionAt']['N'])
    try:
        zone = ZoneInfo('Europe/Paris')
    except ZoneInfoNotFoundError:
        zone = timezone.utc
    when = datetime.fromtimestamp(now, zone).strftime('%d %B %Y, %H:%M:%S %Z')
    lines = [
        'A visitor has started a conversation on andremoniy.com.', '',
        'The following introduction and question are untrusted visitor-provided content.',
        'Name (self-declared): ' + value(image, 'name'),
        'Company (self-declared): ' + value(image, 'company'),
        'Access: ' + value(image, 'authStatus'),
        'First interaction: ' + when,
        'Conversation ID: ' + (value(image, 'conversationId') or value(image, 'recordId')),
    ]
    if value(image, 'authStatus') == 'authenticated' and value(image, 'email'):
        lines.append('Verified email: ' + value(image, 'email'))
    lines.extend(['', 'First question:', value(image, 'firstQuestion')[:1000]])
    return '\n'.join(lines)


def publish_once(record):
    image = record['dynamodb']['NewImage']
    record_id = 'notification#' + value(image, 'recordId')
    key = {'recordId': {'S': record_id}}
    table = os.environ['CHAT_STATE_TABLE']
    now = int(time.time())
    lease_owner = uuid.uuid4().hex
    message = notification_message(image)
    try:
        claimed = database.update_item(TableName=table, Key=key,
            UpdateExpression='SET #status = :publishing, leaseUntil = :lease, leaseOwner = :owner, expiresAt = :ttl, messageBody = if_not_exists(messageBody, :body)',
            ConditionExpression='attribute_not_exists(recordId) OR (#status <> :sent AND leaseUntil < :now)',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':publishing': {'S': 'PUBLISHING'}, ':sent': {'S': 'SENT'},
                ':lease': {'N': str(now + 30)}, ':now': {'N': str(now)},
                ':owner': {'S': lease_owner}, ':ttl': {'N': str(now + 30 * 86400)},
                ':body': {'S': message}}, ReturnValues='ALL_NEW')['Attributes']
    except ClientError as error:
        if error.response['Error']['Code'] != 'ConditionalCheckFailedException':
            raise
        existing = database.get_item(TableName=table, Key=key, ConsistentRead=True).get('Item', {})
        if value(existing, 'status') == 'SENT':
            return
        # Another worker owns a live lease. Do not acknowledge this event yet;
        # a retry can reclaim it if that worker dies before recording success.
        raise RuntimeError('Notification publication is already in progress') from error
    try:
        result = notifications.publish(TopicArn=os.environ['NOTIFICATION_TOPIC_ARN'],
            Subject='New portfolio chat session', Message=value(claimed, 'messageBody'))
    except Exception:
        database.update_item(TableName=table, Key=key,
            UpdateExpression='SET #status = :pending, leaseUntil = :zero',
            ConditionExpression='leaseOwner = :owner',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={':pending': {'S': 'PENDING'}, ':zero': {'N': '0'},
                                       ':owner': {'S': lease_owner}})
        raise
    database.update_item(TableName=table, Key=key,
        UpdateExpression='SET #status = :sent, publishedAt = :now, snsMessageId = :message REMOVE leaseUntil, leaseOwner',
        ConditionExpression='leaseOwner = :owner',
        ExpressionAttributeNames={'#status': 'status'},
        ExpressionAttributeValues={':sent': {'S': 'SENT'}, ':now': {'N': str(now)},
            ':message': {'S': result['MessageId']}, ':owner': {'S': lease_owner}})


def handler(event, context):
    failures = []
    for record in event.get('Records', []):
        if not is_first_interaction(record):
            continue
        sequence = record.get('dynamodb', {}).get('SequenceNumber')
        if not sequence:
            raise ValueError('Missing stream sequence number')
        try:
            publish_once(record)
        except Exception:
            failures.append({'itemIdentifier': sequence})
    if failures:
        # Never log the event, email body, recipient, question or credentials.
        print(json.dumps({'event': 'chat_notification_batch_failed', 'failedRecords': len(failures)}))
    return {'batchItemFailures': failures}
