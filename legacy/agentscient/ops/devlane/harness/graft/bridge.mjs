// The lane's closed, local-only boundary to the pinned Graft library.
import { readFile } from 'node:fs/promises';
import { isAbsolute, join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const tools = new Set(['graft_find_code', 'graft_file_api', 'graft_trace_calls',
  'graft_find_all', 'graft_repo_map']);
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const integer = value => Number.isSafeInteger(value) && value >= 0;

async function main() {
  let raw = '';
  for await (const chunk of process.stdin) {
    raw += chunk;
    if (Buffer.byteLength(raw) > 65536) throw new Error('request exceeds 65536 bytes');
  }
  const request = JSON.parse(raw);
  if (!object(request)) throw new Error('request must be an object');
  const { op, root, cache } = request;
  const keys = op === 'build' ? ['op', 'root', 'cache']
    : op === 'query' ? ['op', 'root', 'cache', 'tool', 'arguments'] : [];
  if (!keys.length || Object.keys(request).length !== keys.length ||
      !keys.every(key => Object.hasOwn(request, key))) throw new Error('unknown request variant');
  for (const value of [root, cache, process.argv[2]]) {
    if (typeof value !== 'string' || !isAbsolute(value) || resolve(value) !== value)
      throw new Error('paths must be canonical and absolute');
  }
  if (cache === root || cache.startsWith(root + '/')) throw new Error('cache is inside source');
  const graft = process.argv[2];
  if (op === 'build') {
    const { buildGraph } = await import(pathToFileURL(join(graft, 'dist/graph/build.js')));
    const result = await buildGraph(root, { contextDir: cache, onlyDirs: ['ops'], graphOnly: true });
    if (!object(result) || !Array.isArray(result.errors) || result.errors.length ||
        !integer(result.files) || !integer(result.nodes) || !integer(result.edges) ||
        !result.files || !result.nodes || result.contextDir !== cache ||
        result.graphPath !== join(cache, '.graph/wiring.json'))
      throw new Error('unavailable: invalid or empty graph build');
    const graph = JSON.parse(await readFile(result.graphPath, 'utf8'));
    if (!Array.isArray(graph.nodes) || !Array.isArray(graph.edges) ||
        graph.nodes.length !== result.nodes || graph.edges.length !== result.edges ||
        !graph.nodes.every(node => typeof node.path === 'string' &&
          node.path.startsWith('ops/') && !node.path.split('/').includes('..')))
      throw new Error('graph escaped ops or disagrees with build counts');
    return result;
  }
  if (!tools.has(request.tool) || !object(request.arguments)) throw new Error('invalid query');
  const { callTool } = await import(pathToFileURL(join(graft, 'dist/mcp/tools.js')));
  const result = await callTool(root, request.tool, request.arguments, cache);
  if (!object(result) || typeof result.text !== 'string' || typeof result.isError !== 'boolean')
    throw new Error('invalid query result');
  return { text: result.text, isError: result.isError };
}

try {
  process.stdout.write(JSON.stringify({ ok: true, result: await main() }) + '\n');
} catch (error) {
  process.stdout.write(JSON.stringify({ ok: false, error: String(error.message ?? error).slice(0, 500) }) + '\n');
}
