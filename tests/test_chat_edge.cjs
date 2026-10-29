const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {runInNewContext} = require('node:vm');
const template = readFileSync(require('node:path').join(__dirname, '../aws/amplify.yaml'), 'utf8');
const functionBlock = template.match(/FunctionCode: !Sub \|\r?\n([\s\S]*?)(?=\r?\n  ChatCachePolicy:)/);
assert.ok(functionBlock, 'The deployed CloudFront function must be available for testing');
const source = functionBlock[1].split(/\r?\n/).map(line => line.replace(/^        /, '')).join('\n')
  .replaceAll('${DomainName}', 'portfolio.example')
  .replaceAll('${GitHubBranch}', 'main')
  .replaceAll('${PortfolioApp.DefaultDomain}', 'app.amplifyapp.com');
const handler = runInNewContext(source + '\nhandler;');

function request(overrides = {}) {
  return {uri: '/chat', method: 'POST', headers: {
    origin: {value: 'https://portfolio.example'}, authorization: {value: 'Bearer access-token'},
  }, ...overrides};
}

const validRequest = request();
assert.equal(handler({request: validRequest}), validRequest);
assert.equal(handler({request: request({uri: '/other'})}).statusCode, 404);
assert.equal(handler({request: request({method: 'GET'})}).statusCode, 405);
assert.equal(handler({request: request({headers: {}})}).statusCode, 403);
const missingToken = request({headers: {origin: {value: 'https://portfolio.example'}}});
assert.equal(handler({request: missingToken}).statusCode, 401);
assert.equal(handler({request: missingToken}).headers['access-control-allow-origin'].value, 'https://portfolio.example');
const preflight = {...missingToken, method: 'OPTIONS'};
assert.equal(handler({request: preflight}), preflight);
assert.equal(handler({request: request({headers: {
  origin: {value: 'https://attacker.example'}, authorization: {value: 'Bearer token'},
}})}).statusCode, 403);
console.log('CloudFront checks passed: chat path, POST/OPTIONS, allowed origins, bearer header, CORS errors.');
