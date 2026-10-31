"""Build portable architecture SVGs using the official AWS icon artwork."""

from html import escape
from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIRECTORY = PROJECT_ROOT / 'assets' / 'architecture'
INK = '#192b36'
MUTED = '#596b79'
PURPLE = '#7854a4'
GREEN = '#338568'


class Diagram:
    def __init__(self, title, subtitle, width, height):
        self.width = width
        self.height = height
        self.parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title description">',
            f'<title id="title">{escape(title)}</title><desc id="description">{escape(subtitle)}</desc>',
            '<defs><marker id="arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0 0 7 3.5 0 7Z" fill="#192b36"/></marker></defs>',
            f'<rect width="{width}" height="{height}" fill="#f7f9fc"/>',
            '<g font-family="Arial, Helvetica, sans-serif">',
        ]
        self.text(52, 49, 'ANDREY LEBEDEV / PORTFOLIO ARCHITECTURE', 12, PURPLE, 700)
        self.text(52, 96, title, 32, INK, 700)
        self.text(52, 130, subtitle, 16, MUTED)

    def text(self, x, y, value, size=14, color=INK, weight=400):
        self.parts.append(f'<text x="{x}" y="{y}" fill="{color}" font-size="{size}" font-weight="{weight}">{escape(value)}</text>')

    def icon(self, name, x, y, size=56):
        source = (OUTPUT_DIRECTORY / 'icons' / f'{name}.svg').read_text(encoding='utf-8')
        source = re.sub(r'<\?xml[^>]*>', '', source).strip()
        prefix = f'{name}-{x}-{y}-'
        identifiers = re.findall(r'\bid="([^"]+)"', source)
        for identifier in sorted(set(identifiers), key=len, reverse=True):
            source = source.replace(f'id="{identifier}"', f'id="{prefix}{identifier}"')
            source = source.replace(f'url(#{identifier})', f'url(#{prefix}{identifier})')
            source = source.replace(f'href="#{identifier}"', f'href="#{prefix}{identifier}"')
        source = re.sub(r'\s(?:width|height)="[^"]*"', '', source, count=2)
        source = source.replace('<svg ', f'<svg x="{x}" y="{y}" width="{size}" height="{size}" ', 1)
        self.parts.append(source)

    def boundary(self, x, y, width, height, label, icon='cloud', color='#d5861f', dashed=False):
        dash = ' stroke-dasharray="8 6"' if dashed else ''
        self.parts.append(f'<rect x="{x}" y="{y}" width="{width}" height="{height}" fill="#fff" stroke="{color}" stroke-width="1.5"{dash}/>')
        self.icon(icon, x + 14, y + 12, 26)
        self.text(x + 50, y + 31, label, 14, color, 700)

    def card(self, x, y, title, detail, icon=None, width=220, height=130, tag=None):
        self.parts.append(f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="10" fill="#fff" stroke="#d9e0e8" stroke-width="1.3"/>')
        if icon:
            self.icon(icon, x + 16, y + 19)
        else:
            self.parts.append(f'<rect x="{x+16}" y="{y+19}" width="56" height="56" rx="9" fill="#edf1f6"/>')
            self.text(x + 24, y + 54, tag or 'Git', 19, INK, 700)
        for position, line in enumerate(title.split('|')):
            self.text(x + 85, y + 38 + position * 21, line, 16, INK, 700)
        for position, line in enumerate(detail.split('|')):
            self.text(x + 16, y + height - 30 + position * 17, line, 12, MUTED)

    def arrow(self, points, label=None, label_at=None, color=INK, dashed=False):
        coordinates = ' '.join(f'{x},{y}' for x, y in points)
        dash = ' stroke-dasharray="6 5"' if dashed else ''
        self.parts.append(f'<polyline points="{coordinates}" fill="none" stroke="{color}" stroke-width="1.7" stroke-linejoin="round" marker-end="url(#arrow)"{dash}/>')
        if label and label_at:
            self.label(*label_at, label, color)

    def label(self, x, y, value, color=MUTED):
        width = len(value) * 6.5 + 16
        self.parts.append(f'<rect x="{x-width/2}" y="{y-15}" width="{width}" height="23" rx="4" fill="#fff"/>')
        self.parts.append(f'<text x="{x}" y="{y}" text-anchor="middle" fill="{color}" font-size="12">{escape(value)}</text>')

    def note(self, x, y, width, title, lines):
        height = 47 + 20 * len(lines)
        self.parts.append(f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="8" fill="#edf3f8"/>')
        self.text(x + 16, y + 26, title, 14, INK, 700)
        for position, line in enumerate(lines):
            self.text(x + 16, y + 49 + position * 20, line, 13, MUTED)

    def save(self, filename):
        self.text(52, self.height - 25, 'Source: aws/amplify.yaml + amplify.yml | Official AWS Architecture Icons: July 2026 | Logical service architecture; no VPC', 12, MUTED)
        self.parts.append('</g></svg>')
        (OUTPUT_DIRECTORY / filename).write_text('\n'.join(self.parts) + '\n', encoding='utf-8')


def build_deployment_diagram():
    diagram = Diagram('Deployment architecture', 'Manual CloudFormation updates; automatic GitHub-driven website and knowledge builds.', 1600, 1870)
    diagram.boundary(330, 170, 1218, 1620, 'AWS Cloud · stack Region: eu-west-1')
    diagram.text(366, 220, '01 / INFRASTRUCTURE · MANUAL AWS CLI DEPLOYMENT', 13, PURPLE, 700)
    diagram.text(366, 530, '02 / APPLICATION + KNOWLEDGE · AMPLIFY AUTO BUILD', 13, GREEN, 700)
    diagram.arrow([(270, 300), (380, 300)], 'Upload YAML', (324, 282), PURPLE)
    diagram.arrow([(600, 300), (730, 300)], 'Template URL', (665, 282), PURPLE)
    diagram.arrow([(950, 300), (1090, 300)], 'Create / update', (1020, 282), PURPLE)
    diagram.arrow([(840, 400), (840, 365)], 'Resolve secret', (945, 390), PURPLE, True)
    diagram.arrow([(730, 340), (660, 340), (660, 545), (490, 545), (490, 560)], color=PURPLE, dashed=True)
    diagram.label(545, 450, 'Configure hosting + build role', PURPLE)
    diagram.arrow([(270, 625), (380, 625)], 'Push main', (324, 607), GREEN)
    diagram.arrow([(600, 625), (730, 625)], 'amplify.yml', (665, 607), GREEN)
    diagram.arrow([(950, 625), (1090, 625)], 'Publish release', (1020, 607), GREEN)
    diagram.arrow([(840, 690), (840, 820)], 'Upload changed CV', (840, 755), GREEN)
    diagram.arrow([(950, 885), (1000, 885)], color=GREEN)
    diagram.arrow([(1220, 885), (1270, 885)], color=GREEN)
    diagram.card(50, 235, 'Local machine', 'aws/amplify.yaml|Authenticated AWS CLI', 'user')
    diagram.card(380, 235, 'Amazon S3', 'Private template upload|cloudformation/ prefix', 's3')
    diagram.card(730, 235, 'AWS|CloudFormation', 'Change set + IAM capability|Parameters preserved on update', 'cloudformation')
    diagram.card(1090, 235, 'Stack-managed|AWS resources', 'Amplify · DNS · IAM · Cognito|API · Lambda · data · Bedrock + Guardrails', 'cloud', width=400)
    diagram.card(730, 400, 'AWS Secrets|Manager', 'GitHub · origin · IP HMAC|External Turnstile secret', 'secrets', height=120)
    diagram.card(50, 560, 'GitHub', 'main branch|HTML · assets · CV · templates', tag='Git')
    diagram.card(380, 560, 'AWS Amplify', 'Connected production branch|Automatic builds enabled', 'amplify')
    diagram.card(730, 560, 'Amplify build', 'Assemble static site + config|Publish CV and wait for ingestion', 'amplify')
    diagram.card(1090, 560, 'Amplify Hosting', 'Static release on managed CDN|Route 53 domain + managed TLS', 'amplify', width=400)
    diagram.card(730, 820, 'Amazon S3', 'Private CV source|cv/cv.txt', 's3')
    diagram.card(1000, 820, 'Amazon Bedrock', 'Knowledge Base ingestion|Titan Text Embeddings v2', 'bedrock')
    diagram.card(1270, 820, 'Amazon S3|Vectors', 'Dedicated vector bucket + index|1,024-dimensional embeddings', 'vectors')
    diagram.note(380, 965, 1110, 'Release contract', [
        'The build waits for successful knowledge-base ingestion before publishing the static website.',
        'GitHub pushes rebuild the application; infrastructure YAML changes require a separate CloudFormation deployment.'
    ])
    diagram.text(366, 1100, '03 / SESSION SECURITY · CLOUDFORMATION-MANAGED RESOURCES', 13, PURPLE, 700)
    diagram.card(380, 1130, 'Session Lambda', 'Introduction · Turnstile Siteverify|Create / upgrade · no Bedrock', 'lambda', width=335)
    diagram.card(755, 1130, 'Lambda Authorizer', 'Opaque session + visitor credential|Uncached lifecycle validation', 'lambda', width=335)
    diagram.card(1130, 1130, 'DynamoDB', 'Existing state + retained history|Visitor · session · quota · exchanges', 'dynamodb', width=360)
    diagram.card(50, 1130, 'Cloudflare|Turnstile', 'Managed widget · public site key|Configure hostnames manually', width=260, tag='CF')
    diagram.arrow([(310, 1195), (380, 1195)], color=PURPLE)
    diagram.arrow([(1090, 1195), (1130, 1195)], color=PURPLE)
    diagram.note(380, 1310, 1110, 'Optional account transition', [
        'Existing Cognito pool, client, verified email and social providers are preserved.',
        'JWT-protected session upgrade rotates the chat credential; subsequent chat requests use the Lambda Authorizer.'
    ])
    diagram.text(366, 1450, '04 / OWNER NOTIFICATIONS · OPTIONAL CLOUDFORMATION RESOURCES', 13, GREEN, 700)
    diagram.card(380, 1480, 'Notification Lambda', 'History stream · first saved Q&A|Leased deduplication in existing state', 'lambda', width=335)
    diagram.card(755, 1480, 'Amazon SNS', 'Standard topic · email subscription|Owner confirms subscription manually', width=335, tag='SNS')
    diagram.card(1130, 1480, 'Amazon SQS', 'Failed stream-batch metadata|14-day encrypted failure queue', width=360, tag='SQS')
    diagram.card(50, 1480, 'Owner email', 'One publication per conversation|Email delivery is at least once', 'user', width=260)
    diagram.arrow([(1310, 1260), (1510, 1260), (1510, 1420), (350, 1420), (350, 1545), (380, 1545)],
                  'History stream', (1100, 1420), GREEN)
    diagram.arrow([(715, 1545), (755, 1545)], color=GREEN)
    diagram.arrow([(920, 1610), (920, 1635), (180, 1635), (180, 1610)], color=GREEN)
    diagram.arrow([(547, 1610), (547, 1645), (1310, 1645), (1310, 1610)], color=MUTED, dashed=True)
    diagram.note(380, 1690, 1110, 'Independent delivery path', [
        'ChatNotificationEmail enables the stream consumer, SNS subscription, failure queue and scoped IAM permissions.',
        'Email publication runs after the exchange is saved; notification failures cannot block the chat response.'
    ])
    diagram.save('deployment.svg')


def build_runtime_diagram():
    diagram = Diagram('Runtime architecture', 'Guest sessions, optional Cognito upgrades and asynchronous owner notifications.', 1800, 2220)
    diagram.boundary(340, 170, 1408, 1955, 'AWS Cloud · eu-west-1 application Region')
    diagram.text(380, 230, '01 / INTRODUCTION AND SESSION ESTABLISHMENT · NO MODEL INVOCATION', 13, PURPLE, 700)
    diagram.card(50, 260, 'Visitor browser', 'Existing greeting · name + company|Start chatting · Managed Turnstile', 'user', width=260)
    diagram.card(400, 260, 'Route 53 +|Amplify Hosting', 'Static pages · HTTPS|GitHub-driven application builds', 'amplify', width=270)
    diagram.card(740, 260, 'CloudFront', 'Allow listed routes · origin token|Overwrites trusted viewer IP · no cache', 'cloudfront', width=320)
    diagram.card(1140, 260, 'HTTP API Gateway', 'POST /chat/session · no Cognito|Separate session-route throttling', 'api', width=310)
    diagram.card(1140, 470, 'Session Lambda', 'Validate introduction + Siteverify|Atomic abuse counters · session creation', 'lambda', width=310)
    diagram.card(50, 470, 'Cloudflare|Turnstile', 'Managed check · chat_session action|Single-use token · hostname validation', width=260, tag='CF')
    diagram.card(400, 470, 'Secrets Manager', 'Turnstile secret · IP HMAC key|Separate public site key in build config', 'secrets', width=310)
    diagram.card(1500, 470, 'DynamoDB|state', 'Visitor + credential hashes|Expiry + revocation', 'dynamodb', width=230, height=150)
    diagram.arrow([(310, 320), (400, 320)], 'Pages', (355, 302))
    diagram.arrow([(310, 365), (350, 365), (350, 425), (900, 425), (900, 390)], 'Form + Turnstile token + existing visitor credential', (675, 425))
    diagram.arrow([(1060, 325), (1140, 325)])
    diagram.arrow([(1295, 390), (1295, 470)])
    diagram.arrow([(310, 535), (350, 535), (350, 640), (1295, 640), (1295, 600)], 'Server-side Siteverify · no per-message challenge', (825, 640), PURPLE, True)
    diagram.arrow([(710, 535), (1140, 535)], 'Secret access', (925, 517), PURPLE, True)
    diagram.arrow([(1450, 535), (1520, 535)])
    diagram.text(380, 705, '02 / EVERY CHAT REQUEST · SESSION AUTHORISATION BEFORE INFERENCE', 13, GREEN, 700)
    diagram.card(400, 750, 'HTTP API Gateway', 'POST /chat · CUSTOM authorizer|Bearer chat token + visitor credential', 'api', width=310)
    diagram.card(790, 750, 'Lambda Authorizer', 'Hash lookup · expiry · revocation|Visitor binding · five-minute deadline', 'lambda', width=310)
    diagram.card(1180, 750, 'Chat Lambda', 'Session context · ownership · lock|Atomic quotas before any inference', 'lambda', width=310)
    diagram.arrow([(900, 390), (900, 440), (755, 440), (755, 720), (555, 720), (555, 750)], 'Via CloudFront · session token returned to browser', (790, 675))
    diagram.arrow([(710, 815), (790, 815)])
    diagram.arrow([(1100, 815), (1180, 815)], 'Verified identity', (1140, 797))
    diagram.arrow([(1670, 620), (1670, 695), (945, 695), (945, 750)], 'Consistent reads · caching disabled', (1400, 695), PURPLE, True)
    diagram.card(400, 960, 'DynamoDB quotas', 'Guest visitor + keyed IP; verified user|Separate shared global counters', 'dynamodb', width=340)
    diagram.card(820, 960, 'DynamoDB history', 'Conversation ID · visitor · name/company|Email on upgrade · timestamped exchanges', 'dynamodb', width=350)
    diagram.card(1250, 960, 'Amazon Bedrock', 'Knowledge Base · Amazon Nova Pro|Published Guardrails · grounded replies', 'bedrock', width=440)
    diagram.arrow([(1290, 880), (1290, 920), (570, 920), (570, 960)], 'Revision-checked transaction', (820, 920))
    diagram.arrow([(1350, 880), (1350, 935), (995, 935), (995, 960)])
    diagram.arrow([(1470, 880), (1470, 960)])
    diagram.note(400, 1130, 1290, 'Identity and quota boundaries', [
        'Visitor ID persists across conversations; conversation ID groups history; random session token authorises only that conversation.',
        'Guest: 10 questions/UTC day; IP: 3/minute + 50/UTC day. Authenticated: 10/minute + 100/UTC day.',
        'Global: 1,000/UTC day AND 1,000/UTC month. Shared IP/global counters never carry visitor name, company or email.',
        'Private S3 source → Bedrock Knowledge Base ingestion → S3 Vectors is unchanged; Guardrails apply to every answer.'
    ])
    diagram.text(380, 1325, '03 / OPTIONAL COGNITO UPGRADE · SAME VISITOR, CONVERSATION AND PENDING QUESTION', 13, PURPLE, 700)
    diagram.card(50, 1370, 'Guest limit reached', 'Sign in / Register · Maybe later|Global limit never suggests sign-in', 'user', width=260)
    diagram.card(400, 1370, 'Cognito', 'PKCE · verified email|Existing email + social providers', 'cognito', width=310)
    diagram.card(790, 1370, 'HTTP API Gateway', 'POST /chat/session/upgrade|Existing Cognito JWT authorizer', 'api', width=350)
    diagram.card(1220, 1370, 'Session Lambda', 'Verify guest + account · rotate token|Attach subject/email · preserve history', 'lambda', width=470)
    diagram.arrow([(310, 1435), (400, 1435)], color=PURPLE)
    diagram.arrow([(710, 1435), (790, 1435)], color=PURPLE)
    diagram.arrow([(1140, 1435), (1220, 1435)], color=PURPLE)
    diagram.note(400, 1550, 1290, 'Resume and lifecycle', [
        'The browser restores its transcript and pending question, then sends the rotated session credential through the same chat authorizer.',
        'Verified-user quotas replace guest/IP quotas. Global limits remain. Sign-out closes the session and returns to guest introduction.',
        'Five minutes of inactivity or explicit closure ends the conversation. Retained summaries include sessions that asked no questions.'
    ])
    diagram.text(380, 1730, '04 / FIRST SAVED Q&A · ASYNCHRONOUS EMAIL OUTSIDE THE CHAT REQUEST', 13, GREEN, 700)
    diagram.arrow([(995, 1090), (995, 1110), (1710, 1110), (1710, 1700), (370, 1700), (370, 1835), (400, 1835)],
                  'Atomic firstInteractionAt marker · old + new images', (1200, 1700), GREEN)
    diagram.card(400, 1770, 'DynamoDB Streams', 'Only first-interaction transitions|No email for introductions or later turns', 'dynamodb', width=310)
    diagram.card(790, 1770, 'Notification Lambda', 'Claim lease · publish · record SENT|30-day deduplication + message snapshot', 'lambda', width=350)
    diagram.card(1220, 1770, 'Amazon SNS', 'Standard topic|Confirmed email subscription', width=250, tag='SNS')
    diagram.card(1510, 1770, 'SQS|failures', 'Batch metadata|14-day retention', width=210, tag='SQS')
    diagram.card(50, 1770, 'Owner email', 'First question + visitor introduction|Verified email when already signed in', 'user', width=260)
    diagram.arrow([(710, 1835), (790, 1835)], color=GREEN)
    diagram.arrow([(1140, 1835), (1220, 1835)], color=GREEN)
    diagram.arrow([(1345, 1900), (1345, 1950), (180, 1950), (180, 1900)], color=GREEN)
    diagram.arrow([(965, 1900), (965, 1930), (1615, 1930), (1615, 1900)],
                  'After bounded retries', (1300, 1930), MUTED, True)
    diagram.note(400, 1990, 1320, 'Delivery semantics and isolation', [
        'The first saved exchange sets firstInteractionAt/firstQuestion once, in the same transaction as the history write.',
        'Opening the widget, introduction submission, later messages, upgrades and closure do not trigger another publication.',
        'Stream retries are deduplicated; a crash after SNS accepts a message can still produce a duplicate. No exactly-once email guarantee.'
    ])
    diagram.text(380, 2170, 'Solid arrows: request/data flow · Dashed arrows: supporting verification or failure handling · Guest chat requires no third-party cookies.', 13, MUTED)
    diagram.save('runtime.svg')


def main():
    build_deployment_diagram()
    build_runtime_diagram()
    import sys
    if '--png' in sys.argv:
        import cairosvg
        for name in ('deployment', 'runtime'):
            cairosvg.svg2png(url=str(OUTPUT_DIRECTORY / (name + '.svg')),
                            write_to=str(OUTPUT_DIRECTORY / (name + '.png')))


if __name__ == '__main__':
    main()
