"""Offline stream notification, retry and first-interaction integration checks."""
import copy
import json
import os
from pathlib import Path
import time
import types
import unittest
from unittest.mock import MagicMock, patch

import boto3
from botocore.exceptions import ClientError
from moto import mock_aws

from test_chat import CLOUDFORMATION_TEMPLATE


class NotificationTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {'AWS_DEFAULT_REGION': 'eu-west-1',
            'CHAT_STATE_TABLE': 'state', 'CHAT_HISTORY_TABLE': 'history',
            'NOTIFICATION_TOPIC_ARN': 'arn:aws:sns:eu-west-1:123456789012:notifications'})
        env.start(); self.addCleanup(env.stop)
        aws = mock_aws(); aws.start(); self.addCleanup(aws.stop)
        self.db = boto3.client('dynamodb')
        for table, keys in [('state', ['recordId']), ('history', ['recordId', 'entryId'])]:
            self.db.create_table(TableName=table, BillingMode='PAY_PER_REQUEST',
                AttributeDefinitions=[{'AttributeName': key, 'AttributeType': 'S'} for key in keys],
                KeySchema=[{'AttributeName': key, 'KeyType': 'HASH' if i == 0 else 'RANGE'} for i, key in enumerate(keys)])
        self.sns = MagicMock()
        self.sns.publish.return_value = {'MessageId': 'message-1'}
        self.notifier = self.load('ChatNotificationFunction', [self.db, self.sns])
        self.bedrock = MagicMock()
        self.chat = self.load('ChatFunction', [self.bedrock, self.db])
        self.identity = {'visitorId': 'visitor', 'name': 'Alice', 'company': 'Example Ltd',
                         'authStatus': 'guest', 'subject': '', 'email': ''}
        self.conversation = 'conversation-123'
        self.db.put_item(TableName='history', Item={**self.chat.history_key(self.conversation),
            'conversationId': {'S': self.conversation}, 'owner': {'S': 'visitor'},
            **{field: {'S': self.identity[field]} for field in ('name', 'company', 'authStatus')},
            'startedAt': {'N': '1000000'}, 'endsAt': {'N': '9999999999'}, 'endReason': {'S': 'inactivity'}})

    def load(self, resource, clients):
        module = types.ModuleType(resource)
        code = CLOUDFORMATION_TEMPLATE['Resources'][resource]['Properties']['Code']['ZipFile']
        with patch('boto3.client', side_effect=clients):
            exec(compile(code, resource, 'exec'), module.__dict__)
        return module

    def summary(self):
        return self.db.get_item(TableName='history', Key=self.chat.history_key(self.conversation),
                                 ConsistentRead=True)['Item']

    def exchange(self, question='Kubernetes?', now=1000001):
        old = self.summary()
        with patch.object(self.chat.time, 'time', return_value=now):
            self.chat.remember_conversation('visitor', self.conversation, question, 'Answer',
                self.identity.get('email', ''), self.identity, 'bedrock-123')
        return self.event(old, self.summary())

    def event(self, old, new, sequence='123'):
        return {'eventName': 'MODIFY', 'eventID': 'event-' + sequence,
            'dynamodb': {'SequenceNumber': sequence, 'OldImage': old, 'NewImage': new}}

    def deliver(self, *records):
        return self.notifier.handler({'Records': list(records)}, None)

    def row(self):
        return self.db.get_item(TableName='state', Key={
            'recordId': {'S': 'notification#' + self.summary()['recordId']['S']}}).get('Item', {})

    def test_first_saved_exchange_is_atomic_and_sets_marker_once(self):
        first = self.exchange()
        second = self.exchange('Docker?', now=1000030)
        summary = self.summary()
        self.assertEqual('1000001', summary['firstInteractionAt']['N'])
        self.assertEqual('Kubernetes?', summary['firstQuestion']['S'])
        self.assertEqual('1000030', summary['lastActivityAt']['N'])
        self.assertTrue(self.notifier.is_first_interaction(first))
        self.assertFalse(self.notifier.is_first_interaction(second))
        self.assertEqual(3, self.db.scan(TableName='history')['Count'])

    def test_creation_later_messages_upgrade_close_and_removal_do_not_notify(self):
        summary = self.summary()
        self.deliver({'eventName': 'INSERT', 'dynamodb': {'NewImage': summary}},
            self.event(summary, summary), {'eventName': 'REMOVE', 'dynamodb': {'OldImage': summary}})
        first = self.exchange()
        self.deliver(first)
        second = self.exchange('Docker?')
        upgraded = copy.deepcopy(self.summary())
        upgraded['authStatus'] = {'S': 'authenticated'}
        upgraded['email'] = {'S': 'alice@example.com'}
        self.deliver(second, self.event(self.summary(), upgraded))
        closed = {**upgraded, 'closedAt': {'N': '1000090'}}
        self.deliver(self.event(upgraded, closed))
        self.sns.publish.assert_called_once()

    def test_email_contains_only_expected_metadata_and_first_question(self):
        record = self.exchange()
        record['dynamodb']['NewImage']['sessionToken'] = {'S': 'never-email-token'}
        self.deliver(record)
        args = self.sns.publish.call_args.kwargs
        self.assertEqual(os.environ['NOTIFICATION_TOPIC_ARN'], args['TopicArn'])
        self.assertEqual('New portfolio chat session', args['Subject'])
        body = args['Message']
        for text in ('Alice', 'Example Ltd', 'self-declared', 'Guest', 'Kubernetes?', self.conversation):
            self.assertIn(text.lower(), body.lower())
        self.assertNotIn('Verified email:', body)
        self.assertNotIn('never-email-token', body)
        self.assertNotIn('bedrock-123', body)
        self.assertEqual('SENT', self.row()['status']['S'])
        self.assertGreater(int(self.row()['expiresAt']['N']), time.time() + 29 * 86400)

    def test_verified_email_is_included_only_when_already_authenticated(self):
        self.identity.update({'authStatus': 'authenticated', 'email': 'alice@example.com', 'subject': 'cognito-sub'})
        self.deliver(self.exchange())
        self.assertIn('Verified email: alice@example.com', self.sns.publish.call_args.kwargs['Message'])

    def test_duplicate_stream_delivery_does_not_republish(self):
        event = self.exchange()
        for _ in range(3): self.assertEqual({'batchItemFailures': []}, self.deliver(event))
        self.sns.publish.assert_called_once()

    def test_publish_failure_releases_lease_and_retries_without_changing_snapshot(self):
        event = self.exchange()
        self.sns.publish.side_effect = ClientError({'Error': {'Code': 'InternalError', 'Message': 'unavailable'}}, 'Publish')
        self.assertEqual({'batchItemFailures': [{'itemIdentifier': '123'}]}, self.deliver(event))
        self.assertEqual('PENDING', self.row()['status']['S'])
        snapshot = self.row()['messageBody']['S']
        self.sns.publish.side_effect = None
        self.assertEqual({'batchItemFailures': []}, self.deliver(event))
        self.assertEqual('SENT', self.row()['status']['S'])
        self.assertEqual(snapshot, self.sns.publish.call_args.kwargs['Message'])

    def test_active_lease_defers_retry_then_abandoned_lease_can_be_reclaimed(self):
        event = self.exchange()
        key = {'recordId': {'S': 'notification#' + self.summary()['recordId']['S']}}
        self.db.put_item(TableName='state', Item={**key, 'status': {'S': 'PUBLISHING'},
            'leaseUntil': {'N': str(int(time.time()) + 30)}, 'leaseOwner': {'S': 'dead-worker'}})
        self.assertEqual({'batchItemFailures': [{'itemIdentifier': '123'}]}, self.deliver(event))
        self.sns.publish.assert_not_called()
        self.db.update_item(TableName='state', Key=key, UpdateExpression='SET leaseUntil = :zero',
            ExpressionAttributeValues={':zero': {'N': '0'}})
        self.assertEqual({'batchItemFailures': []}, self.deliver(event))
        self.sns.publish.assert_called_once()

    def test_partial_batch_failures_return_only_failed_sequence(self):
        good = self.exchange()
        bad = copy.deepcopy(good)
        bad['dynamodb']['SequenceNumber'] = '124'
        bad['dynamodb']['NewImage']['recordId'] = {'S': 'session#different'}
        self.sns.publish.side_effect = [{'MessageId': 'message-1'}, RuntimeError('provider unavailable')]
        self.assertEqual({'batchItemFailures': [{'itemIdentifier': '124'}]}, self.deliver(good, bad))

    def test_failures_do_not_log_personal_data_or_question(self):
        self.sns.publish.side_effect = RuntimeError('alice@example.com private question secret')
        with patch('builtins.print') as printed:
            self.deliver(self.exchange())
        log = str(printed.call_args_list)
        for private in ('Alice', 'alice@example.com', 'Kubernetes', 'secret', 'Example Ltd'):
            self.assertNotIn(private, log)

    def test_first_question_marker_cannot_be_partially_saved_when_exchange_write_fails(self):
        with patch.object(self.db, 'transact_write_items', side_effect=ClientError(
                {'Error': {'Code': 'TransactionCanceledException'}}, 'TransactWriteItems')):
            with self.assertRaises(ClientError): self.exchange()
        self.assertNotIn('firstInteractionAt', self.summary())
        self.sns.publish.assert_not_called()

    def test_templates_declare_filtered_async_pipeline_and_scoped_permissions(self):
        resources = CLOUDFORMATION_TEMPLATE['Resources']
        mapping = resources['ChatNotificationStreamMapping']['Properties']
        self.assertEqual('TRIM_HORIZON', mapping['StartingPosition'])
        self.assertEqual(['ReportBatchItemFailures'], mapping['FunctionResponseTypes'])
        self.assertEqual(10, mapping['MaximumRetryAttempts'])
        self.assertEqual(3600, mapping['MaximumRecordAgeInSeconds'])
        pattern = json.loads(mapping['FilterCriteria']['Filters'][0]['Pattern'])
        self.assertEqual(['MODIFY'], pattern['eventName'])
        self.assertEqual([{'exists': False}], pattern['dynamodb']['OldImage']['firstInteractionAt']['N'])
        self.assertEqual('email', resources['ChatNotificationSubscription']['Properties']['Protocol'])
        self.assertEqual('Retain', resources['ChatHistoryTable']['DeletionPolicy'])
        self.assertNotIn('sns:Publish', str(resources['ChatRole']))
        self.assertNotIn('bedrock:', str(resources['ChatNotificationRole']))
        self.assertEqual('EnableChatNotifications', resources['ChatNotificationStreamMapping']['Condition'])
        source = (Path(__file__).resolve().parents[1] / 'aws/notification_handler.py').read_text(encoding='utf-8')
        self.assertEqual(source.strip(), resources['ChatNotificationFunction']['Properties']['Code']['ZipFile'].strip())


if __name__ == '__main__':
    unittest.main()
