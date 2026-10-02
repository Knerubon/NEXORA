import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import ts from 'typescript';

// Compiles app/manual-execution.tsx for Node's test runner (MANUAL-EXEC-1).
const require = createRequire(import.meta.url);
export const compile = source => ts.transpileModule(source, { compilerOptions: {
  target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
} }).outputText;
export const manualExecutionSource = readFileSync(new URL('../app/manual-execution.tsx', import.meta.url), 'utf8');
export const manualExecution = {};
new Function('require', 'exports', compile(manualExecutionSource))(require, manualExecution);
