"""Offline contract checks for the actual inline CloudFormation handler and build."""
import importlib.util
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
        self.handler_module = types.ModuleType('chat_handler')
        handler_source = CLOUDFORMATION_TEMPLATE['Resources']['ChatFunction']['Properties']['Code']['ZipFile']
        with patch('boto3.client', return_value=self.bedrock_client):
            exec(compile(handler_source, 'inline-chat-handler', 'exec'), self.handler_module.__dict__)
        self.environment_patch = patch.dict(os.environ, {
            'ALLOWED_ORIGINS': 'https://example.com',
            'KNOWLEDGE_BASE_ID': 'KB12345678',
            'MODEL_ARN': 'model',
        })
        self.environment_patch.start()
        self.addCleanup(self.environment_patch.stop)

    def request(self, request_body, origin='https://example.com'):
        return self.handler_module.handler(
            {'headers': {'origin': origin}, 'body': json.dumps(request_body)}, None,
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
        self.assertNotIn('s3://', response['body'])
        retrieval_request = self.bedrock_client.retrieve_and_generate.call_args.kwargs
        self.assertEqual('session-123', retrieval_request['sessionId'])
        retrieval_configuration = retrieval_request['retrieveAndGenerateConfiguration']
        knowledge_configuration = retrieval_configuration['knowledgeBaseConfiguration']
        self.assertEqual('KB12345678', knowledge_configuration['knowledgeBaseId'])
        prompt_template = knowledge_configuration['generationConfiguration']['promptTemplate']
        self.assertIn('$search_results$', prompt_template['textPromptTemplate'])

    def test_throttle_is_safe_to_display(self):
        self.bedrock_client.retrieve_and_generate.side_effect = ClientError(
            {'Error': {'Code': 'ThrottlingException', 'Message': 'private internal details'}},
            'RetrieveAndGenerate',
        )
        response = self.request({'message': 'AWS?'})
        self.assertEqual(429, response['statusCode'])
        self.assertNotIn('private internal details', response['body'])


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
        self.assertEqual({'endpoint': 'https://api.example/chat'}, public_configuration)
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


if __name__ == '__main__':
    unittest.main()
