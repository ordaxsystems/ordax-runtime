package main

import (
 "crypto/sha256"
 "encoding/base64"
 "encoding/hex"
 "encoding/json"
 "errors"
 "strings"
)

// canonicalRecoveryWire encodes an already persisted outbox report for the
// PostgreSQL transport. It does not change the legacy recovery endpoint.
// Cutover must be atomic with the native agent's claim/start/lease protocol.
func canonicalRecoveryWire(report map[string]any) (map[string]any, error) {
 raw, ok := report["result"].(map[string]any)
 if !ok || raw == nil { return nil, errors.New("canonical recovery result missing") }
 encoded, err := json.Marshal(raw)
 if err != nil { return nil, err }
 digest := sha256.Sum256(encoded)
 expected, ok := report["result_sha256"].(string)
 if !ok || !strings.EqualFold(expected, hex.EncodeToString(digest[:])) {
  return nil, errors.New("canonical recovery result digest mismatch")
 }
 wire := make(map[string]any,len(report)+1)
 for key,value := range report {
  if key != "result" && key != "exit_code" { wire[key]=value }
 }
 wire["result_canonical_b64"]=base64.StdEncoding.EncodeToString(encoded)
 return wire,nil
}
