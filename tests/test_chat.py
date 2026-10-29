"""Offline contract checks for the actual inline CloudFormation handler and build."""
import importlib.util
import base64
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
import yaml

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]


class CloudFormationLoader(yaml.SafeLoader):
    pass


def construct_cloudformation_intrinsic(yaml_loader, tag_name, yaml_node):
    if isinstance(yaml_node, yaml.ScalarNode):
        return {tag_name: yaml_loader.construct_scalar(yaml_node)}
    if isinstance(yaml_node, yaml.SequenceNode):
        return {tag_name: yaml_loader.construct_sequence(yaml_node)}
    return {tag_name: yaml_loader.construct_mapping(yaml_node)}


CloudFormationLoader.add_multi_constructor('!', construct_cloudformation_intrinsic)
CLOUDFORMATION_TEMPLATE = yaml.load(
    (PROJECT_DIRECTORY / 'aws/amplify.yaml').read_text(encoding='utf-8'),
    Loader=CloudFormationLoader,
)


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.bedrock_client = MagicMock()
        self.state_client = MagicMock()
        self.handler_module = types.ModuleType('chat_handler')
        handler_source = CLOUDFORMATION_TEMPLATE['Resources']['ChatFunction']['Properties']['Code']['ZipFile']
        with patch('boto3.client', side_effect=[self.bedrock_client, self.state_client]):
            exec(compile(handler_source, 'inline-chat-handler', 'exec'), self.handler_module.__dict__)
        self.environment_patch = patch.dict(os.environ, {
            'ALLOWED_ORIGINS': 'https://example.com',
            'KNOWLEDGE_BASE_ID': 'KB12345678',
            'MODEL_ARN': 'model',
            'AUTH_CLIENT_ID': 'chat-client',
            'AUTH_DOMAIN': 'https://login.example.com',
            'CHAT_STATE_TABLE': 'chat-state',
            'QUESTIONS_PER_MINUTE': '10',
            'QUESTIONS_PER_DAY': '100',
        })
        self.environment_patch.start()
        self.addCleanup(self.environment_patch.stop)
        self.state_client.get_item.return_value = {'Item': {
            'owner': {'S': 'visitor-123'}, 'expiresAt': {'N': '9999999999'},
        }}
        self.profile_response = MagicMock()
        self.profile_response.__enter__.return_value.read.return_value = json.dumps({
            'sub': 'visitor-123', 'given_name': 'Alice',
        }).encode()
        self.profile_patch = patch.object(self.handler_module, 'urlopen', return_value=self.profile_response)
        self.profile_patch.start()
        self.addCleanup(self.profile_patch.stop)

    def event(self, request_body, origin='https://example.com', user_id='visitor-123'):
        return {
            'headers': {'origin': origin, 'Authorization': 'Bearer signed-access-token'},
            'body': json.dumps(request_body),
            'requestContext': {'authorizer': {'jwt': {'claims': {
                'sub': user_id, 'token_use': 'access', 'client_id': 'chat-client',
                'scope': 'openid profile portfolio-chat/ask',
            }}}},
        }

    def request(self, request_body, origin='https://example.com'):
        return self.handler_module.handler(
            self.event(request_body, origin), None,
        )

    def test_validation_and_origin_do_not_call_bedrock(self):
        invalid_requests = (
            {}, [], {'message': 12}, {'message': ' '}, {'message': 'a' * 1001},
            {'message': 'AWS?', 'sessionId': []},
        )
        for invalid_request in invalid_requests:
            self.assertEqual(400, self.request(invalid_request)['statusCode'])
        self.assertEqual(403, self.request({'message': 'AWS?'}, 'https://attacker.example')['statusCode'])
        self.assertEqual(413, self.request({'message': 'a' * 9000})['statusCode'])
        self.bedrock_client.retrieve_and_generate.assert_not_called()

    def test_evidence_and_session_are_preserved_without_private_s3_uris(self):
        self.bedrock_client.retrieve_and_generate.return_value = {
            'output': {'text': 'He led ten engineers.'}, 'sessionId': 'session-123',
            'citations': [{'retrievedReferences': [{'content': {'text': 'ING: team of 10'},
                'location': {'s3Location': {'uri': 's3://private/cv/cv.txt'}}}]}]}
        response = self.request({'message': 'Leadership?', 'sessionId': 'session-123'})
        self.assertEqual(200, response['statusCode'])
        response_body = json.loads(response['body'])
        self.assertEqual(['ING: team of 10'], response_body['sources'])
        self.assertEqual('Alice', response_body['visitorName'])
        saved_conversation = self.state_client.put_item.call_args.kwargs['Item']
        self.assertEqual('visitor-123', saved_conversation['owner']['S'])
        self.assertNotIn('session-123', saved_conversation['recordId']['S'])
        self.assertNotIn('Alice', str(saved_conversation))
        self.assertNotIn('s3://', response['body'])
        retrieval_request = self.bedrock_client.retrieve_and_generate.call_args.kwargs
        self.assertEqual('session-123', retrieval_request['sessionId'])
        retrieval_configuration = retrieval_request['retrieveAndGenerateConfiguration']
        knowledge_configuration = retrieval_configuration['knowledgeBaseConfiguration']
        self.assertEqual('KB12345678', knowledge_configuration['knowledgeBaseId'])
        prompt_template = knowledge_configuration['generationConfiguration']['promptTemplate']
        self.assertIn('$search_results$', prompt_template['textPromptTemplate'])
        self.assertIn('$query$', prompt_template['textPromptTemplate'])
        self.assertIn('$output_format_instructions$', prompt_template['textPromptTemplate'])
        self.assertIn('"Alice"', prompt_template['textPromptTemplate'])

    def test_throttle_is_safe_to_display(self):
        self.bedrock_client.retrieve_and_generate.side_effect = ClientError(
            {'Error': {'Code': 'ThrottlingException', 'Message': 'private internal details'}},
            'RetrieveAndGenerate',
        )
        response = self.request({'message': 'AWS?'})
        self.assertEqual(429, response['statusCode'])
        self.assertNotIn('private internal details', response['body'])

    def test_bedrock_diagnostics_stay_in_server_logs(self):
        self.bedrock_client.retrieve_and_generate.side_effect = ClientError(
            {
                'Error': {'Code': 'ValidationException', 'Message': 'Invalid model identifier'},
                'ResponseMetadata': {'RequestId': 'bedrock-request-123'},
            },
            'RetrieveAndGenerate',
        )
        with patch('builtins.print') as log_message:
            response = self.request({'message': 'A visitor question'})
        diagnostic = json.loads(log_message.call_args.args[0])
        self.assertEqual('ValidationException', diagnostic['errorCode'])
        self.assertEqual('Invalid model identifier', diagnostic['serviceMessage'])
        self.assertEqual('bedrock-request-123', diagnostic['requestId'])
        self.assertEqual('model', diagnostic['modelArn'])
        self.assertNotIn('A visitor question', log_message.call_args.args[0])
        self.assertEqual(503, response['statusCode'])
        self.assertNotIn('Invalid model identifier', response['body'])
        self.assertNotIn('bedrock-request-123', response['body'])

    def test_anonymous_requests_never_reach_quotas_profile_or_bedrock(self):
        anonymous_event = self.event({'message': 'AWS?'})
        anonymous_event.pop('requestContext')
        response = self.handler_module.handler(anonymous_event, None)
        self.assertEqual(401, response['statusCode'])
        self.state_client.transact_write_items.assert_not_called()
        self.handler_module.urlopen.assert_not_called()
        self.bedrock_client.retrieve_and_generate.assert_not_called()

    def test_id_tokens_and_wrong_clients_or_scopes_are_rejected(self):
        invalid_claims = (
            {'token_use': 'id'}, {'client_id': 'different-client'}, {'scope': 'openid'},
        )
        for claim_override in invalid_claims:
            request_event = self.event({'message': 'AWS?'})
            request_event['requestContext']['authorizer']['jwt']['claims'].update(claim_override)
            self.assertEqual(401, self.handler_module.handler(request_event, None)['statusCode'])
        self.bedrock_client.retrieve_and_generate.assert_not_called()

    def test_user_counters_are_separate_and_updated_in_one_transaction(self):
        with patch.object(self.handler_module.time, 'time', return_value=1000000):
            self.handler_module.consume_user_quota('alice')
            self.handler_module.consume_user_quota('bob')
        first_updates = self.state_client.transact_write_items.call_args_list[0].kwargs['TransactItems']
        second_updates = self.state_client.transact_write_items.call_args_list[1].kwargs['TransactItems']
        self.assertEqual(2, len(first_updates))
        for first_update, second_update in zip(first_updates, second_updates):
            first_key = first_update['Update']['Key']['recordId']['S']
            second_key = second_update['Update']['Key']['recordId']['S']
            self.assertNotEqual(first_key, second_key)
        self.assertEqual('10', first_updates[0]['Update']['ExpressionAttributeValues'][':limit']['N'])
        self.assertEqual('100', first_updates[1]['Update']['ExpressionAttributeValues'][':limit']['N'])

    def test_quota_denial_returns_reset_time_and_stops_bedrock(self):
        self.state_client.transact_write_items.side_effect = ClientError({
            'Error': {'Code': 'TransactionCanceledException', 'Message': 'Quota exceeded'},
            'CancellationReasons': [{'Code': 'None'}, {'Code': 'ConditionalCheckFailed'}],
        }, 'TransactWriteItems')
        with patch.object(self.handler_module.time, 'time', return_value=1000000):
            response = self.request({'message': 'AWS?'})
        self.assertEqual(429, response['statusCode'])
        expected_reset_seconds = (1000000 // 86400 + 1) * 86400 - 1000000
        self.assertEqual(expected_reset_seconds, json.loads(response['body'])['retryAfterSeconds'])
        self.assertEqual(str(expected_reset_seconds), response['headers']['Retry-After'])
        self.bedrock_client.retrieve_and_generate.assert_not_called()

    def test_other_users_cannot_resume_a_conversation(self):
        self.state_client.get_item.return_value['Item']['owner'] = {'S': 'another-visitor'}
        response = self.request({'message': 'Follow up', 'sessionId': 'session-123'})
        self.assertEqual(403, response['statusCode'])
        self.state_client.transact_write_items.assert_not_called()
        self.bedrock_client.retrieve_and_generate.assert_not_called()

    def test_expired_conversation_cannot_be_resumed(self):
        self.state_client.get_item.return_value['Item']['expiresAt'] = {'N': '1'}
        self.assertEqual(403, self.request({'message': 'Follow up', 'sessionId': 'session-123'})['statusCode'])
        self.bedrock_client.retrieve_and_generate.assert_not_called()

    def test_profile_identity_must_match_token_subject(self):
        self.profile_response.__enter__.return_value.read.return_value = b'{"sub":"another-visitor"}'
        self.assertEqual(401, self.request({'message': 'AWS?'})['statusCode'])
        self.bedrock_client.retrieve_and_generate.assert_not_called()

    def test_missing_profile_name_does_not_fabricate_a_name(self):
        self.profile_response.__enter__.return_value.read.return_value = b'{"sub":"visitor-123"}'
        self.bedrock_client.retrieve_and_generate.return_value = {
            'output': {'text': 'He has AWS experience.'}, 'sessionId': 'session-123',
        }
        response = self.request({'message': 'AWS?'})
        self.assertEqual('', json.loads(response['body'])['visitorName'])
        retrieval_request = self.bedrock_client.retrieve_and_generate.call_args.kwargs
        prompt = retrieval_request['retrieveAndGenerateConfiguration']['knowledgeBaseConfiguration']
        self.assertNotIn('Visitor display name', prompt['generationConfiguration']['promptTemplate']['textPromptTemplate'])

    def test_counter_store_failure_stops_generation(self):
        self.state_client.transact_write_items.side_effect = ClientError({
            'Error': {'Code': 'AccessDeniedException', 'Message': 'Denied'},
        }, 'TransactWriteItems')
        with patch('builtins.print') as log_message:
            response = self.request({'message': 'AWS?'})
        self.assertEqual(503, response['statusCode'])
        self.assertEqual('chat_service_failed', json.loads(log_message.call_args.args[0])['event'])
        self.bedrock_client.retrieve_and_generate.assert_not_called()

    def test_visitor_name_is_profile_data_not_request_data(self):
        self.bedrock_client.retrieve_and_generate.return_value = {
            'output': {'text': 'Hello Alice'}, 'sessionId': 'session-123',
        }
        response = self.request({'message': 'AWS?', 'visitorName': 'Invented name'})
        self.assertEqual(200, response['statusCode'])
        self.assertEqual('Alice', json.loads(response['body'])['visitorName'])
        self.assertNotIn('Invented name', str(self.bedrock_client.retrieve_and_generate.call_args))

    def test_base64_requests_keep_authenticated_subject(self):
        self.bedrock_client.retrieve_and_generate.return_value = {
            'output': {'text': 'Hello Alice'}, 'sessionId': 'session-123',
        }
        request_event = self.event({'message': 'AWS?'})
        request_event['body'] = base64.b64encode(request_event['body'].encode()).decode()
        request_event['isBase64Encoded'] = True
        self.assertEqual(200, self.handler_module.handler(request_event, None)['statusCode'])


class PublishTests(unittest.TestCase):
    def setUp(self):
        module_specification = importlib.util.spec_from_file_location(
            'publish', PROJECT_DIRECTORY / 'scripts/publish_knowledge.py',
        )
        self.publisher_module = importlib.util.module_from_spec(module_specification)
        module_specification.loader.exec_module(self.publisher_module)
        self.temporary_workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_workspace.cleanup)
        previous_directory = Path.cwd()
        os.chdir(self.temporary_workspace.name)
        self.addCleanup(os.chdir, previous_directory)
        Path('dist/assets').mkdir(parents=True)
        Path('knowledge').mkdir()
        Path('knowledge/cv.txt').write_text('CV evidence', encoding='utf-8')
        self.environment_patch = patch.dict(os.environ, {
            'CV_BUCKET': 'private',
            'CV_KNOWLEDGE_BASE': 'kb',
            'CV_DATA_SOURCE': 'source',
            'CHAT_API_URL': 'https://api.example/chat',
            'CHAT_AUTH_DOMAIN': 'https://login.example.com',
            'CHAT_AUTH_CLIENT_ID': 'chat-client',
        })
        self.environment_patch.start()
        self.addCleanup(self.environment_patch.stop)

    def mock_aws_clients(self, storage_client, knowledge_client):
        return patch.object(
            self.publisher_module.boto3, 'client',
            side_effect=[storage_client, knowledge_client],
        )

    def test_upload_wait_and_public_configuration(self):
        storage_client, knowledge_client = MagicMock(), MagicMock()
        storage_client.head_object.side_effect = ClientError({'Error': {'Code': '404'}}, 'HeadObject')
        knowledge_client.start_ingestion_job.return_value = {
            'ingestionJob': {'status': 'STARTING', 'ingestionJobId': 'job'},
        }
        knowledge_client.get_ingestion_job.return_value = {'ingestionJob': {'status': 'COMPLETE'}}
        with self.mock_aws_clients(storage_client, knowledge_client), patch.object(
            self.publisher_module.time, 'sleep',
        ):
            self.publisher_module.publish()
        self.assertEqual('cv/cv.txt', storage_client.put_object.call_args.kwargs['Key'])
        knowledge_client.get_ingestion_job.assert_called_once()
        public_configuration = json.loads(Path('dist/assets/chat-config.json').read_text())
        self.assertEqual({
            'endpoint': 'https://api.example/chat',
            'auth': {'domain': 'https://login.example.com', 'clientId': 'chat-client'},
        }, public_configuration)
        self.assertFalse(Path('dist/knowledge').exists())

    def test_failed_ingestion_fails_build(self):
        storage_client, knowledge_client = MagicMock(), MagicMock()
        knowledge_client.start_ingestion_job.return_value = {
            'ingestionJob': {'status': 'FAILED', 'failureReasons': ['model access']},
        }
        with self.mock_aws_clients(storage_client, knowledge_client):
            with self.assertRaisesRegex(RuntimeError, 'model access'):
                self.publisher_module.publish()
        self.assertFalse(Path('dist/assets/chat-config.json').exists())

    def test_unchanged_knowledge_still_triggers_ingestion(self):
        storage_client, knowledge_client = MagicMock(), MagicMock()
        knowledge_digest = self.publisher_module.hashlib.sha256(b'CV evidence').hexdigest()
        storage_client.head_object.return_value = {'Metadata': {'sha256': knowledge_digest}}
        knowledge_client.start_ingestion_job.return_value = {'ingestionJob': {'status': 'COMPLETE'}}
        with self.mock_aws_clients(storage_client, knowledge_client):
            self.publisher_module.publish()
        storage_client.put_object.assert_not_called()
        knowledge_client.start_ingestion_job.assert_called_once_with(
            knowledgeBaseId='kb', dataSourceId='source',
        )

    def test_partial_configuration_is_rejected_before_aws_access(self):
        with patch.dict(os.environ, {'CV_DATA_SOURCE': ''}), patch.object(
            self.publisher_module.boto3, 'client',
        ) as create_client:
            with self.assertRaisesRegex(RuntimeError, 'Incomplete'):
                self.publisher_module.publish()
        create_client.assert_not_called()

    def test_ingestion_with_failed_documents_rejects_publication(self):
        storage_client, knowledge_client = MagicMock(), MagicMock()
        knowledge_client.start_ingestion_job.return_value = {
            'ingestionJob': {'status': 'COMPLETE', 'statistics': {'numberOfDocumentsFailed': 1}},
        }
        with self.mock_aws_clients(storage_client, knowledge_client):
            with self.assertRaisesRegex(RuntimeError, 'CV ingestion failed'):
                self.publisher_module.publish()
        self.assertFalse(Path('dist/assets/chat-config.json').exists())

    def test_amplify_build_without_backend_configuration_fails(self):
        build_environment = {
            name: '' for name in self.publisher_module.ENVIRONMENT_VARIABLE_NAMES
        }
        build_environment['AWS_APP_ID'] = 'portfolio-app'
        with patch.dict(os.environ, build_environment), patch.object(
            self.publisher_module.boto3, 'client',
        ) as create_client:
            with self.assertRaisesRegex(RuntimeError, 'Deploy aws/amplify.yaml'):
                self.publisher_module.publish()
        create_client.assert_not_called()
        self.assertFalse(Path('dist/assets/chat-config.json').exists())

    def test_local_preview_without_backend_configuration_stays_disconnected(self):
        local_environment = {
            name: '' for name in self.publisher_module.ENVIRONMENT_VARIABLE_NAMES
        }
        local_environment['AWS_APP_ID'] = ''
        with patch.dict(os.environ, local_environment), patch.object(
            self.publisher_module.boto3, 'client',
        ) as create_client:
            self.publisher_module.publish()
        create_client.assert_not_called()
        public_configuration = json.loads(Path('dist/assets/chat-config.json').read_text())
        self.assertEqual({'endpoint': None}, public_configuration)


if __name__ == '__main__':
    unittest.main()
