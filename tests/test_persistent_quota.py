"""Quota rollover, migration and transaction checks using moto[dynamodb]."""

from datetime import datetime, timezone
import os
import types
import unittest
from unittest.mock import MagicMock, patch

import boto3

from test_chat import CLOUDFORMATION_TEMPLATE

try:
    from moto import mock_aws
except ImportError:
    mock_aws = None


@unittest.skipUnless(mock_aws, 'Install moto[dynamodb] to run DynamoDB integration checks')
class PersistentQuotaTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {
            'AWS_DEFAULT_REGION': 'eu-west-1',
            'CHAT_STATE_TABLE': 'chat-state', 'CHAT_HISTORY_TABLE': 'chat-history',
            'QUESTIONS_PER_MINUTE': '2', 'QUESTIONS_PER_DAY': '3',
            'GLOBAL_QUESTIONS_PER_DAY': '5', 'GLOBAL_QUESTIONS_PER_MONTH': '7'})
        environment.start()
        self.addCleanup(environment.stop)
        backend = mock_aws()
        backend.start()
        self.addCleanup(backend.stop)
        self.database = boto3.client('dynamodb', region_name='eu-west-1')
        for name, keys in [('chat-state', ['recordId']), ('chat-history', ['recordId', 'entryId'])]:
            self.database.create_table(
                TableName=name, BillingMode='PAY_PER_REQUEST',
                AttributeDefinitions=[{'AttributeName': key, 'AttributeType': 'S'} for key in keys],
                KeySchema=[{'AttributeName': key, 'KeyType': 'HASH' if index == 0 else 'RANGE'}
                           for index, key in enumerate(keys)])
        self.handler = types.ModuleType('persistent_quota_handler')
        source = CLOUDFORMATION_TEMPLATE['Resources']['ChatFunction']['Properties']['Code']['ZipFile']
        with patch('boto3.client', side_effect=[MagicMock(), self.database]):
            exec(compile(source, 'persistent-quota-handler', 'exec'), self.handler.__dict__)

    def consume(self, timestamp, owner='alice'):
        with patch.object(self.handler.time, 'time', return_value=timestamp):
            self.handler.consume_chat_quota(owner, owner + '@example.com', owner)

    def row(self, owner):
        return self.database.get_item(TableName='chat-state',
            Key={'recordId': {'S': 'quota#' + owner}}, ConsistentRead=True)['Item']

    def test_one_user_row_is_reused_when_minute_and_day_roll_over(self):
        self.consume(86400)
        self.consume(86401)
        with self.assertRaises(self.handler.QuotaExceededError) as failure:
            self.consume(86402)
        self.assertEqual(58, failure.exception.retry_after)
        self.assertEqual('2', self.row('alice')['dayRequestCount']['N'])
        self.consume(86460)
        self.assertEqual('1', self.row('alice')['minuteRequestCount']['N'])
        self.assertEqual('3', self.row('alice')['dayRequestCount']['N'])
        with self.assertRaises(self.handler.QuotaExceededError) as failure:
            self.consume(86461)
        self.assertEqual(172800 - 86461, failure.exception.retry_after)
        self.consume(172800)
        user = self.row('alice')
        self.assertEqual('1', user['dayRequestCount']['N'])
        self.assertEqual('1970-01-03T00:00:00Z', user['dayPeriodStart']['S'])
        self.assertEqual('alice', user['owner']['S'])
        self.assertEqual('alice@example.com', user['email']['S'])
        self.assertNotIn('expiresAt', user)
        self.assertEqual(2, self.database.scan(TableName='chat-state')['Count'])

    def test_global_denial_does_not_consume_another_users_allowance(self):
        with patch.dict(os.environ, {'GLOBAL_QUESTIONS_PER_DAY': '1'}):
            self.consume(86400)
            with self.assertRaises(self.handler.QuotaExceededError) as failure:
                self.consume(86401, 'bob')
        self.assertTrue(failure.exception.global_limit)
        self.assertEqual('1', self.row('global')['dayRequestCount']['N'])
        self.assertNotIn('email', self.row('global'))
        self.assertEqual(2, self.database.scan(TableName='chat-state')['Count'])

    def test_month_and_day_renew_in_the_same_global_row(self):
        january_end = int(datetime(2028, 1, 31, 23, 59, 59, tzinfo=timezone.utc).timestamp())
        self.consume(january_end)
        self.consume(january_end + 1)
        global_row = self.row('global')
        self.assertEqual('1', global_row['monthRequestCount']['N'])
        self.assertEqual('2028-02-01T00:00:00Z', global_row['monthPeriodStart']['S'])
        self.assertEqual('2028-03-01T00:00:00Z', global_row['monthResetsAt']['S'])
        self.assertEqual(2, self.database.scan(TableName='chat-state')['Count'])

    def test_legacy_current_window_counts_are_preserved_once(self):
        self.database.put_item(TableName='chat-state', Item={
            'recordId': {'S': 'quota#alice#day#1'}, 'requestCount': {'N': '2'},
            'expiresAt': {'N': '259200'}})
        self.consume(86400)
        self.assertEqual('3', self.row('alice')['dayRequestCount']['N'])
        with self.assertRaises(self.handler.QuotaExceededError):
            self.consume(86460)
        self.assertEqual('3', self.row('alice')['dayRequestCount']['N'])
        self.assertEqual(3, self.database.scan(TableName='chat-state')['Count'])

    def test_conflict_reloads_counters_before_allowing_another_request(self):
        self.consume(86400)
        write_transaction = self.database.transact_write_items
        def competing_request(**arguments):
            self.database.update_item(TableName='chat-state', Key={'recordId': {'S': 'quota#alice'}},
                UpdateExpression='SET minuteRequestCount = :limit, revision = :version',
                ExpressionAttributeValues={':limit': {'N': '2'}, ':version': {'N': '2'}})
            return write_transaction(**arguments)
        with patch.object(self.database, 'transact_write_items', side_effect=competing_request):
            with self.assertRaises(self.handler.QuotaExceededError):
                self.consume(86401)
        self.assertEqual('2', self.row('alice')['minuteRequestCount']['N'])
        self.assertEqual('1', self.row('global')['dayRequestCount']['N'])

    def test_migration_rechecks_legacy_counts_before_consuming(self):
        legacy_key = {'recordId': {'S': 'quota#alice#day#1'}}
        self.database.put_item(TableName='chat-state', Item={
            **legacy_key, 'requestCount': {'N': '2'}, 'expiresAt': {'N': '259200'}})
        write_transaction = self.database.transact_write_items
        def legacy_request_finishes(**arguments):
            self.database.update_item(TableName='chat-state', Key=legacy_key,
                UpdateExpression='SET requestCount = :count',
                ExpressionAttributeValues={':count': {'N': '3'}})
            return write_transaction(**arguments)
        with patch.object(self.database, 'transact_write_items', side_effect=legacy_request_finishes):
            with self.assertRaises(self.handler.QuotaExceededError):
                self.consume(86400)
        self.assertEqual(1, self.database.scan(TableName='chat-state')['Count'])

    def test_interleaved_requests_retry_without_losing_global_increments(self):
        self.consume(86400)
        write_transaction = self.database.transact_write_items
        competing_request_pending = True
        def interleaved_transaction(**arguments):
            nonlocal competing_request_pending
            if competing_request_pending:
                competing_request_pending = False
                self.handler.consume_chat_quota('bob')
            return write_transaction(**arguments)
        with patch.object(self.database, 'transact_write_items', side_effect=interleaved_transaction) as transactions:
            self.consume(86401)
        self.assertEqual(3, transactions.call_count)
        self.assertEqual('2', self.row('alice')['minuteRequestCount']['N'])
        self.assertEqual('1', self.row('bob')['minuteRequestCount']['N'])
        self.assertEqual('3', self.row('global')['dayRequestCount']['N'])

    def test_conversation_ownership_and_closure_use_history_only(self):
        with patch.object(self.handler.time, 'time', return_value=86400):
            self.handler.remember_conversation('alice', 'session-123', 'Question', 'Answer', 'alice@example.com')
            self.assertEqual('session-123', self.handler.require_owned_conversation('alice', 'session-123'))
            with self.assertRaises(self.handler.ConversationAccessError):
                self.handler.require_owned_conversation('bob', 'session-123')
            self.handler.close_conversation('alice', 'session-123')
            self.handler.close_conversation('alice', 'session-123')
            self.assertIsNone(self.handler.require_owned_conversation('alice', 'session-123'))
        self.assertEqual(0, self.database.scan(TableName='chat-state')['Count'])
        self.assertEqual(2, self.database.scan(TableName='chat-history')['Count'])


if __name__ == '__main__':
    unittest.main()
