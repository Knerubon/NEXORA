import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import ts from 'typescript';
import { createElement, useState, useRef } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import * as model from '../app/overlay-model.ts';

// Compile the real overlay module and the live page's StructureChart for Node's test runner.
const require = createRequire(import.meta.url);
const compile = source => ts.transpileModule(source, { compilerOptions: {
  target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
} }).outputText;
export const overlaySource = readFileSync(new URL('../app/chart-overlays.tsx', import.meta.url), 'utf8');
export const modelSource = readFileSync(new URL('../app/overlay-model.ts', import.meta.url), 'utf8');
export const overlays = {};
new Function('require', 'exports', compile(overlaySource))(name => name === './overlay-model' ? model : require(name), overlays);
export { model };

export function chartComponent(pageSource) {
  const body = pageSource.slice(pageSource.indexOf('function StructureChart('), pageSource.indexOf('export default function Home'));
  const exports = {};
  new Function('require', 'exports', 'useState', 'useRef', 'MatrixFloat', 'ChartOverlays', 'LayerControls', 'OverlayPopup', 'useChartOverlays', compile('export ' + body))(
    require, exports, useState, useRef, () => null,
    overlays.ChartOverlays, overlays.LayerControls, overlays.OverlayPopup, overlays.useChartOverlays);
  return exports.StructureChart;
}
export const renderPageChart = (pageSource, props) => renderToStaticMarkup(createElement(chartComponent(pageSource), props));
export const svgOf = markup => markup.match(/<svg[\s\S]*?<\/svg>/)[0];
