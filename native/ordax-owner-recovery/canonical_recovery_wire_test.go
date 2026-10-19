package main

import (
 "crypto/sha256"
 "encoding/base64"
 "encoding/hex"
 "encoding/json"
 "testing"
)

func TestCanonicalRecoveryWire(t *testing.T) {
 result:=map[string]any{"ok":true,"summary":"completed","data":map[string]any{}}
 data,_:=json.Marshal(result)
 sum:=sha256.Sum256(data)
 report:=map[string]any{"result":result,"result_sha256":hex.EncodeToString(sum[:]),"job_id":"job-id","exit_code":0}
 wire,err:=canonicalRecoveryWire(report)
 if err!=nil {t.Fatal(err)}
 if _,ok:=wire["result"];ok {t.Fatal("duplicate raw result")}
 if _,ok:=wire["exit_code"];ok {t.Fatal("legacy exit code leaked")}
 raw,ok:=wire["result_canonical_b64"].(string);if !ok {t.Fatal("canonical payload missing")}
 decoded,err:=base64.StdEncoding.DecodeString(raw);if err!=nil {t.Fatal(err)}
 if string(decoded)!=string(data) {t.Fatalf("canonical payload changed")}
 if _,ok:=report["result"];!ok {t.Fatal("outbox report mutated")}
 report["result_sha256"]="bad"
 if _,err:=canonicalRecoveryWire(report);err==nil {t.Fatal("tampered digest accepted")}
 delete(report,"result")
 if _,err:=canonicalRecoveryWire(report);err==nil {t.Fatal("missing result accepted")}
}
