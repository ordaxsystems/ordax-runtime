package main

import (
 "bytes"
 "encoding/json"
 "errors"
 "fmt"
 "io"
 "net/http"
 "net/url"
 "strings"
)

// canonicalDeviceTransport sends scoped native requests to the PostgreSQL
// authority. There is no D1 fallback. The caller must first switch the
// entire claim/start/lease/report lifecycle to this protocol.
type canonicalDeviceTransport struct {
 baseURL string
 deviceID string
 deviceToken string
 httpClient *http.Client
}

func (t canonicalDeviceTransport) post(kind string, payload map[string]any) (map[string]any, error) {
 switch kind {
 case "heartbeat","claim","start","lease","progress","report","recover-report":
 default: return nil, errors.New("canonical device transport operation not permitted")
 }
 parsed, err := url.Parse(t.baseURL)
 if err != nil || parsed.Scheme != "https" || parsed.Host == "" || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" || parsed.Path != "" && parsed.Path != "/" {
  return nil, errors.New("canonical device transport requires HTTPS origin")
 }
 if t.deviceID == "" || len(t.deviceToken) < 32 || t.httpClient == nil {
  return nil, errors.New("canonical device transport identity missing")
 }
 body,err:=json.Marshal(payload)
 if err!=nil{return nil,err}
 if len(body)>640*1024{return nil,errors.New("canonical device transport payload too large")}
 target:=strings.TrimRight(t.baseURL,"/")+"/v3/device/transport/"+kind
 request,err:=http.NewRequest(http.MethodPost,target,bytes.NewReader(body))
 if err!=nil{return nil,err}
 request.Header.Set("Content-Type","application/json")
 request.Header.Set("X-Ordax-Device-Id",t.deviceID)
 request.Header.Set("X-Ordax-Device-Token",t.deviceToken)
 // Never forward device bearer material across redirects, including redirects
 // emitted by the same origin after a proxy or routing change.
 client:=*t.httpClient
 client.CheckRedirect=func(*http.Request,[]*http.Request) error {return http.ErrUseLastResponse}
 response,err:=client.Do(request)
 if err!=nil{return nil,err}
 defer response.Body.Close()
 // Claimed jobs carry a bounded canonical payload; other responses are short.
 responseLimit:=int64(8192)
 if kind=="claim" {responseLimit=128*1024}
 var ack map[string]any
 if err:=json.NewDecoder(io.LimitReader(response.Body,responseLimit)).Decode(&ack);err!=nil{
  return nil,errors.New("canonical device transport acknowledgement invalid")
 }
 if response.StatusCode < 200 || response.StatusCode >= 300 || ack["ok"] != true {
  return nil,fmt.Errorf("canonical device transport rejected %s (HTTP %d)",kind,response.StatusCode)
 }
 return ack,nil
}
