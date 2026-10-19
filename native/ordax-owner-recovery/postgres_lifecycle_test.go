package main

import (
 "encoding/json"
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
