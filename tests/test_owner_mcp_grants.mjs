import test from 'node:test';
import assert from 'node:assert/strict';
import {parseArgs,selectClient} from '../scripts/owner-mcp-grants.mjs';
const DEVICE='24630b00-e9c0-4d93-b38a-2367048cd9d1';
const CLIENT='202fd3e1-b058-4531-8750-82c57e4b6ca0';
test('owner grant tool rejects incomplete or unknown actions',()=>{
 assert.throws(()=>parseArgs(['authorize']),/device/);
 assert.throws(()=>parseArgs(['list','--device',DEVICE,'--unexpected','yes']),/invalid option/);
 assert.throws(()=>parseArgs(['list','--device','../../other']),/UUID/);
});
test('device grants require no synthetic Space or project',()=>{
 const o=parseArgs(['authorize','--device',DEVICE,'--profile','full-computer-control']);
 assert.equal(o.profile,'full-computer-control');assert.equal(o.days,30);
 assert.throws(()=>parseArgs(['authorize','--device',DEVICE,'--profile','full-computer-control','--space',DEVICE]),/mismatch/);
});
test('project grant requires exact paired Space and project identifiers',()=>{
 assert.throws(()=>parseArgs(['authorize','--device',DEVICE,'--profile','project-continuity']),/mismatch/);
 assert.throws(()=>parseArgs(['authorize','--device',DEVICE,'--profile','project-continuity','--space',DEVICE]),/UUID/);
 assert.equal(parseArgs(['authorize','--device',DEVICE,'--profile','project-continuity','--space',DEVICE,'--project',DEVICE]).project,DEVICE);
});
test('grant validity is bounded',()=>{
 for(const days of ['0','366','1.2','NaN'])assert.throws(()=>parseArgs(['authorize','--device',DEVICE,'--profile','computer-observe','--days',days]),/days/);
});
test('revoke requires exact grant group UUID',()=>{
 assert.throws(()=>parseArgs(['revoke','--device',DEVICE]),/group/);
 assert.equal(parseArgs(['revoke','--device',DEVICE,'--group',DEVICE]).group,DEVICE);
});
test('OAuth client selection fails closed on ambiguity',()=>{
 const authority={clients:[{client_id:CLIENT},{client_id:DEVICE}]};
 assert.throws(()=>selectClient(authority),/multiple/);
 assert.equal(selectClient(authority,CLIENT),CLIENT);
 assert.throws(()=>selectClient(authority,'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'),/no consented/);
});
