import assert from 'node:assert/strict';
import test from 'node:test';
import { clampPosition, defaultPosition, readPosition, savePosition, POSITION_KEY } from '../app/matrix-position.ts';
const b = {width: 1000, height: 600, panelWidth: 300, panelHeight: 320};
test('drag bounds include all edges, resize, invalid values and oversized panels', () => {
  assert.deepEqual(clampPosition({x:-100,y:900}, b), {x:0,y:280});
  assert.deepEqual(clampPosition({x:900,y:-10}, b), {x:700,y:0});
  assert.deepEqual(clampPosition({x:120,y:130}, b), {x:120,y:130});
  assert.deepEqual(clampPosition({x:NaN,y:Infinity}, b), {x:0,y:0});
  assert.deepEqual(clampPosition({x:900,y:900}, {...b,width:200,height:100}), {x:0,y:0});
  assert.deepEqual(defaultPosition(b), {x:682,y:20});
});
test('position round trip, reset, corrupt data and blocked storage', () => {
  const data = new Map();
  const storage = {getItem:k=>data.get(k) ?? null,setItem:(k,v)=>data.set(k,v),removeItem:k=>data.delete(k)};
  savePosition(storage, {x:120,y:60});
  assert.deepEqual(readPosition(storage), {x:120,y:60});
  savePosition(storage, null); assert.equal(readPosition(storage), null);
  for (const value of ['bad','null','{}','{"x":"10","y":20}']) {data.set(POSITION_KEY,value);assert.equal(readPosition(storage),null);}
  const blocked = {getItem(){throw Error();},setItem(){throw Error();},removeItem(){throw Error();}};
  assert.equal(readPosition(blocked),null);
  assert.doesNotThrow(()=>savePosition(blocked,{x:0,y:0}));
  assert.doesNotThrow(()=>savePosition(blocked,null));
});
