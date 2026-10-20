package main

import (
 "encoding/json"
 "errors"
 "fmt"
 "os"
 "path/filepath"
 "strings"
)

// replayCanonicalPendingReports is used exclusively by the PostgreSQL
// execution lifecycle. The legacy WebSocket run loop must never call this:
// crossing authorities would allow a D1 job to be reported to PostgreSQL.
func replayCanonicalPendingReports(stateDir string, transport canonicalDeviceTransport) error {
 dir:=filepath.Join(stateDir,"postgres-outbox")
 entries,err:=os.ReadDir(dir)
 if errors.Is(err,os.ErrNotExist){return nil}
 if err!=nil{return err}
 for _,entry:=range entries {
  if entry.IsDir()||!strings.HasSuffix(entry.Name(),".json"){continue}
  path:=filepath.Join(dir,entry.Name())
  // Do not follow outbox symlinks to arbitrary files.
  info,err:=os.Lstat(path)
  if err!=nil{return err}
  if !info.Mode().IsRegular(){return fmt.Errorf("pending report is not a regular file")}
  if info.Size()>640*1024{return errors.New("pending report exceeds recovery bound")}
  data,err:=os.ReadFile(path)
  if err!=nil{return err}
  var report map[string]any
  if err=json.Unmarshal(data,&report);err!=nil||report==nil{
   return errors.New("pending report is invalid")
  }
  wire,err:=canonicalRecoveryWire(report)
  if err!=nil{return err}
  if _,err=transport.post("recover-report",wire);err!=nil{return err}
  if err=os.Remove(path);err!=nil{return err}
 }
 return nil
}
