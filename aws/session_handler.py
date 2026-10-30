"""Lightweight session establishment/upgrade and uncached HTTP API authorizer.

Opaque credentials are random 256-bit secrets; only their hashes are persisted.
This module is embedded in two Lambdas by scripts/sync_chat_code.py.
"""
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import time
import unicodedata
import uuid
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

db = boto3.client('dynamodb', config=Config(
    read_timeout=2, connect_timeout=1, retries={'total_max_attempts': 2}))
secret_cache = {}


class Rejected(ValueError):
    def __init__(self, message, status=400):
        self.status = status
        super().__init__(message)


def response(status, body):
    return {'statusCode': status, 'headers': {
        'Content-Type': 'application/json', 'Cache-Control': 'no-store'},
        'body': json.dumps(body)}


def headers(event):
    return {key.lower(): value for key, value in event.get('headers', {}).items()}


def trusted(event):
    h = headers(event)
    return (h.get('origin') in os.environ['ALLOWED_ORIGINS'].split(',') and
            hmac.compare_digest(h.get('x-portfolio-origin', '').encode(), os.environ['ORIGIN_VERIFICATION_TOKEN'].encode()))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def credential(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', value):
        raise Rejected('This chat session could not be verified. Start a new conversation.', 401)
    return value


def key(value):
    return {'recordId': {'S': value}}


def history_key(conversation):
    return {**key('session#' + digest(conversation)), 'entryId': {'S': 'summary'}}


def get(record):
    return db.get_item(TableName=os.environ['CHAT_STATE_TABLE'], Key=key(record),
                       ConsistentRead=True).get('Item', {})


def text(item, field):
    return item.get(field, {}).get('S', '')


def number(item, field):
    return int(item.get(field, {}).get('N', '0'))


def identity_field(value, label, maximum):
    if (not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum or
            any(unicodedata.category(char).startswith('C') for char in value) or
            not any(char.isalnum() for char in value)):
        raise Rejected(f'Enter a valid {label} (1–{maximum} characters).')
    return ' '.join(value.split())


def body(event):
    raw = event.get('body') or ''
    if event.get('isBase64Encoded'):
        raw = base64.b64decode(raw, validate=True).decode('utf-8')
    if len(raw.encode()) > 8192:
        raise Rejected('The request is too large.', 413)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise Rejected('The request must be a JSON object.')
    return value


def secret(name):
    if name not in secret_cache:
        secret_cache[name] = json.loads(boto3.client('secretsmanager').get_secret_value(
            SecretId=name)['SecretString'])
    return secret_cache[name]


def ip_identity(event):
    # CloudFront's viewer-request function OVERWRITES this header from viewer.ip.
    # The distribution's private origin header is checked before this is called.
    ip = str(ipaddress.ip_address(headers(event).get('x-portfolio-viewer-ip', '')))
    return hmac.new(os.environ['IP_HASH_KEY'].encode(), ip.encode(), hashlib.sha256).hexdigest()


def reserve_creation(ip_hash):
    now = int(time.time())
    counters = [(f'create#ip#{ip_hash}#{now // 60}', int(os.environ['SESSION_CREATIONS_PER_MINUTE'])),
                (f'create#global#{now // 86400}', int(os.environ['SESSION_CREATIONS_PER_DAY']))]
    writes = []
    for record, limit in counters:
        writes.append({'Update': {'TableName': os.environ['CHAT_STATE_TABLE'], 'Key': key(record),
            'UpdateExpression': 'SET expiresAt = :ttl ADD requestCount :one',
            'ConditionExpression': 'attribute_not_exists(requestCount) OR requestCount < :limit',
            'ExpressionAttributeValues': {':ttl': {'N': str(now + 172800)},
                ':one': {'N': '1'}, ':limit': {'N': str(limit)}}}})
    try:
        db.transact_write_items(TransactItems=writes)
    except ClientError as error:
        if error.response['Error']['Code'] in ('TransactionCanceledException', 'TransactionConflictException'):
            raise Rejected('Too many attempts to start a chat. Please try again in a minute.', 429) from error
        raise


def verify_turnstile(token, event):
    if not isinstance(token, str) or not 1 <= len(token) <= 2048:
        raise Rejected('Please complete the security check and try again.')
    request = Request('https://challenges.cloudflare.com/turnstile/v0/siteverify',
        data=urlencode({'secret': secret(os.environ['TURNSTILE_SECRET_ARN'])['secret'],
                        'response': token}).encode(), method='POST')
    with urlopen(request, timeout=5) as result:
        verification = json.loads(result.read(16384))
    hostname = urlsplit(headers(event)['origin']).hostname
    if (verification.get('success') is not True or verification.get('hostname') != hostname or
            verification.get('action') != 'chat_session'):
        raise Rejected('The security check failed or expired. Please try it again.')
    # Siteverify rejects expired/reused tokens. A conditional row also closes
    # concurrent replay races and protects local mocks with identical semantics.
    try:
        db.put_item(TableName=os.environ['CHAT_STATE_TABLE'], Item={
            **key('turnstile#' + digest(token)), 'expiresAt': {'N': str(int(time.time()) + 600)}},
            ConditionExpression='attribute_not_exists(recordId)')
    except ClientError as error:
        if error.response['Error']['Code'] == 'ConditionalCheckFailedException':
            raise Rejected('This security check was already used. Please try it again.') from error
        raise


def visitor(value):
    now = int(time.time())
    if value is not None:
        token = credential(value)
        row = get('visitor#' + digest(token))
        if number(row, 'expiresAt') <= now:
            raise Rejected('Your visitor identifier has expired. Please start again.', 401)
        return token, text(row, 'visitorId'), number(row, 'expiresAt'), None
    token = secrets.token_urlsafe(32)
    expires = now + 180 * 86400
    row = {**key('visitor#' + digest(token)), 'visitorId': {'S': uuid.uuid4().hex},
           'expiresAt': {'N': str(expires)}}
    return token, text(row, 'visitorId'), expires, {'Put': {
        'TableName': os.environ['CHAT_STATE_TABLE'], 'Item': row,
        'ConditionExpression': 'attribute_not_exists(recordId)'}}


def valid_session(token, visitor_token):
    token_hash = digest(credential(token))
    row = get('credential#' + token_hash)
    now = int(time.time())
    if (not row or number(row, 'expiresAt') <= now or row.get('revoked') or
            not hmac.compare_digest(text(row, 'visitorHash'), digest(credential(visitor_token)))):
        raise Rejected('This chat session expired. Please start a new conversation.', 401)
    visitor_row = get('visitor#' + digest(visitor_token))
    if number(visitor_row, 'expiresAt') <= now or text(visitor_row, 'visitorId') != text(row, 'visitorId'):
        raise Rejected('This visitor identifier expired. Please start again.', 401)
    summary = db.get_item(TableName=os.environ['CHAT_HISTORY_TABLE'],
        Key=history_key(text(row, 'conversationId')), ConsistentRead=True).get('Item', {})
    if (text(summary, 'visitorId') != text(row, 'visitorId') or summary.get('closedAt') or
            number(summary, 'endsAt') <= now):
        raise Rejected('This conversation ended. Please start a new conversation.', 401)
    return token_hash, row, summary


def authorizer(event, context):
    try:
        if not trusted(event):
            return {'isAuthorized': False}
        h = headers(event)
        authorization = h.get('authorization', '')
        if not authorization.startswith('Bearer '):
            return {'isAuthorized': False}
        token_hash, row, _ = valid_session(authorization[7:], h.get('x-visitor-token'))
        return {'isAuthorized': True, 'context': {
            'sessionHash': token_hash, 'visitorId': text(row, 'visitorId'),
            'conversationId': text(row, 'conversationId'), 'authStatus': text(row, 'authStatus'),
            'subject': text(row, 'subject'), 'email': text(row, 'email'),
            'quotaOwner': text(row, 'quotaOwner'), 'name': text(row, 'name'), 'company': text(row, 'company')}}
    except Exception:
        # Fail closed without logging credentials, identity fields or raw IPs.
        return {'isAuthorized': False}


def create(data, event):
    name = identity_field(data.get('name'), 'name', 80)
    company = identity_field(data.get('company'), 'company', 120)
    ip_hash = ip_identity(event)
    reserve_creation(ip_hash)
    verify_turnstile(data.get('turnstileToken'), event)
    visitor_token, visitor_id, visitor_expiry, visitor_write = visitor(data.get('visitorToken'))
    now = int(time.time())
    token = secrets.token_urlsafe(32)
    conversation = uuid.uuid4().hex
    expires = min(now + int(os.environ['SESSION_TOKEN_SECONDS']), visitor_expiry)
    metadata = {'visitorId': {'S': visitor_id}, 'name': {'S': name}, 'company': {'S': company},
                'authStatus': {'S': 'guest'}}
    session = {**key('credential#' + digest(token)), **metadata,
        'visitorHash': {'S': digest(visitor_token)}, 'conversationId': {'S': conversation},
        'expiresAt': {'N': str(expires)}}
    summary = {**history_key(conversation), **metadata,
        'conversationId': {'S': conversation}, 'owner': {'S': visitor_id},
        'startedAt': {'N': str(now)}, 'lastActivityAt': {'N': str(now)},
        'endsAt': {'N': str(now + 300)}, 'endReason': {'S': 'inactivity'}}
    writes = [{'Put': {'TableName': os.environ['CHAT_STATE_TABLE'], 'Item': session,
        'ConditionExpression': 'attribute_not_exists(recordId)'}},
        {'Put': {'TableName': os.environ['CHAT_HISTORY_TABLE'], 'Item': summary,
        'ConditionExpression': 'attribute_not_exists(recordId)'}}]
    if visitor_write:
        writes.append(visitor_write)
    db.transact_write_items(TransactItems=writes)
    return response(201, {'sessionToken': token, 'conversationId': conversation,
        'expiresAt': expires, 'visitorToken': visitor_token, 'visitorExpiresAt': visitor_expiry,
        'authStatus': 'guest', 'name': name, 'company': company})


def upgrade(data, event):
    claims = event.get('requestContext', {}).get('authorizer', {}).get('jwt', {}).get('claims', {})
    now = int(time.time())
    if (claims.get('token_use') != 'access' or claims.get('client_id') != os.environ['AUTH_CLIENT_ID'] or
            not claims.get('sub') or 'portfolio-chat/ask' not in claims.get('scope', '').split() or
            int(claims.get('exp', '0')) <= now):
        raise Rejected('Please sign in again to continue.', 401)
    old_hash, old, _ = valid_session(data.get('sessionToken'), headers(event).get('x-visitor-token'))
    if text(old, 'subject') and text(old, 'subject') != claims['sub']:
        raise Rejected('This conversation belongs to another account.', 403)
    request = Request(os.environ['AUTH_DOMAIN'] + '/oauth2/userInfo',
        headers={'Authorization': headers(event).get('authorization', '')})
    with urlopen(request, timeout=5) as result:
        profile = json.loads(result.read(16384))
    if (profile.get('sub') != claims['sub'] or str(profile.get('email_verified')).lower() != 'true' or
            not isinstance(profile.get('email'), str) or
            not re.fullmatch(r'[^\s@]+@[^\s@]+', profile['email']) or len(profile['email']) > 320):
        raise Rejected('Please verify your account email before continuing.', 403)
    local, domain = profile['email'].strip().lower().rsplit('@', 1)
    email = local.split('+', 1)[0] + '@' + domain
    if domain in ('gmail.com', 'googlemail.com'):
        local, domain = local.split('+', 1)[0].replace('.', ''), 'gmail.com'
    else:
        local = local.split('+', 1)[0]
    quota_owner = 'mailbox-' + digest(local + '@' + domain)
    token = secrets.token_urlsafe(32)
    expires = min(now + int(os.environ['SESSION_TOKEN_SECONDS']), int(claims['exp']),
                  number(get('visitor#' + text(old, 'visitorHash')), 'expiresAt'))
    replacement = {**old, **key('credential#' + digest(token)), 'expiresAt': {'N': str(expires)},
        'authStatus': {'S': 'authenticated'}, 'subject': {'S': claims['sub']},
        'email': {'S': email}, 'quotaOwner': {'S': quota_owner}}
    replacement.pop('lockUntil', None)
    replacement.pop('lockId', None)
    values = {':now': {'N': str(now)}, ':true': {'BOOL': True}}
    db.transact_write_items(TransactItems=[
        {'Update': {'TableName': os.environ['CHAT_STATE_TABLE'], 'Key': key('credential#' + old_hash),
            'UpdateExpression': 'SET revoked = :true',
            'ConditionExpression': 'attribute_not_exists(revoked) AND expiresAt > :now AND (attribute_not_exists(lockUntil) OR lockUntil < :now)',
            'ExpressionAttributeValues': values}},
        {'Put': {'TableName': os.environ['CHAT_STATE_TABLE'], 'Item': replacement,
            'ConditionExpression': 'attribute_not_exists(recordId)'}},
        {'Update': {'TableName': os.environ['CHAT_HISTORY_TABLE'],
            'Key': history_key(text(old, 'conversationId')),
            'UpdateExpression': 'SET authStatus = :status, email = :email, subject = :sub',
            'ConditionExpression': 'visitorId = :visitor AND endsAt > :now AND attribute_not_exists(closedAt)',
            'ExpressionAttributeValues': {':status': {'S': 'authenticated'}, ':email': {'S': email},
                ':sub': {'S': claims['sub']}, ':visitor': old['visitorId'], ':now': {'N': str(now)}}}}])
    return response(200, {'sessionToken': token, 'expiresAt': expires,
        'conversationId': text(old, 'conversationId'), 'authStatus': 'authenticated'})


def handler(event, context):
    if not trusted(event):
        return response(403, {'error': 'Origin not allowed.'})
    try:
        data = body(event)
        if event.get('routeKey') == 'POST /chat/session/upgrade':
            return upgrade(data, event)
        if event.get('routeKey') != 'POST /chat/session':
            return response(404, {'error': 'Route not found.'})
        return create(data, event)
    except Rejected as error:
        return response(error.status, {'error': str(error)})
    except (ValueError, TypeError, UnicodeError):
        return response(400, {'error': 'Please check your introduction and try again.'})
    except ClientError as error:
        if error.response['Error']['Code'] in ('TransactionCanceledException', 'TransactionConflictException'):
            return response(409, {'error': 'The session changed or is busy. Please try again.'})
        print('Session database request failed')
        return response(503, {'error': 'Chat is temporarily unavailable. Please try again.'})
    except Exception:
        print('Session request failed')
        return response(503, {'error': 'The security check or session service is unavailable. Please try again.'})
