package main

import (
 "net/http"
 "net/http/httptest"
 "fmt"
 "strings"
 "testing"
)

func TestCanonicalPostgresTransportFailClosed(t *testing.T) {
 for _,tc:=range []struct{name string;status int;body string;success bool}{
  {"positive acknowledged",200,`{"ok":true,"job":null}`,true},
  {"negative 200",200,`{"ok":false}`,false},
  {"empty 200",200,"",false},
  {"html 200",200,"<html/>",false},
  {"unavailable",503,`{"ok":false}`,false},
 } {
  t.Run(tc.name,func(t *testing.T){
   server:=httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter,r *http.Request){
    if r.URL.Path!="/v3/device/transport/claim" || r.Method!="POST" {t.Errorf("unexpected route %s",r.URL.Path)}
    if r.Header.Get("X-Ordax-Device-Token")!=strings.Repeat("x",32) {t.Error("device token not forwarded")}
    w.WriteHeader(tc.status)
    _,_=w.Write([]byte(tc.body))
   }))
   defer server.Close()
   client:=canonicalDeviceTransport{baseURL:server.URL,deviceID:"device-test",deviceToken:strings.Repeat("x",32),httpClient:server.Client()}
   _,err:=client.post("claim",map[string]any{"agent_instance_id":"test"})
   if (err==nil)!=tc.success {t.Fatalf("err=%v success=%v",err,tc.success)}
  })
 }
}

func TestCanonicalPostgresTransportRejectsUnsafeTargets(t *testing.T) {
 cases:=[]string{"http://127.0.0.1","https://example.com?query=token","https://u:p@example.com","https://example.com/legacy"}
 for _,origin:=range cases{
  t.Run(origin,func(t *testing.T){
   client:=canonicalDeviceTransport{baseURL:origin,deviceID:"test",deviceToken:strings.Repeat("x",32),httpClient:http.DefaultClient}
   if _,err:=client.post("claim",map[string]any{});err==nil {t.Fatal("unsafe origin accepted")}
  })
 }
 client:=canonicalDeviceTransport{baseURL:"https://example.com",deviceID:"test",deviceToken:strings.Repeat("x",32),httpClient:http.DefaultClient}
 if _,err:=client.post("legacy-job",map[string]any{});err==nil {t.Fatal("unknown operation accepted")}
}

func TestClaimAcceptsBoundedCanonicalJobEnvelope(t *testing.T) {
 server:=httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter,r *http.Request){
  _,_=fmt.Fprintf(w,`{"ok":true,"job":{"payload":"%s"}}`,strings.Repeat("a",32*1024))
 }))
 defer server.Close()
 client:=canonicalDeviceTransport{baseURL:server.URL,deviceID:"device-test",deviceToken:strings.Repeat("x",32),httpClient:server.Client()}
 ack,err:=client.post("claim",map[string]any{})
 if err!=nil {t.Fatal(err)}
 if ack["job"]==nil {t.Fatal("job was discarded")}
}
