import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import ts from 'typescript';
import { renderToStaticMarkup } from 'react-dom/server';
import { createElement } from 'react';
import * as layout from '../app/pnf-layout.ts';

// Compile the actual component for Node's test runner without a new build dependency.
const require = createRequire(import.meta.url);
const source = readFileSync(new URL('../app/structure-chart.tsx', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
} });
const exports = {};
new Function('require', 'exports', outputText)(name => name === './pnf-layout' ? layout : require(name), exports);
export const renderChart = output => renderToStaticMarkup(createElement(exports.StructureChart, { output }));
