import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import ts from 'typescript';

// Compiles the flattened control-center-* modules for Node's test runner and
// resolves relative imports between them (same approach as entry-readiness-harness.mjs).
// Flat files (not a control-center/ subdirectory) on purpose: tests/manual-drawing.test.mjs
// does a readdirSync(app) + readFileSync over every entry in app/ and assumes it is flat;
// a nested directory there throws EISDIR. See control-center.tsx header for the rest of
// the design rationale.
const require = createRequire(import.meta.url);
export const compile = source => ts.transpileModule(source, { compilerOptions: {
  target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
} }).outputText;

const MODULES = [
  'control-center-types', 'control-center-mock-data', 'control-center-trading-mode-panel',
  'control-center-risk-settings-panel', 'control-center-position-monitor-panel',
  'control-center-market-context-panel', 'control-center-scanners-panel',
  'control-center-system-health-panel', 'control-center-emergency-controls-panel', 'control-center',
];

const sources = {};
for (const name of MODULES) {
  const ext = name === 'control-center-types' || name === 'control-center-mock-data' ? 'ts' : 'tsx';
  sources[name] = readFileSync(new URL(`../app/${name}.${ext}`, import.meta.url), 'utf8');
}

const compiled = {};
function appRequire(name) {
  const bare = name.replace('./', '');
  if (bare in sources) {
    if (!compiled[bare]) {
      compiled[bare] = {};
      new Function('require', 'exports', compile(sources[bare]))(appRequire, compiled[bare]);
    }
    return compiled[bare];
  }
  return require(name);
}

export { sources };
export const ControlCenter = appRequire('control-center').ControlCenter;
export const mockData = appRequire('control-center-mock-data');
