import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import ts from 'typescript';
import { createElement, useState, useRef } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
const require = createRequire(import.meta.url);
function chart(source, output) {
  const body = source.slice(source.indexOf('function StructureChart('), source.indexOf('export default function Home'));
  const compiled = ts.transpileModule('export ' + body, {compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText;
  const exports = {};
  new Function('require','exports','useState','useRef','MatrixFloat',compiled)(require,exports,useState,useRef,()=>null);
  return renderToStaticMarkup(createElement(exports.StructureChart,{output})).match(/<svg[\s\S]*?<\/svg>/)[0];
}
test('live page P&F SVG remains identical to pre-UI HEAD for confirmed, adaptive and empty data', () => {
  const source = readFileSync(new URL('../app/page.tsx',import.meta.url),'utf8');
  const baseline = execFileSync('git',['show','8dd31a2:apps/web/app/page.tsx'],{encoding:'utf8'});
  for (const output of [{}, {columns:[{column_id:1,direction:'X'},{column_id:2,direction:'O'}],transitions:[{column_id:1,direction:'X',to_price:'103',effective_box_size:'1',boxes_moved:3},{column_id:2,direction:'O',to_price:'102.5',effective_box_size:'0.25',boxes_moved:2}],structure:{levels:[{status:'confirmed',side:'support',price:'100'}]}}]) {
    assert.equal(chart(source,output),chart(baseline,output));
  }
});
