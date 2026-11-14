/** Exercise the navigation splitter through pointer, keyboard and viewport events. */
import assert from "node:assert/strict";
import test from "node:test";
import {
  mountNavigationResize, NAVIGATION_DEFAULT_WIDTH, NAVIGATION_MIN_WIDTH, NAVIGATION_MAX_WIDTH,
} from "../webapp/static/navigation.js";

const storageKey="lre.workspace.navigation-width.v1";
function surface(){
  const listeners=new Map(),attributes=new Map(),properties=new Map(),classes=new Set(),captured=new Set();
  return {
    listeners,attributes,hidden:false,tabIndex:0,captured,releases:[],
    style:{setProperty:(key,value)=>properties.set(key,String(value)),getPropertyValue:key=>properties.get(key)||"",removeProperty:key=>properties.delete(key)},
    classList:{add:(...names)=>names.forEach(name=>classes.add(name)),remove:(...names)=>names.forEach(name=>classes.delete(name)),contains:name=>classes.has(name),toggle(name,force){const on=force??!classes.has(name);on?classes.add(name):classes.delete(name);return on;}},
    setAttribute:(name,value)=>attributes.set(name,String(value)),getAttribute:name=>attributes.get(name)??null,removeAttribute:name=>attributes.delete(name),
    addEventListener(name,listener){if(!listeners.has(name))listeners.set(name,new Set());listeners.get(name).add(listener);},
    removeEventListener(name,listener){listeners.get(name)?.delete(listener);},
    dispatch(name,properties={}){
      const event={target:this,currentTarget:this,button:0,isPrimary:true,pointerId:7,clientX:340,defaultPrevented:false,
        preventDefault(){this.defaultPrevented=true;},stopPropagation(){this.stopped=true;},...properties};
      for(const callback of [...(listeners.get(name)||[])])callback(event);
      return event;
    },
    setPointerCapture(id){captured.add(id);},hasPointerCapture:id=>captured.has(id),
    releasePointerCapture(id){captured.delete(id);this.releases.push(id);},focus(){this.focused=true;},
  };
}
function harness({saved=null,viewport=1280,storageError=""}={}){
  const workspace=surface(),handle=surface(),win=surface(),writes=[];
  win.innerWidth=viewport;
  const storage={
    getItem(key){assert.equal(key,storageKey);if(storageError==="read")throw new Error("storage unavailable");return saved;},
    setItem(key,value){if(storageError==="write")throw new Error("quota exceeded");writes.push([key,String(value)]);saved=String(value);},
  };
  workspace.getBoundingClientRect=()=>({left:0,width:win.innerWidth});
  const cleanup=mountNavigationResize(workspace,handle,{window:win,storage});
  return {workspace,handle,win,writes,cleanup,
    width:()=>Number.parseFloat(workspace.style.getPropertyValue("--navigation-width")),
    resize(width){win.innerWidth=width;win.dispatch("resize");},
    pointer(name,properties={}){const event=handle.dispatch(name,properties);if(!event.stopped)win.dispatch(name,event);return event;},
    key(key,properties={}){return handle.dispatch("keydown",{key,...properties});},
  };
}

test("navigation starts at a usable width and ignores malformed saved preferences",()=>{
  assert.equal(NAVIGATION_DEFAULT_WIDTH,340);
  assert.equal(NAVIGATION_MIN_WIDTH,280);
  assert.equal(NAVIGATION_MAX_WIDTH,560);
  for(const saved of [null,"","NaN","Infinity","420px","{}"]){
    const h=harness({saved});
    assert.equal(h.width(),340,`saved value ${saved}`);
    assert.equal(h.writes.length,0);
    h.cleanup();
  }
  const h=harness({saved:"460"});
  assert.equal(h.width(),460);assert.equal(h.writes.length,0);h.cleanup();
});

test("dragging clamps both bounds and persists only the completed user adjustment",()=>{
  const h=harness();
  h.pointer("pointerdown",{clientX:340});
  assert.equal(h.handle.captured.has(7),true);
  h.pointer("pointermove",{clientX:1400});
  assert.equal(h.width(),560);assert.equal(h.writes.length,0);
  h.pointer("pointerup",{clientX:1400});
  assert.deepEqual(h.writes,[[storageKey,"560"]]);
  assert.equal(h.handle.captured.size,0);
  h.pointer("pointerdown",{clientX:560});
  h.pointer("pointermove",{clientX:-400});
  assert.equal(h.width(),280);
  h.pointer("pointerup",{clientX:-400});
  assert.deepEqual(h.writes.at(-1),[storageKey,"280"]);
  h.cleanup();
});

test("other pointers and non-primary buttons cannot take over an active resize",()=>{
  const h=harness();
  h.pointer("pointerdown",{button:2});h.pointer("pointermove",{clientX:500});
  assert.equal(h.width(),340);assert.equal(h.handle.captured.size,0);
  h.pointer("pointerdown",{clientX:340,pointerId:7});
  h.pointer("pointermove",{clientX:500,pointerId:8});
  h.pointer("pointerup",{clientX:500,pointerId:8});
  assert.equal(h.width(),340);assert.equal(h.writes.length,0);
  h.pointer("pointermove",{clientX:400,pointerId:7});
  h.pointer("pointerup",{clientX:400,pointerId:7});
  assert.equal(h.width(),400);assert.deepEqual(h.writes,[[storageKey,"400"]]);
  h.cleanup();
});

test("pointer cancellation, lost capture and Escape roll back without saving",()=>{
  for(const cancel of ["pointercancel","lostpointercapture","Escape"]){
    const h=harness({saved:"400"});
    h.pointer("pointerdown",{clientX:400});h.pointer("pointermove",{clientX:520});
    assert.equal(h.width(),520);
    if(cancel==="Escape")h.key("Escape");
    else {if(cancel==="lostpointercapture")h.handle.captured.delete(7);h.pointer(cancel);}
    assert.equal(h.width(),400,cancel);assert.equal(h.writes.length,0,cancel);
    assert.equal(h.handle.captured.size,0,cancel);
    h.pointer("pointermove",{clientX:550});h.pointer("pointerup");
    assert.equal(h.width(),400);assert.equal(h.writes.length,0);
    h.cleanup();
  }
});

test("keyboard and reset gestures offer the same bounded adjustment without dragging",()=>{
  const h=harness();
  assert.equal(h.key("ArrowRight").defaultPrevented,true);assert.equal(h.width(),356);
  assert.equal(h.handle.getAttribute("aria-valuenow"),"356");
  h.key("ArrowRight",{shiftKey:true});assert.equal(h.width(),388);
  h.key("ArrowLeft");assert.equal(h.width(),372);
  h.key("ArrowLeft",{shiftKey:true});assert.equal(h.width(),340);
  h.key("Home");assert.equal(h.width(),280);
  h.key("End");assert.equal(h.width(),560);
  h.key("Enter");assert.equal(h.width(),340);
  h.key("ArrowRight");h.handle.dispatch("dblclick");assert.equal(h.width(),340);
  assert.deepEqual(h.writes.at(-1),[storageKey,"340"]);
  const saved=h.writes.length;
  assert.equal(h.key("Tab").defaultPrevented,false);assert.equal(h.writes.length,saved);
  h.cleanup();
});

test("viewport constraints preserve the preferred width and constrain keyboard End",()=>{
  const h=harness({saved:"520"});
  h.resize(850);assert.equal(h.width(),370);assert.equal(h.writes.length,0);
  assert.equal(h.handle.getAttribute("aria-valuemax"),"370");
  h.pointer("pointerdown",{clientX:370});h.pointer("pointerup",{clientX:370});
  assert.equal(h.writes.length,0,"focusing the squeezed splitter does not replace the preference");
  h.resize(1280);assert.equal(h.width(),520);assert.equal(h.writes.length,0);
  h.resize(850);h.key("End");assert.equal(h.width(),370);
  assert.deepEqual(h.writes.at(-1),[storageKey,"370"]);
  h.resize(1280);assert.equal(h.width(),370);
  h.cleanup();
});

test("mobile navigation disables resizing and restores the desktop preference",()=>{
  const h=harness({saved:"460",viewport:700});
  assert.equal(h.handle.hidden,true);
  assert.equal(h.handle.tabIndex,-1);
  h.pointer("pointerdown");h.pointer("pointermove",{clientX:550});h.pointer("pointerup");
  h.key("End");h.handle.dispatch("dblclick");
  assert.equal(h.handle.captured.size,0);assert.equal(h.writes.length,0);
  h.resize(1280);assert.equal(h.handle.hidden,false);assert.equal(h.handle.tabIndex,0);assert.equal(h.width(),460);
  h.pointer("pointerdown",{clientX:460});h.pointer("pointermove",{clientX:500});
  h.resize(700);assert.equal(h.handle.captured.size,0);assert.equal(h.writes.length,0);
  h.resize(1280);assert.equal(h.width(),460);h.cleanup();
});

test("storage failures do not break resizing and cleanup releases listeners and capture",()=>{
  for(const storageError of ["read","write"]){
    const h=harness({storageError});
    assert.equal(h.width(),340);
    assert.doesNotThrow(()=>h.key("ArrowRight"));assert.equal(h.width(),356);
    h.pointer("pointerdown",{clientX:356});h.pointer("pointermove",{clientX:440});
    h.cleanup();assert.equal(h.handle.captured.size,0);
    const width=h.width(),writeCount=h.writes.length;
    h.key("End");h.pointer("pointermove",{clientX:560});h.pointer("pointerup");h.resize(800);
    assert.equal(h.width(),width);assert.equal(h.writes.length,writeCount);
    for(const target of [h.handle,h.win])assert.ok([...target.listeners.values()].every(callbacks=>callbacks.size===0));
    assert.doesNotThrow(h.cleanup);
  }
});

test("blocked browser storage and failed pointer capture leave keyboard resizing available",()=>{
  const workspace=surface(),handle=surface(),win=surface();
  win.innerWidth=1280;
  Object.defineProperty(win,"localStorage",{get(){throw new Error("blocked by browser");}});
  const cleanup=mountNavigationResize(workspace,handle,{window:win});
  handle.setPointerCapture=()=>{throw new Error("pointer no longer active");};
  handle.dispatch("pointerdown");handle.dispatch("pointermove",{clientX:500});handle.dispatch("pointerup");
  assert.equal(workspace.style.getPropertyValue("--navigation-width"),"340px");
  assert.equal(workspace.classList.contains("navigation-resizing"),false);
  assert.doesNotThrow(()=>handle.dispatch("keydown",{key:"ArrowRight"}));
  assert.equal(workspace.style.getPropertyValue("--navigation-width"),"356px");
  cleanup();
});
