package main

import (
 "net/http"
 "net/http/httptest"
 "strings"
 "testing"
)

func TestCanonicalClaimEmptyAndMissingJob(t *testing.T) {
 for _,tc:=range []struct{name,body string;valid bool}{
  {"empty queue",`{"ok":true,"job":null}`,true},
  {"missing job",`{"ok":true}`,false},
  {"refused",`{"ok":false,"job":null}`,false},
  {"malformed job",`{"ok":true,"job":{"job_id":"invalid"}}`,false},
 } {
  t.Run(tc.name,func(t *testing.T){
   server:=httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter,r *http.Request){
    if r.URL.Path!="/v3/device/transport/claim"{t.Errorf("unexpected path %s",r.URL.Path)}
    _,_=w.Write([]byte(tc.body))
   }))
   defer server.Close()
   transport:=canonicalDeviceTransport{baseURL:server.URL,deviceID:"test",deviceToken:strings.Repeat("x",32),httpClient:server.Client()}
   job,err:=claimCanonicalOwnerJob(transport,"6c212d20-c9c4-448d-a933-8d0b9345f88f","8d6d3001-e57a-402a-9f63-eb5c2b3f93fb")
   if tc.valid {if err!=nil||job!=nil {t.Fatalf("unexpected job=%v err=%v",job,err)}} else if err==nil {t.Fatal("invalid claim accepted")}
  })
 }
}
