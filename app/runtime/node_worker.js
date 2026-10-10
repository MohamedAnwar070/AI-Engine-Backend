'use strict';

const readline = require('readline');
const { createRequire } = require('module');
const path = require('path');

const requireFromBackend = createRequire(path.join(process.cwd(), path.sep));
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

/** Modules tools may load. Keep tight for production trust boundary. */
const ALLOWED_REQUIRE_PREFIXES = [
  'http',
  'https',
  'url',
  'querystring',
  'crypto',
  'buffer',
  'stream',
  'util',
  'events',
  'path',
  'zlib',
  'node:http',
  'node:https',
  'node:url',
  'node:querystring',
  'node:crypto',
  'node:buffer',
  'node:stream',
  'node:util',
  'node:events',
  'node:path',
  'node:zlib',
];

const TOOL_ENV_PREFIX = 'TOOL_ENV_';

function sanitizeError(error) {
  if (!error) {
    return { message: 'Unknown error' };
  }

  return {
    message: String(error.message || error),
    name: error.name || 'Error',
    stack: error.stack || null,
  };
}

function createConsoleCapture() {
  const logs = [];
  const append = (level, args) => {
    logs.push({
      level,
      message: args.map((value) => (typeof value === 'string' ? value : JSON.stringify(value))).join(' '),
      timestamp: new Date().toISOString(),
    });
  };

  return {
    logs,
    console: {
      log: (...args) => append('log', args),
      info: (...args) => append('info', args),
      warn: (...args) => append('warn', args),
      error: (...args) => append('error', args),
    },
  };
}

function createSandboxedRequire() {
  return function sandboxedRequire(moduleName) {
    const name = String(moduleName || '');
    const allowed = ALLOWED_REQUIRE_PREFIXES.some(
      (prefix) => name === prefix || name.startsWith(`${prefix}/`)
    );
    if (!allowed) {
      throw new Error(
        `require('${name}') is not allowed in the tool sandbox. Use allowlisted modules or the http runtime.`
      );
    }
    return requireFromBackend(name);
  };
}

function createSandboxedEnv() {
  const env = {};
  for (const [key, value] of Object.entries(process.env)) {
    if (key.startsWith(TOOL_ENV_PREFIX)) {
      env[key.slice(TOOL_ENV_PREFIX.length)] = value;
    }
  }
  if (process.env.NODE_ENV !== undefined) {
    env.NODE_ENV = process.env.NODE_ENV;
  }
  return env;
}

async function executePayload(payload) {
  const { id, code, input_data: inputData } = payload;
  const capture = createConsoleCapture();

  try {
    const executor = new AsyncFunction(
      'inputs',
      'fetch',
      'require',
      'console',
      'URL',
      'URLSearchParams',
      'AbortController',
      'setTimeout',
      'clearTimeout',
      'Buffer',
      'process',
      `
        ${code}
        if (typeof executeNode === 'function') {
          return await executeNode(inputs);
        }
        return undefined;
      `
    );

    const result = await executor(
      inputData,
      global.fetch,
      createSandboxedRequire(),
      capture.console,
      URL,
      URLSearchParams,
      AbortController,
      setTimeout,
      clearTimeout,
      Buffer,
      { env: createSandboxedEnv() }
    );
    process.stdout.write(`${JSON.stringify({ id, ok: true, result, logs: capture.logs })}\n`);
  } catch (error) {
    process.stdout.write(`${JSON.stringify({ id, ok: false, error: sanitizeError(error), logs: capture.logs })}\n`);
  }
}

const rl = readline.createInterface({
  input: process.stdin,
  crlfDelay: Infinity,
});

rl.on('line', async (line) => {
  if (!line.trim()) {
    return;
  }

  let payload;
  try {
    payload = JSON.parse(line);
  } catch (error) {
    process.stdout.write(`${JSON.stringify({ id: null, ok: false, error: sanitizeError(error), logs: [] })}\n`);
    return;
  }

  await executePayload(payload);
});

rl.on('close', () => {
  process.exit(0);
});
