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
    diagram = Diagram('Deployment architecture', 'Two release paths: manual infrastructure updates and automatic GitHub-driven website builds.', 1600, 1150)
    diagram.boundary(330, 170, 1218, 910, 'AWS Cloud · stack Region: eu-west-1')
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
    diagram.card(1090, 235, 'Stack-managed|AWS resources', 'Amplify · DNS · IAM · Cognito|API · Lambda · data · Bedrock', 'cloud', width=400)
    diagram.card(730, 400, 'AWS Secrets|Manager', 'GitHub connection token|Resolved during stack deployment', 'secrets', height=120)
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
    diagram.save('deployment.svg')


def build_runtime_diagram():
    diagram = Diagram('Runtime architecture', 'Static portfolio delivery and an authenticated, quota-controlled CV assistant using retrieval augmented generation.', 1800, 1280)
    diagram.boundary(340, 170, 1408, 1040, 'AWS Cloud')
    diagram.text(380, 230, 'GLOBAL DELIVERY + API EDGE', 13, PURPLE, 700)
    diagram.boundary(370, 480, 1348, 700, 'Primary Region · eu-west-1', 'region', PURPLE, True)
    diagram.arrow([(270, 310), (400, 310)], 'DNS', (335, 292))
    diagram.arrow([(620, 310), (740, 310)], 'Website alias', (680, 292))
    diagram.arrow([(160, 245), (160, 158), (850, 158), (850, 245)], 'HTTPS · static pages + assets', (650, 158))
    diagram.arrow([(160, 375), (160, 435), (1200, 435), (1200, 375)], 'POST /chat · access token', (810, 435))
    diagram.arrow([(270, 340), (302, 340), (302, 605), (400, 605)], 'Sign in · PKCE', (310, 565), PURPLE, True)
    diagram.arrow([(1200, 375), (1200, 460), (850, 460), (850, 540)], 'Origin verification header', (1040, 460))
    diagram.arrow([(620, 605), (740, 605)], 'JWT validation', (680, 587), PURPLE, True)
    diagram.arrow([(960, 605), (1070, 605)], 'Invoke', (1015, 587))
    diagram.arrow([(1290, 605), (1420, 605)], 'RetrieveAndGenerate', (1355, 587))
    diagram.arrow([(1120, 670), (1120, 740), (850, 740), (850, 815)], 'Atomic quotas + owner', (880, 740))
    diagram.arrow([(1200, 670), (1200, 815)], 'Save exchange', (1200, 765))
    diagram.arrow([(1070, 635), (1010, 635), (1010, 775), (510, 775), (510, 815)], color=MUTED, dashed=True)
    diagram.label(637, 775, 'Operational logs', MUTED)
    diagram.arrow([(1530, 670), (1530, 815)], 'Generate answer', (1530, 745))
    diagram.arrow([(1640, 605), (1680, 605), (1680, 1065), (1640, 1065)], color=GREEN)
    diagram.label(1660, 975, 'Retrieve CV vectors', GREEN)
    diagram.arrow([(620, 1065), (1420, 1065)], 'Build-time ingestion · Titan Text Embeddings v2 · 1,024 dimensions', (1020, 1065), GREEN, True)
    diagram.card(50, 245, 'Visitor browser', 'Static HTML / CSS / JavaScript|Floating CV assistant', 'user')
    diagram.card(400, 245, 'Amazon|Route 53', 'Authoritative website DNS|Apex + www domain', 'route53')
    diagram.card(740, 245, 'AWS Amplify|Hosting', 'Managed CDN + HTTPS|Static website delivery', 'amplify')
    diagram.card(1090, 245, 'Amazon|CloudFront', 'Dedicated chat API distribution|Edge request checks · no cache', 'cloudfront')
    diagram.card(400, 540, 'Amazon|Cognito', 'Email sign-in + verification|Optional social identity providers', 'cognito')
    diagram.card(740, 540, 'Amazon|API Gateway', 'HTTP API · JWT authorizer|Route: POST /chat', 'api')
    diagram.card(1070, 540, 'AWS Lambda', 'Ownership + quota enforcement|CV-grounded prompt + profile name', 'lambda')
    diagram.card(1420, 540, 'Amazon Bedrock', 'Knowledge Bases|Retrieve and generate CV answers', 'bedrock')
    diagram.card(400, 815, 'Amazon|CloudWatch', 'Application error / request logs|14-day log retention', 'cloudwatch')
    diagram.card(740, 815, 'Amazon|DynamoDB', 'User + shared quota counters|Session ownership · expiring state', 'dynamodb')
    diagram.card(1090, 815, 'Amazon|DynamoDB', 'Retained conversation history|Questions · answers · timestamps', 'dynamodb')
    diagram.card(1420, 815, 'Amazon Bedrock', 'Amazon Nova Lite|EU cross-Region inference profile', 'bedrock')
    diagram.card(400, 1000, 'Amazon S3', 'Private source document|Curated knowledge/cv.txt', 's3')
    diagram.card(1420, 1000, 'Amazon S3|Vectors', 'Private vector bucket + index|Retrieval store for the CV', 'vectors')
    diagram.note(740, 1090, 550, 'Conversation lifecycle', ['Five-minute inactivity cutoff or explicit session close.', 'History is retained independently of expiring quota state.'])
    diagram.text(380, 1230, 'Solid arrows: request / data flow · Dashed arrows: identity, logging or build-time preparation · Replies return over the request path.', 13, MUTED)
    diagram.save('runtime.svg')


def main():
    build_deployment_diagram()
    build_runtime_diagram()


if __name__ == '__main__':
    main()
