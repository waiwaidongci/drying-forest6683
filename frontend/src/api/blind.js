import {api,download} from './client.js';
export const blindApi={
  listBatches:()=>api('/blind/batches'),
  getBatch:id=>api('/blind/batches/'+id),
  sealBatch:data=>api('/blind/batches',{method:'POST',body:JSON.stringify(data)}),
  changeInputs:(id,inputs)=>api('/blind/batches/'+id+'/inputs',{method:'PUT',body:JSON.stringify({inputs})}),
  listApplications:()=>api('/blind/applications'),
  getApplication:id=>api('/blind/applications/'+id),
  apply:data=>api('/blind/applications',{method:'POST',body:JSON.stringify(data)}),
  retry:id=>api('/blind/applications/'+id+'/retry',{method:'POST'}),
  unblind:id=>api('/blind/applications/'+id+'/unblind',{method:'POST'}),
  exportUrl:id=>'/blind/applications/'+id+'/export',
  download:id=>download('/blind/applications/'+id+'/export','blind-'+id+'.csv')
};
