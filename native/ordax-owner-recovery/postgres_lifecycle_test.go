package main

import (
 "encoding/json"
 "crypto/sha256"
 "encoding/hex"
 "encoding/base64"
 "os"
 "path/filepath"
 "net/http"
 "net/http/httptest"
 "strings"
 "testing"
)

func TestCanonicalOwnerIterationEmptyQueueOnly(t *testing.T) {
 calls:=[]string{}
 server:=httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter,r *http.Request){
  calls=append(calls,r.URL.Path)
  var payload map[string]any
  if err:=json.NewDecoder(r.Body).Decode(&payload);err!=nil{t.Error(err)}
  switch r.URL.Path {
  case "/v3/device/transport/heartbeat":
   if payload["capabilities_digest"]==nil||payload["runtime_kind"]!="ordax-os"{t.Error("invalid heartbeat")}
   _,_=w.Write([]byte(`{"ok":true}`))
  case "/v3/device/transport/claim":
   if payload["agent_instance_id"]==nil {t.Error("missing claim identity")}
   _,_=w.Write([]byte(`{"ok":true,"job":null}`))
  default:
   t.Errorf("unexpected call %s",r.URL.Path)
  }
 }))
 defer server.Close()
 cfg:=config{StateDir:t.TempDir(),ControlPlane:server.URL,DeviceID:"test",DeviceToken:strings.Repeat("x",32)}
 c,err:=newClient(cfg);if err!=nil{t.Fatal(err)}
 transport:=canonicalDeviceTransport{baseURL:server.URL,deviceID:cfg.DeviceID,deviceToken:cfg.DeviceToken,httpClient:server.Client()}
 if err:=canonicalOwnerIteration(c,transport);err!=nil{t.Fatal(err)}
 if len(calls)!=2{t.Fatalf("unexpected operations: %v",calls)}
}

func TestCanonicalTransportModeRequiresExplicitSelection(t *testing.T) {
 t.Setenv("ORDAX_OWNER_TRANSPORT","")
 if canonicalTransportSelected(){t.Fatal("implicit postgres selection")}
 t.Setenv("ORDAX_OWNER_TRANSPORT","postgresql")
 if !canonicalTransportSelected()||validateOwnerTransportSelection()!=nil{t.Fatal("canonical selection failed")}
 t.Setenv("ORDAX_OWNER_TRANSPORT","typo")
 if err:=validateOwnerTransportSelection();err==nil{t.Fatal("invalid mode accepted")}
}

func TestCanonicalOwnerCompleteJobLifecycle(t *testing.T) {
 cfg:=config{StateDir:t.TempDir(),DeviceID:"6c212d20-c9c4-448d-a933-8d0b9345f88f",DeviceToken:strings.Repeat("x",32)}
 c,err:=newClient(cfg);if err!=nil{t.Fatal(err)}
 capabilities:=allowedCapabilities()
 if len(capabilities)==0{t.Fatal("no owner capabilities")}
 payload:=[]byte("{}")
 digest:=sha256.Sum256(payload)
 job:=map[string]any{
  "job_id":"9e3b2d39-8a18-4b59-ae17-ae62502b0e01",
  "effect_id":"c2bdf087-9c09-4fbe-97dc-bb5bb0311902",
  "attempt_id":"5d8b9456-1b32-4a9c-98ef-eb335a100037",
  "lease_id":"7d83170f-5232-45bc-bf2c-5d4517813842",
  "execution_epoch":1,
  "agent_instance_id":c.agentInstanceID,"boot_id":c.bootID,
  "payload_canonical_b64":base64.StdEncoding.EncodeToString(payload),
  "payload_sha256":hex.EncodeToString(digest[:]),
  "capability":capabilities[0],
 }
 calls:=[]string{}
 server:=httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter,r *http.Request){
  calls=append(calls,r.URL.Path)
  switch r.URL.Path {
  case "/v3/device/transport/heartbeat", "/v3/device/transport/start":
   _,_=w.Write([]byte(`{"ok":true}`))
  case "/v3/device/transport/claim":
   _=json.NewEncoder(w).Encode(map[string]any{"ok":true,"job":job})
  case "/v3/device/transport/report":
   var received map[string]any
   if err:=json.NewDecoder(r.Body).Decode(&received);err!=nil{t.Error(err)}
   if _,ok:=received["result_canonical_b64"];!ok{t.Error("missing canonical receipt")}
   if received["job_id"]!=job["job_id"]{t.Error("wrong job reported")}
   _,_=w.Write([]byte(`{"ok":true}`))
  default:t.Errorf("unexpected lifecycle call: %s",r.URL.Path)
  }
 }))
 defer server.Close()
 transport:=canonicalDeviceTransport{baseURL:server.URL,deviceID:cfg.DeviceID,deviceToken:cfg.DeviceToken,httpClient:server.Client()}
 executions:=0
 fake:=func(_ config,capability,effect string) actionResult {
  executions++
  if capability!=capabilities[0]||effect!=job["effect_id"]{t.Error("wrong effect")}
  return actionResult{OK:true,Summary:"tested",Data:map[string]any{}}
 }
 if err:=canonicalOwnerIterationWithExecutor(c,transport,fake);err!=nil{t.Fatal(err)}
 if executions!=1||len(calls)!=4{t.Fatalf("executions=%d calls=%v",executions,calls)}
 entries,err:=os.ReadDir(filepath.Join(cfg.StateDir,"postgres-outbox"))
 if err!=nil||len(entries)!=0{t.Fatalf("receipt not cleared after ack: entries=%v err=%v",entries,err)}
}
