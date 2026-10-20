package main

import (
 "crypto/sha256"
 "encoding/hex"
 "encoding/json"
 "errors"
 "os"
 "os/signal"
 "path/filepath"
 "sort"
 "syscall"
 "time"
)

// runCanonicalOwnerRecovery owns the *entire* execution lifecycle. It must
// never connect to the legacy D1 WebSocket or process its separate outbox.
func runCanonicalOwnerRecovery(cfg config) error {
 c,err:=newClient(cfg);if err!=nil{return err}
 transport:=canonicalDeviceTransport{baseURL:cfg.ControlPlane,deviceID:cfg.DeviceID,deviceToken:cfg.DeviceToken,httpClient:c.http}
 stop:=make(chan os.Signal,1)
 signal.Notify(stop,syscall.SIGINT,syscall.SIGTERM);defer signal.Stop(stop)
 failures:=0
 for {
  select {case <-stop:return nil;default:}
  if err:=canonicalOwnerIteration(c,transport);err!=nil{
   failures++
   if !sleepOrStop(stop,canonicalOwnerRetryDelay(failures)){return nil}
   continue
  }
  failures=0
  // The canonical iteration includes a heartbeat and a fenced claim.
  // A 3s empty-queue loop consumes an unsustainable number of database
  // statements; 12s bounds demand without changing claim/lease semantics.
  if !sleepOrStop(stop,12*time.Second){return nil}
 }
}

// Backoff must not multiply errors if Hyperdrive is temporarily unavailable.
func canonicalOwnerRetryDelay(failures int) time.Duration {
 if failures<=1{return 5*time.Second}
 if failures==2{return 10*time.Second}
 if failures==3{return 20*time.Second}
 return 30*time.Second
}

func canonicalOwnerIteration(c *client,transport canonicalDeviceTransport) error {
 return canonicalOwnerIterationWithExecutor(c,transport,executeCapability)
}

func canonicalOwnerIterationWithExecutor(c *client,transport canonicalDeviceTransport, execute func(config,string,string) actionResult) error {
 // Recover exact committed receipts before claiming any new action.
 if err:=replayCanonicalPendingReports(c.cfg.StateDir,transport);err!=nil{return err}
 capabilities:=allowedCapabilities()
 sort.Strings(capabilities)
 compact,_:=json.Marshal(capabilities)
 digest:=sha256.Sum256(compact)
 if _,err:=transport.post("heartbeat",map[string]any{
  "status":"online","runtime_kind":"ordax-os","agent_version":version,
  "capabilities_digest":hex.EncodeToString(digest[:]),
 });err!=nil{return err}
 job,err:=claimCanonicalOwnerJob(transport,c.agentInstanceID,c.bootID)
 if err!=nil||job==nil{return err}
 ctx:=contextMap(*job)
 ctx["runtime_kind"]="ordax-os"
 ctx["agent_version"]=version
 if _,err=transport.post("start",ctx);err!=nil{return err}
 // A completed local mutation must be reported even if lease renewal fails.
 // The server will reject a superseded epoch instead of replaying the action.
 resultCh:=make(chan actionResult,1)
 go func(){resultCh<-execute(c.cfg,job.Capability,job.EffectID)}()
 renew:=time.NewTicker(30*time.Second);defer renew.Stop()
 var result actionResult
 for {
  select {
  case result=<-resultCh:goto finished
  case <-renew.C:
   lease:=contextMap(*job)
   lease["lease_seconds"]=120
   if _,err=transport.post("lease",lease);err!=nil{
    // The work may still be running. Do not issue another claim.
    result=<-resultCh
    goto finished
   }
  }
 }
finished:
 encoded,digestHex,err:=canonicalResult(result)
 if err!=nil{return err}
 var resultObject map[string]any
 if err=json.Unmarshal(encoded,&resultObject);err!=nil{return err}
 reportID,err:=randomUUID();if err!=nil{return err}
 report:=contextMap(*job)
 report["report_id"]=reportID
 report["result"]=resultObject
 report["result_sha256"]=digestHex
 report["status"]="failed"
 report["error_code"]="owner_recovery_action_failed"
 if result.OK {report["status"]="succeeded";report["error_code"]=nil}
 outbox:=filepath.Join(c.cfg.StateDir,"postgres-outbox")
 if err=os.MkdirAll(outbox,0700);err!=nil{return err}
 path:=filepath.Join(outbox,reportID+".json")
 if err=writeJSONAtomic(path,report,0600);err!=nil{return err}
 wire,err:=canonicalRecoveryWire(report);if err!=nil{return err}
 if _,err=transport.post("report",wire);err!=nil{return err}
 if err=os.Remove(path);err!=nil{return err}
 return nil
}

// Enforce a canonical-only startup choice; never implicitly fall back to D1.
func canonicalTransportSelected() bool {return os.Getenv("ORDAX_OWNER_TRANSPORT")=="postgresql"}
func validateOwnerTransportSelection() error {
 mode:=os.Getenv("ORDAX_OWNER_TRANSPORT")
 if mode!=""&&mode!="postgresql"&&mode!="legacy" {return errors.New("unknown owner transport mode")}
 return nil
}
