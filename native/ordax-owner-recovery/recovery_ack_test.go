package main

import (
 "net/http"
 "net/http/httptest"
 "os"
 "path/filepath"
 "testing"
)

func TestPendingReportRequiresPositiveAcknowledgement(t *testing.T) {
 for _, tc := range []struct{name string; status int; body string; removed bool}{
  {"explicit success",200,`{"ok":true,"replayed":true}`,true},
  {"negative acknowledgement",200,`{"ok":false,"error":"terminal_report_conflict"}`,false},
  {"empty response",200,"",false},
  {"malformed response",200,"not json",false},
  {"server failure",503,`{"ok":false}`,false},
 } {
  t.Run(tc.name,func(t *testing.T){
   server:=httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter,r *http.Request){
    if r.URL.Path!="/v3/device/recover-report" {t.Errorf("unexpected path %s",r.URL.Path)}
    w.Header().Set("Content-Type","application/json")
    w.WriteHeader(tc.status)
    _,_ = w.Write([]byte(tc.body))
   }))
   defer server.Close()
   state:=t.TempDir()
   outbox:=filepath.Join(state,"outbox")
   if err:=os.Mkdir(outbox,0700);err!=nil{t.Fatal(err)}
   report:=filepath.Join(outbox,"test.json")
   if err:=os.WriteFile(report,[]byte(`{"job_id":"test"}`),0600);err!=nil{t.Fatal(err)}
   c:=&client{cfg:config{StateDir:state,ControlPlane:server.URL,DeviceID:"test",DeviceToken:"test"},http:server.Client()}
   err:=c.recoverPendingReports()
   if tc.removed && err!=nil {t.Fatalf("unexpected error: %v",err)}
   if !tc.removed && err==nil {t.Fatal("missing rejection")}
   _,statErr:=os.Stat(report)
   exists:=statErr==nil
   if exists==tc.removed {t.Fatalf("outbox existence=%v; removed expected=%v",exists,tc.removed)}
  })
 }
}
