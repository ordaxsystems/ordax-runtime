package main

import (
 "crypto/sha256"
 "encoding/hex"
 "encoding/json"
 "net/http"
 "net/http/httptest"
 "os"
 "path/filepath"
 "strings"
 "testing"
)

func TestCanonicalOutboxReplayRetainsUnacknowledgedReceipts(t *testing.T) {
 for _,tc:=range []struct{name string;ack string;removed bool}{
  {"acknowledged",`{"ok":true}`,true},
  {"negative",`{"ok":false}`,false},
  {"malformed","{}",false},
 }{
  t.Run(tc.name,func(t *testing.T){
   seen:=false
   server:=httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter,r *http.Request){
    seen=true
    if r.URL.Path!="/v3/device/transport/recover-report"{t.Errorf("wrong path: %s",r.URL.Path)}
    var wire map[string]any
    if err:=json.NewDecoder(r.Body).Decode(&wire);err!=nil{t.Error(err)}
    if _,ok:=wire["result_canonical_b64"];!ok{t.Error("missing canonical payload")}
    _,_=w.Write([]byte(tc.ack))
   }))
   defer server.Close()
   dir:=t.TempDir()
   outbox:=filepath.Join(dir,"outbox")
   if err:=os.Mkdir(outbox,0700);err!=nil{t.Fatal(err)}
   result:=map[string]any{"ok":true}
   encoded,_:=json.Marshal(result)
   digest:=sha256.Sum256(encoded)
   report:=map[string]any{"result":result,"result_sha256":hex.EncodeToString(digest[:]),"report_id":"test"}
   data,_:=json.Marshal(report)
   file:=filepath.Join(outbox,"test.json")
   if err:=os.WriteFile(file,data,0600);err!=nil{t.Fatal(err)}
   transport:=canonicalDeviceTransport{baseURL:server.URL,deviceID:"test",deviceToken:strings.Repeat("x",32),httpClient:server.Client()}
   err:=replayCanonicalPendingReports(dir,transport)
   if !seen{t.Fatal("request was not sent")}
   if tc.removed&&err!=nil{t.Fatal(err)}
   if !tc.removed&&err==nil{t.Fatal("negative acknowledgement accepted")}
   _,statErr:=os.Stat(file)
   if (statErr==nil)==tc.removed{t.Fatalf("outbox preservation mismatch")}
  })
 }
}
func TestCanonicalOutboxRejectsSymlinks(t *testing.T){
 dir:=t.TempDir()
 outbox:=filepath.Join(dir,"outbox")
 if err:=os.Mkdir(outbox,0700);err!=nil{t.Fatal(err)}
 target:=filepath.Join(dir,"target.json")
 if err:=os.WriteFile(target,[]byte("{}"),0600);err!=nil{t.Fatal(err)}
 if err:=os.Symlink(target,filepath.Join(outbox,"link.json"));err!=nil{t.Skipf("symlink unsupported: %v",err)}
 if err:=replayCanonicalPendingReports(dir,canonicalDeviceTransport{});err==nil{t.Fatal("symlink followed")}
 if _,err:=os.Stat(target);err!=nil{t.Fatal("target was altered")}
}
