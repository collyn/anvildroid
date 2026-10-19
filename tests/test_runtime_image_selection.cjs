const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('crates/anvildroid-gui/src-frontend/main.js','utf8');
const old={library_id:'old',flavor:'CUSTOM',images:{system:{sha256:'a'},vendor:{sha256:'b'}}};
const desired={library_id:'new',flavor:'GAPPS',status:'Ready',images:{system:{sha256:'c'},vendor:{sha256:'d'}}};
const calls=[];
const ctx=vm.createContext({imageDownloadState:{...old,available_images:[old,desired]},controllerRequest:async r=>{calls.push(r);return desired;}});
vm.runInContext(source.slice(source.indexOf('function imageForRequest('),source.indexOf('async function mutateController(')),ctx);
(async()=>{
 const req={op:'reinstall',flavor:'GAPPS',system_sha256:'c',vendor_sha256:'d'};
 await ctx.activateRequestImage(req);
 assert.equal(calls[0].image_id,'new');
 await ctx.activateRequestImage({op:'reinstall',flavor:'installed'});assert.equal(calls.length,1);
 await assert.rejects(ctx.activateRequestImage({...req,system_sha256:'wrong'}),/unavailable/);
 ctx.controllerRequest=async()=>({...old,status:'Ready'});
 await assert.rejects(ctx.activateRequestImage(req),/selection changed/);
 // Test actual image-library event handler with a select that rejects unknown options.
 let selected='installed',handler;
 const select={get value(){return selected;},set value(v){selected=['installed','custom:old','GAPPS'].includes(v)?v:'';}};
 const nodes={'controller-image':select,'controller-name':{focus(){}},'runtime-create-panel':{scrollIntoView(){}}};
 Object.assign(ctx,{document:{getElementById:id=>id==='available-runtime-images'?{addEventListener:(e,f)=>handler=f}:nodes[id]},imageDownloadBusy:false,imageDownloadActive:()=>false,selectedRuntimeId:'runtime',selectedImageLibraryId:'',runtimeBatchBusy:false,navigatePage(){},renderAvailableImages(){},beginReinstall(){selected='installed';},cancelReinstall(){},renderImageDownload(){},renderCreateImageHint(){}});
 const start=source.indexOf("document.getElementById('available-runtime-images').addEventListener");
 vm.runInContext(source.slice(start,source.indexOf('\n});',start)+4),ctx);
 ctx.controllerRequest=async()=>({...old,status:'Ready'});
 const button={dataset:{imageReinstall:'old'},disabled:false};
 await handler({target:{closest:s=>s==='[data-image-use], [data-image-reinstall]'?button:null}});
 assert.equal(selected,'custom:old');
 console.log('PASS: custom library selection, dropdown cache synchronization, stale SHA rejection, installed source bypass');
})().catch(e=>{console.error(e);process.exitCode=1;});
