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
            'GUARDRAIL_ID': 'cv-scope-guardrail',
            'GUARDRAIL_VERSION': '1',
            'AUTH_CLIENT_ID': 'chat-client',
            'AUTH_DOMAIN': 'https://login.example.com',
            'CHAT_STATE_TABLE': 'chat-state',
            'CHAT_HISTORY_TABLE': 'chat-history',
            'QUESTIONS_PER_MINUTE': '10',
            'QUESTIONS_PER_DAY': '100',
            'GLOBAL_QUESTIONS_PER_DAY': '1000',
            'GLOBAL_QUESTIONS_PER_MONTH': '10000',
            'GUEST_QUESTIONS_PER_DAY': '10', 'GUEST_IP_QUESTIONS_PER_MINUTE': '3',
            'GUEST_IP_QUESTIONS_PER_DAY': '50', 'IP_HASH_KEY': 'test-ip-key',
            'ORIGIN_VERIFICATION_TOKEN': 'server-only-origin-token',
        })
        self.environment_patch.start()
        self.addCleanup(self.environment_patch.stop)
        self.state_client.get_item.return_value = {'Item': {
            'owner': {'S': 'visitor-123'}, 'endsAt': {'N': '9999999999'},
            'bedrockSessionId': {'S': 'session-123'},
        }}
        self.quota_records = {}
        def read_database_item(**request):
            if request['TableName'] == 'chat-state':
                record_id = request['Key']['recordId']['S']
                return {'Item': self.quota_records.get(record_id, {})}
            return self.state_client.get_item.return_value
        self.state_client.get_item.side_effect = read_database_item
        self.profile_response = MagicMock()
        self.profile_response.__enter__.return_value.read.return_value = json.dumps({
            'sub': 'visitor-123', 'given_name': 'Alice',
        }).encode()
        self.profile_patch = patch.object(self.handler_module, 'urlopen', return_value=self.profile_response)
        self.profile_patch.start()
        self.addCleanup(self.profile_patch.stop)

    def event(self, request_body, origin='https://example.com', user_id='visitor-123'):
        # Simulate trusted authorizer context; Chat Lambda never calls userInfo.
        profile = json.loads(self.profile_response.__enter__.return_value.read.return_value)
        email = self.handler_module.verified_user_email(profile)
        identity = {'visitorId': user_id, 'conversationId': 'session-123', 'sessionHash': 'hash',
            'name': 'Alice', 'company': 'Example Ltd', 'authStatus': 'authenticated' if email else 'guest',
            'subject': user_id if email else '', 'email': email,
            'quotaOwner': self.handler_module.quota_identity(user_id, profile) if email else ''}
        return {
            'headers': {'origin': origin, 'Authorization': 'Bearer signed-access-token',
                        'X-Portfolio-Viewer-IP': '192.0.2.1',
                        'X-Portfolio-Origin': 'server-only-origin-token'},
            'body': json.dumps(request_body),
            'requestContext': {'authorizer': {'lambda': identity, 'jwt': {'claims': {
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
        saved_conversation = self.state_client.transact_write_items.call_args.kwargs['TransactItems'][1]['Put']['Item']
        self.assertEqual('visitor-123', saved_conversation['owner']['S'])
        self.assertNotIn('session-123', saved_conversation['recordId']['S'])
        self.assertEqual('Alice', saved_conversation['name']['S'])
        self.assertEqual('Example Ltd', saved_conversation['company']['S'])
        self.assertNotIn('s3://', response['body'])
        retrieval_request = self.bedrock_client.retrieve_and_generate.call_args.kwargs
        self.assertEqual('session-123', retrieval_request['sessionId'])
        retrieval_configuration = retrieval_request['retrieveAndGenerateConfiguration']
        knowledge_configuration = retrieval_configuration['knowledgeBaseConfiguration']
        self.assertEqual('KB12345678', knowledge_configuration['knowledgeBaseId'])
        self.assertEqual(10, knowledge_configuration['retrievalConfiguration']['vectorSearchConfiguration']['numberOfResults'])
        prompt_template = knowledge_configuration['generationConfiguration']['promptTemplate']
        self.assertIn('including non-AWS certifications', prompt_template['textPromptTemplate'])
        self.assertIn('merely because retrieval did not return it', prompt_template['textPromptTemplate'])
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

    def test_every_generation_uses_the_published_guardrail(self):
        for question in ('Write me a hello world in python', 'Explain git reset', 'Has Andrey used Python?', 'Which teams did he lead?'):
            request = self.handler_module.build_retrieval_request(question, None)
            configuration = request['retrieveAndGenerateConfiguration']['knowledgeBaseConfiguration']['generationConfiguration']
            self.assertEqual({'guardrailId': 'cv-scope-guardrail', 'guardrailVersion': '1'}, configuration['guardrailConfiguration'])
            self.assertIn('Never fulfill requests to write, edit or debug code', configuration['promptTemplate']['textPromptTemplate'])
            self.assertIn('Short follow-up questions', configuration['promptTemplate']['textPromptTemplate'])

    def test_candidate_assessment_questions_are_explicitly_in_scope(self):
        for question in ('What are his soft skills?', 'What makes you different from other candidates?', 'What kind of working environment suits him best?'):
            request = self.handler_module.build_retrieval_request(question, None)
            configuration = request['retrieveAndGenerateConfiguration']['knowledgeBaseConfiguration']['generationConfiguration']
            prompt = configuration['promptTemplate']['textPromptTemplate']
            self.assertIn('Recruiter and behavioural interview questions about Andrey are in scope', prompt)
            self.assertIn('An exact quoted self-description is not', prompt)
            self.assertIn('"you" and "your" refer to Andrey as the candidate', prompt)
            self.assertIn('Never fulfill requests to write, edit or debug code', prompt)
            self.assertIn('guardrailConfiguration', configuration)
        knowledge = (PROJECT_DIRECTORY / 'knowledge/cv.txt').read_text(encoding='utf-8')
        self.assertIn('Questions: What are his soft skills?', knowledge)
        self.assertIn('Questions: What makes you different from other candidates?', knowledge)
        self.assertIn('Questions: What kind of working environment suits him best?', knowledge)

    def test_guardrail_uses_a_new_candidate_friendly_policy_revision(self):
        resources = CLOUDFORMATION_TEMPLATE['Resources']
        topics = resources['ChatGuardrail']['Properties']['TopicPolicyConfig']['TopicsConfig']
        self.assertEqual('Medical legal and financial advice', topics[2]['Name'])
        self.assertIn('v2', resources['ChatGuardrailVersion']['Properties']['Description'])

    def test_guardrail_intervention_returns_and_saves_only_the_refusal(self):
        for question in ('Write me a hello world in python', 'Explain git reset'):
            self.bedrock_client.retrieve_and_generate.return_value = {
                'output': {'text': 'unsafe completion that must not be returned'},
                'sessionId': 'session-123', 'guardrailAction': 'INTERVENED',
                'citations': [{'retrievedReferences': [{'content': {'text': 'unrelated evidence'}}]}]}
            response = self.request({'message': question})
            self.assertEqual(200, response['statusCode'])
            body = json.loads(response['body'])
            self.assertEqual(self.handler_module.SCOPE_REFUSAL, body['answer'])
            self.assertEqual([], body['sources'])
            turn = self.state_client.transact_write_items.call_args.kwargs['TransactItems'][1]['Put']['Item']
            self.assertEqual(self.handler_module.SCOPE_REFUSAL, turn['answer']['S'])

    def test_guardrail_configuration_cannot_be_overridden_by_the_client(self):
        self.bedrock_client.retrieve_and_generate.return_value = {
            'output': {'text': 'Career answer'}, 'sessionId': 'session-123', 'guardrailAction': 'NONE'}
        response = self.request({'message': 'Leadership?', 'guardrailConfiguration': {}, 'guardrailVersion': 'DRAFT'})
        self.assertEqual(200, response['statusCode'])
        request = self.bedrock_client.retrieve_and_generate.call_args.kwargs
        configuration = request['retrieveAndGenerateConfiguration']['knowledgeBaseConfiguration']['generationConfiguration']
        self.assertEqual('1', configuration['guardrailConfiguration']['guardrailVersion'])

    def test_missing_guardrail_configuration_fails_closed(self):
        with patch.dict(os.environ):
            del os.environ['GUARDRAIL_ID']
            self.assertEqual(503, self.request({'message': 'Leadership?'})['statusCode'])
        for invalid_configuration in ({'GUARDRAIL_ID': ''}, {'GUARDRAIL_VERSION': 'DRAFT'}):
            with patch.dict(os.environ, invalid_configuration):
                self.assertEqual(503, self.request({'message': 'Leadership?'})['statusCode'])
        self.bedrock_client.retrieve_and_generate.assert_not_called()

    def test_guardrail_policy_and_permissions_are_provisioned(self):
        resources = CLOUDFORMATION_TEMPLATE['Resources']
        policy = resources['ChatGuardrail']['Properties']
        topics = policy['TopicPolicyConfig']['TopicsConfig']
        self.assertIn('Write me a hello world in python', topics[0]['Examples'])
        for topic in topics:
            self.assertTrue(topic['InputEnabled'])
            self.assertTrue(topic['OutputEnabled'])
            self.assertEqual('BLOCK', topic['InputAction'])
            self.assertEqual('BLOCK', topic['OutputAction'])
        filters = policy['ContentPolicyConfig']['FiltersConfig']
        attack_filter = next(content_filter for content_filter in filters if content_filter['Type'] == 'PROMPT_ATTACK')
        self.assertEqual('HIGH', attack_filter['InputStrength'])
        self.assertTrue(attack_filter['InputEnabled'])
        self.assertEqual('BLOCK', attack_filter['InputAction'])
        self.assertFalse(attack_filter['OutputEnabled'])
        self.assertEqual(self.handler_module.SCOPE_REFUSAL, policy['BlockedInputMessaging'])
        self.assertEqual(self.handler_module.SCOPE_REFUSAL, policy['BlockedOutputsMessaging'])
        self.assertEqual('AWS::Bedrock::GuardrailVersion', resources['ChatGuardrailVersion']['Type'])
        statements = resources['ChatRole']['Properties']['Policies'][0]['PolicyDocument']['Statement']
        permission = next(statement for statement in statements if statement.get('Action') == 'bedrock:ApplyGuardrail')
        self.assertEqual({'GetAtt': 'ChatGuardrail.GuardrailArn'}, permission['Resource'])

    def test_guardrail_service_failure_does_not_retry_without_protection(self):
        self.bedrock_client.retrieve_and_generate.side_effect = ClientError(
            {'Error': {'Code': 'AccessDeniedException', 'Message': 'Guardrail access denied'}},
            'RetrieveAndGenerate')
        self.assertEqual(503, self.request({'message': 'Leadership?'})['statusCode'])
        self.bedrock_client.retrieve_and_generate.assert_called_once()
        self.assertEqual(1, self.state_client.transact_write_items.call_count)

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
        self.assertNotIn('serviceMessage', diagnostic)
        self.assertEqual('bedrock-request-123', diagnostic['requestId'])
        self.assertEqual('model', diagnostic['modelArn'])
        self.assertNotIn('A visitor question', log_message.call_args.args[0])
        self.assertEqual(503, response['statusCode'])
        self.assertNotIn('Invalid model identifier', response['body'])
        self.assertNotIn('bedrock-request-123', response['body'])

    def test_unestablished_requests_never_reach_quotas_profile_or_bedrock(self):
        anonymous_event = self.event({'message': 'AWS?'})
        anonymous_event.pop('requestContext')
        response = self.handler_module.handler(anonymous_event, None)
        self.assertEqual(401, response['statusCode'])
        self.state_client.transact_write_items.assert_not_called()
        self.handler_module.urlopen.assert_not_called()
        self.bedrock_client.retrieve_and_generate.assert_not_called()

    def test_cognito_claims_alone_do_not_authorize_chat(self):
        invalid_claims = (
            {'token_use': 'id'}, {'client_id': 'different-client'}, {'scope': 'openid'},
        )
        for claim_override in invalid_claims:
            request_event = self.event({'message': 'AWS?'})
            request_event['requestContext']['authorizer'].pop('lambda')
            request_event['requestContext']['authorizer']['jwt']['claims'].update(claim_override)
            self.assertEqual(401, self.handler_module.handler(request_event, None)['statusCode'])
        self.bedrock_client.retrieve_and_generate.assert_not_called()

    def test_user_counters_are_separate_and_updated_in_one_transaction(self):
        with patch.object(self.handler_module.time, 'time', return_value=1000000):
            self.handler_module.consume_chat_quota('alice')
            self.handler_module.consume_chat_quota('bob')
        first_writes = self.state_client.transact_write_items.call_args_list[0].kwargs['TransactItems']
        second_writes = self.state_client.transact_write_items.call_args_list[1].kwargs['TransactItems']
        self.assertEqual(2, sum('Put' in write for write in first_writes))
        self.assertEqual('quota#alice', first_writes[0]['Put']['Item']['recordId']['S'])
        self.assertEqual('quota#bob', second_writes[0]['Put']['Item']['recordId']['S'])
        self.assertEqual('1', first_writes[0]['Put']['Item']['minuteRequestCount']['N'])
        self.assertEqual('1', first_writes[0]['Put']['Item']['dayRequestCount']['N'])
        self.assertEqual(first_writes[1], second_writes[1])

    def test_quota_denial_returns_reset_time_and_stops_bedrock(self):
        self.quota_records['quota#guest-visitor-123'] = {
            'recordId': {'S': 'quota#guest-visitor-123'}, 'revision': {'N': '5'},
            'dayPeriodStart': {'S': self.handler_module.quota_timestamp(1000000 // 86400 * 86400)},
            'dayRequestCount': {'N': '10'}}
        with patch.object(self.handler_module.time, 'time', return_value=1000000):
            response = self.request({'message': 'AWS?'})
        self.assertEqual(429, response['statusCode'])
        expected_reset_seconds = (1000000 // 86400 + 1) * 86400 - 1000000
        self.assertEqual(expected_reset_seconds, json.loads(response['body'])['retryAfterSeconds'])
        self.assertEqual(str(expected_reset_seconds), response['headers']['Retry-After'])
        self.assertIn('quotaResetsAt', json.loads(response['body']))
        self.state_client.transact_write_items.assert_not_called()
        self.bedrock_client.retrieve_and_generate.assert_not_called()

    def test_other_users_cannot_resume_a_conversation(self):
        self.state_client.get_item.return_value['Item']['owner'] = {'S': 'another-visitor'}
        response = self.request({'message': 'Follow up', 'sessionId': 'session-123'})
        self.assertEqual(403, response['statusCode'])
        self.state_client.transact_write_items.assert_not_called()
        self.bedrock_client.retrieve_and_generate.assert_not_called()

    def test_expired_conversation_cannot_be_resumed(self):
        self.state_client.get_item.return_value['Item']['endsAt'] = {'N': '1'}
        self.bedrock_client.retrieve_and_generate.return_value = {
            'output': {'text': 'Fresh answer'}, 'sessionId': 'fresh-session'}
        self.assertEqual(403, self.request({'message': 'Follow up', 'sessionId': 'session-123'})['statusCode'])
        self.bedrock_client.retrieve_and_generate.assert_not_called()

    def test_transcript_and_deadline_are_saved_atomically(self):
        self.bedrock_client.retrieve_and_generate.return_value = {
            'output': {'text': 'He managed engineers.'}, 'sessionId': 'session-123'}
        with patch.object(self.handler_module.time, 'time', return_value=1000000):
            self.assertEqual(200, self.request({'message': 'Leadership?'})['statusCode'])
        writes = self.state_client.transact_write_items.call_args.kwargs['TransactItems']
        summary = writes[0]['Update']
        turn = writes[1]['Put']['Item']
        self.assertEqual('chat-history', summary['TableName'])
        self.assertEqual('1000300', summary['ExpressionAttributeValues'][':deadline']['N'])
        self.assertEqual('visitor-123', summary['ExpressionAttributeValues'][':owner']['S'])
        self.assertEqual('Leadership?', turn['question']['S'])
        self.assertEqual('He managed engineers.', turn['answer']['S'])
        self.assertNotIn('expiresAt', turn)
        self.assertNotIn('authorization', turn)

    def test_close_does_not_consume_quota_or_call_bedrock(self):
        self.assertEqual(200, self.request({'action': 'close', 'sessionId': 'session-123'})['statusCode'])
        self.bedrock_client.retrieve_and_generate.assert_not_called()
        self.state_client.transact_write_items.assert_not_called()
        closure = next(call.kwargs for call in self.state_client.update_item.call_args_list
                       if call.kwargs['TableName'] == 'chat-history')
        self.assertEqual('session_closed', closure['ExpressionAttributeValues'][':reason']['S'])

    def test_other_user_cannot_close_conversation(self):
        self.state_client.get_item.return_value['Item']['owner'] = {'S': 'another-visitor'}
        self.assertEqual(403, self.request({'action': 'close', 'sessionId': 'session-123'})['statusCode'])
        self.state_client.transact_write_items.assert_not_called()

    def test_closed_conversation_requires_new_session(self):
        self.state_client.get_item.return_value['Item']['closedAt'] = {'N': '1000'}
        self.bedrock_client.retrieve_and_generate.return_value = {
            'output': {'text': 'Fresh answer'}, 'sessionId': 'fresh-session'}
        self.assertEqual(403, self.request({'message': 'Hello', 'sessionId': 'session-123'})['statusCode'])
        self.bedrock_client.retrieve_and_generate.assert_not_called()

    def test_verified_email_is_saved_in_user_state_and_history_only(self):
        self.profile_response.__enter__.return_value.read.return_value = json.dumps({
            'sub': 'visitor-123', 'email': 'Alice+work@Example.com', 'email_verified': True,
        }).encode()
        self.bedrock_client.retrieve_and_generate.return_value = {
            'output': {'text': 'Answer'}, 'sessionId': 'session-123'}
        self.assertEqual(200, self.request({'message': 'Hello', 'email': 'attacker@example.com'})['statusCode'])
        quota_writes = self.state_client.transact_write_items.call_args_list[0].kwargs['TransactItems']
        self.assertEqual({'S': 'alice@example.com'}, quota_writes[0]['Put']['Item']['email'])
        self.assertEqual({'S': 'visitor-123'}, quota_writes[0]['Put']['Item']['owner'])
        global_item = next(write['Put']['Item'] for write in quota_writes
                          if 'Put' in write and write['Put']['Item']['recordId']['S'] == 'quota#global')
        self.assertNotIn('email', global_item)
        history_writes = self.state_client.transact_write_items.call_args_list[1].kwargs['TransactItems']
        self.assertEqual({'S': 'alice@example.com'}, history_writes[0]['Update']['ExpressionAttributeValues'][':email'])
        self.assertEqual({'S': 'alice@example.com'}, history_writes[1]['Put']['Item']['email'])
        self.assertEqual({'S': 'visitor-123'}, history_writes[1]['Put']['Item']['owner'])
        self.assertNotIn('alice@example.com', json.dumps(self.bedrock_client.retrieve_and_generate.call_args.kwargs))

    def test_cognito_client_can_read_email_verification_status(self):
        client_properties = CLOUDFORMATION_TEMPLATE['Resources']['ChatUserPoolClient']['Properties']
        self.assertIn('email', client_properties['ReadAttributes'])
        self.assertIn('email_verified', client_properties['ReadAttributes'])
        self.assertNotIn('email_verified', client_properties['WriteAttributes'])
        profile = {'email': 'Alice@example.com', 'email_verified': 'true'}
        self.assertEqual('alice@example.com', self.handler_module.verified_user_email(profile))

    def test_every_exchange_includes_owner_and_verified_email(self):
        self.profile_response.__enter__.return_value.read.return_value = json.dumps({
            'sub': 'visitor-123', 'email': 'Alice@example.com', 'email_verified': True,
        }).encode()
        self.bedrock_client.retrieve_and_generate.return_value = {
            'output': {'text': 'Answer'}, 'sessionId': 'session-123'}
        for question in ('Leadership?', 'Which teams?', 'What responsibilities?'):
            self.assertEqual(200, self.request({
                'message': question, 'sessionId': 'session-123'})['statusCode'])
        saved_turns = [call.kwargs['TransactItems'][1]['Put']['Item']
            for call in self.state_client.transact_write_items.call_args_list
            if 'Update' in call.kwargs['TransactItems'][0]]
        self.assertEqual(3, len(saved_turns))
        self.assertEqual(3, len({turn['entryId']['S'] for turn in saved_turns}))
        for turn in saved_turns:
            self.assertEqual({'S': 'visitor-123'}, turn['owner'])
            self.assertEqual({'S': 'alice@example.com'}, turn['email'])

    def test_unverified_or_missing_email_is_not_stored(self):
        self.bedrock_client.retrieve_and_generate.return_value = {
            'output': {'text': 'Answer'}, 'sessionId': 'session-123'}
        for profile in ({'sub': 'visitor-123'}, {
                'sub': 'visitor-123', 'email': 'unverified@example.com', 'email_verified': False}):
            self.profile_response.__enter__.return_value.read.return_value = json.dumps(profile).encode()
            self.assertEqual(200, self.request({'message': 'Hello'})['statusCode'])
            history_writes = self.state_client.transact_write_items.call_args.kwargs['TransactItems']
            self.assertNotIn(':email', history_writes[0]['Update']['ExpressionAttributeValues'])
            self.assertNotIn('email', history_writes[1]['Put']['Item'])
            self.assertEqual({'S': 'visitor-123'}, history_writes[1]['Put']['Item']['owner'])

    def test_missing_session_identity_is_rejected(self):
        event = self.event({'message': 'AWS?'})
        event['requestContext']['authorizer']['lambda'].pop('visitorId')
        self.assertEqual(401, self.handler_module.handler(event, None)['statusCode'])
        self.bedrock_client.retrieve_and_generate.assert_not_called()

    def test_self_declared_name_is_independent_of_cognito_profile(self):
        self.profile_response.__enter__.return_value.read.return_value = b'{"sub":"visitor-123"}'
        self.bedrock_client.retrieve_and_generate.return_value = {
            'output': {'text': 'He has AWS experience.'}, 'sessionId': 'session-123',
        }
        response = self.request({'message': 'AWS?'})
        self.assertEqual('Alice', json.loads(response['body'])['visitorName'])
        retrieval_request = self.bedrock_client.retrieve_and_generate.call_args.kwargs
        prompt = retrieval_request['retrieveAndGenerateConfiguration']['knowledgeBaseConfiguration']
        self.assertIn('Visitor display name', prompt['generationConfiguration']['promptTemplate']['textPromptTemplate'])

    def test_counter_store_failure_stops_generation(self):
        self.state_client.transact_write_items.side_effect = ClientError({
            'Error': {'Code': 'AccessDeniedException', 'Message': 'Denied'},
        }, 'TransactWriteItems')
        with patch('builtins.print') as log_message:
            response = self.request({'message': 'AWS?'})
        self.assertEqual(503, response['statusCode'])
        self.assertEqual('chat_service_failed', json.loads(log_message.call_args.args[0])['event'])
        self.bedrock_client.retrieve_and_generate.assert_not_called()

    def test_visitor_name_is_session_data_not_message_data(self):
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

    def test_direct_origin_requests_are_rejected_before_state_access(self):
        direct_request = self.event({'message': 'AWS?'})
        direct_request['headers'].pop('X-Portfolio-Origin')
        response = self.handler_module.handler(direct_request, None)
        self.assertEqual(403, response['statusCode'])
        self.state_client.transact_write_items.assert_not_called()
        self.bedrock_client.retrieve_and_generate.assert_not_called()

    def test_verified_email_aliases_share_normalized_quota_identity(self):
        aliases = ('First.Last+one@gmail.com', 'firstlast+two@googlemail.com', 'firstlast@gmail.com')
        quota_keys = [self.handler_module.quota_identity('different-subject-' + str(position), {
            'email': email_address, 'email_verified': True,
        }) for position, email_address in enumerate(aliases)]
        self.assertEqual(1, len(set(quota_keys)))
        self.assertNotIn('@', quota_keys[0])
        self.assertNotIn('firstlast', quota_keys[0])
        business_mailbox = self.handler_module.quota_identity('business', {
            'email': 'first.last@company.example', 'email_verified': 'true',
        })
        other_business_mailbox = self.handler_module.quota_identity('business', {
            'email': 'firstlast@company.example', 'email_verified': 'true',
        })
        self.assertNotEqual(business_mailbox, other_business_mailbox)

    def test_unverified_email_cannot_take_over_another_mailbox_quota(self):
        first_identity = self.handler_module.quota_identity('one', {
            'email': 'victim@example.com', 'email_verified': False,
        })
        second_identity = self.handler_module.quota_identity('two', {
            'email': 'victim@example.com', 'email_verified': False,
        })
        self.assertNotEqual(first_identity, second_identity)

    def test_global_month_limit_returns_correct_reset_time(self):
        current_time = 1798761599
        user_windows, global_windows = self.handler_module.quota_windows(current_time)
        self.quota_records['quota#global'] = {
            'recordId': {'S': 'quota#global'}, 'revision': {'N': '2'},
            'monthPeriodStart': {'S': self.handler_module.quota_timestamp(global_windows[1][1])},
            'monthRequestCount': {'N': '10000'}}
        with patch.object(self.handler_module.time, 'time', return_value=current_time):
            response = self.request({'message': 'AWS?'})
        response_body = json.loads(response['body'])
        self.assertEqual(429, response['statusCode'])
        self.assertEqual('global', response_body['limitScope'])
        self.assertEqual(1, response_body['retryAfterSeconds'])
        self.bedrock_client.retrieve_and_generate.assert_not_called()

    def test_global_day_limit_applies_independently_of_user_limits(self):
        current_time = 1000000
        self.quota_records['quota#global'] = {
            'recordId': {'S': 'quota#global'}, 'revision': {'N': '2'},
            'dayPeriodStart': {'S': self.handler_module.quota_timestamp(current_time // 86400 * 86400)},
            'dayRequestCount': {'N': '1000'}}
        with patch.object(self.handler_module.time, 'time', return_value=current_time):
            response = self.request({'message': 'AWS?'})
        self.assertEqual('global', json.loads(response['body'])['limitScope'])
        self.bedrock_client.retrieve_and_generate.assert_not_called()

    def test_calendar_month_windows_handle_leap_years(self):
        leap_day = self.handler_module.datetime(2028, 2, 29, tzinfo=self.handler_module.timezone.utc)
        with patch.object(self.handler_module.time, 'time', return_value=leap_day.timestamp()):
            self.handler_module.consume_chat_quota('visitor')
        global_record = self.state_client.transact_write_items.call_args.kwargs['TransactItems'][1]['Put']['Item']
        self.assertEqual('quota#global', global_record['recordId']['S'])
        self.assertEqual('2028-02-01T00:00:00Z', global_record['monthPeriodStart']['S'])
        self.assertEqual('2028-03-01T00:00:00Z', global_record['monthResetsAt']['S'])


class SignupTests(unittest.TestCase):
    def setUp(self):
        self.signup_module = types.ModuleType('signup_guard')
        function_source = CLOUDFORMATION_TEMPLATE['Resources']['ChatSignupFunction']['Properties']['Code']['ZipFile']
        exec(compile(function_source, 'inline-signup-guard', 'exec'), self.signup_module.__dict__)

    def test_tagged_email_is_rejected_before_cognito_stores_it(self):
        for tagged_email in ('person+one@gmail.com', 'person+two@company.example'):
            with self.subTest(tagged_email=tagged_email):
                with self.assertRaisesRegex(ValueError, 'base email address'):
                    self.signup_module.handler({'request': {'userAttributes': {'email': tagged_email}}}, None)

    def test_base_email_keeps_verification_required(self):
        signup_event = {
            'request': {'userAttributes': {'email': 'person@example.com'}},
            'response': {'autoConfirmUser': False, 'autoVerifyEmail': False},
        }
        self.assertEqual(signup_event, self.signup_module.handler(signup_event, None))

    def test_missing_email_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'valid email'):
            self.signup_module.handler({'request': {'userAttributes': {}}}, None)


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

    def test_public_configuration_contains_only_public_turnstile_key(self):
        with patch.dict(os.environ, {'TURNSTILE_SITE_KEY': 'public-widget-key', 'TURNSTILE_SECRET': 'never-public'}):
            self.publisher_module.write_public_configuration('https://api.example/chat')
        configuration = json.loads(Path('dist/assets/chat-config.json').read_text(encoding='utf-8'))
        self.assertEqual('public-widget-key', configuration['turnstileSiteKey'])
        self.assertNotIn('never-public', json.dumps(configuration))

    def test_amplify_requires_turnstile_before_any_aws_call(self):
        with patch.dict(os.environ, {'AWS_APP_ID': 'app'}):
            os.environ.pop('TURNSTILE_SITE_KEY', None)
            with patch.object(self.publisher_module.boto3, 'client') as client:
                with self.assertRaisesRegex(RuntimeError, 'TURNSTILE_SITE_KEY'):
                    self.publisher_module.publish()
            client.assert_not_called()

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
