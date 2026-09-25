import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import ts from 'typescript';

// Compiles app/entry-readiness.tsx for Node's test runner and resolves it for modules that import it.
const require = createRequire(import.meta.url);
export const compile = source => ts.transpileModule(source, { compilerOptions: {
  target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
} }).outputText;
export const entryReadinessSource = readFileSync(new URL('../app/entry-readiness.tsx', import.meta.url), 'utf8');
export const entryReadiness = {};
new Function('require', 'exports', compile(entryReadinessSource))(require, entryReadiness);
export const appRequire = name => name === './entry-readiness' ? entryReadiness : require(name);
// Removes the Entry Readiness block (last child of the strength panel) so pre-feature panel markup can be compared.
export const withoutEntryReadiness = html => {
  const start = html.indexOf('<div class="entry-readiness"');
  const end = html.indexOf('</div><div class="market-panel">');
  if (start < 0 || end < start) throw new Error('Entry Readiness block not found in strength panel');
  return html.slice(0, start) + html.slice(end);
};
